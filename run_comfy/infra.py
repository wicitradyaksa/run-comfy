"""Automatic RunPod infrastructure: network volume, models on it, worker image, serverless endpoint.

`Infra.ensure()` is idempotent. It creates only what is missing and records the result in config.toml
and data/, so it can run before every paid batch:

    1. network volume   - created via RunPod REST, sized from the manifests (+ headroom)
    2. models           - only files not yet on the volume (per the ledger) are downloaded by a
                          throwaway seed pod that verifies sha256; gated HF models are checked first
    3. image            - stock worker-comfyui when the workflows need only core nodes, otherwise the
                          custom-node image built by .github/workflows/build-worker.yml (no local Docker)
    4. endpoint / pods  - serverless endpoint created or switched to the right image; pods get a
                          default image + start command that reads models from the volume

`Infra.plan()` answers "what would that do and cost?" offline, for the estimate panel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .config import Config, set_value
from .provision import collect, image_tag, needs_custom_nodes, seed_volume
from .registry import Workflow

log = logging.getLogger("run_comfy")

REST = "https://rest.runpod.io/v1"
VOLUME_USD_PER_GB_MONTH = 0.07    # RunPod network volume list price (first TB)
DOWNLOAD_MB_S = 150               # conservative datacenter download speed for estimates
SEED_BOOT_MIN = 3
MIN_VOLUME_GB = 50
# Pods run the same worker image as serverless, but start ComfyUI's own server so the pod pool can
# drive it over HTTP. The worker image's extra_model_paths.yaml points ComfyUI at /runpod-volume/models.
POD_COMFY_CMD = ("bash -c 'cd /comfyui && python -u main.py --listen 0.0.0.0 --port 8188 "
                 "--extra-model-paths-config /comfyui/extra_model_paths.yaml'")


class InfraError(Exception):
    pass


# ---------------------------------------------------------------- RunPod REST
class RunPodRest:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=REST, timeout=60, transport=transport,
                                      headers={"Authorization": f"Bearer {api_key}"})

    async def close(self):
        await self.http.aclose()

    async def create_volume(self, name: str, size_gb: int, data_center: str) -> dict:
        r = await self.http.post("/networkvolumes", json={"name": name, "size": size_gb,
                                                          "dataCenterId": data_center})
        if r.status_code >= 300:
            raise InfraError(f"creating network volume failed: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    async def get_volume(self, volume_id: str) -> dict | None:
        r = await self.http.get(f"/networkvolumes/{volume_id}")
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise InfraError(f"reading network volume failed: HTTP {r.status_code} {r.text[:300]}")
        return r.json()


# ---------------------------------------------------------------- ledger of what's on the volume
class Ledger:
    def __init__(self, data_dir: Path, volume_id: str):
        self.path = Path(data_dir) / f"volume_{volume_id or 'none'}.json"
        self.files: dict[str, dict] = json.loads(self.path.read_text())["files"] if self.path.exists() else {}

    def missing(self, models: list[dict]) -> list[dict]:
        out = []
        for m in models:
            have = self.files.get(m["path"])
            if not have or (m.get("sha256") and have.get("sha256") != m["sha256"]):
                out.append(m)
        return out

    def record(self, status: dict, models: list[dict]):
        urls = {m["path"]: m["url"] for m in models}
        for p, info in (status.get("files") or {}).items():
            self.files[p] = {**info, "url": urls.get(p, "")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"files": self.files}, indent=1))


# ---------------------------------------------------------------- helpers
def size_gb(m: dict) -> float:
    return float(m.get("size_gb") or 0)


def hf_page(url: str) -> str:
    parts = url.split("/")
    return "/".join(parts[:5]) if "huggingface.co" in url else url


async def probe_models(models: list[dict], hf_token: str | None,
                       transport: httpx.AsyncBaseTransport | None = None) -> dict[str, float]:
    """HEAD every model URL: real sizes (GB) where available; InfraError for gated models we can't read."""
    sizes: dict[str, float] = {}
    blocked = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": "run-comfy-setup"}) as c:
        async def one(m):
            headers = {}
            if "huggingface.co" in m["url"] and hf_token:
                headers["Authorization"] = f"Bearer {hf_token}"
            try:
                r = await c.head(m["url"], headers=headers)
            except httpx.HTTPError as e:
                log.warning("could not reach %s: %s", m["url"], e)
                return
            if r.status_code in (401, 403):
                blocked.append(m)
            elif r.status_code < 300:
                n = r.headers.get("x-linked-size") or r.headers.get("content-length")
                if n and n.isdigit() and int(n) > 0:
                    sizes[m["path"]] = int(n) / 1e9
        await asyncio.gather(*(one(m) for m in models))
    if blocked:
        pages = sorted({hf_page(m["url"]) for m in blocked})
        need = "set HF_TOKEN in .env and " if not hf_token else ""
        raise InfraError(f"gated models can't be downloaded yet: {need}accept the licence at "
                         + ", ".join(pages))
    return sizes


async def image_available(image: str, transport: httpx.AsyncBaseTransport | None = None) -> bool | None:
    """True/False for public ghcr.io images; None when we can't tell (private, other registries)."""
    if not image.startswith("ghcr.io/") or ":" not in image.split("/")[-1]:
        return None
    name, tag = image[len("ghcr.io/"):].rsplit(":", 1)
    async with httpx.AsyncClient(timeout=20, transport=transport) as c:
        try:
            t = await c.get("https://ghcr.io/token", params={"scope": f"repository:{name}:pull"})
            if t.status_code != 200:
                return None
            r = await c.head(f"https://ghcr.io/v2/{name}/manifests/{tag}", headers={
                "Authorization": f"Bearer {t.json()['token']}",
                "Accept": "application/vnd.oci.image.index.v1+json, "
                          "application/vnd.docker.distribution.manifest.list.v2+json, "
                          "application/vnd.docker.distribution.manifest.v2+json"})
        except httpx.HTTPError:
            return None
    return True if r.status_code == 200 else False if r.status_code == 404 else None


# ---------------------------------------------------------------- endpoint operations (SDK)
class SdkEndpointOps:
    def __init__(self, api_key: str):
        import runpod

        runpod.api_key = api_key
        self.rp = runpod

    async def create(self, cfg: Config, image: str, volume_id: str, registry_auth_id: str | None) -> str:
        from .serverless import setup_endpoint

        return await asyncio.to_thread(setup_endpoint, cfg.serverless, cfg.api_key, image=image,
                                       network_volume_id=volume_id, registry_auth_id=registry_auth_id)

    async def set_image(self, cfg: Config, endpoint_id: str, image: str, registry_auth_id: str | None):
        sc = cfg.serverless
        tpl = await asyncio.to_thread(self.rp.create_template, name=f"{sc.name}-tpl-{int(time.time())}",
                                      image_name=image, is_serverless=True,
                                      container_disk_in_gb=sc.container_disk_gb, env=sc.env or None,
                                      registry_auth_id=registry_auth_id)
        await asyncio.to_thread(self.rp.update_endpoint_template, endpoint_id, tpl["id"])

    async def registry_auth(self, username: str, token: str) -> str:
        r = await asyncio.to_thread(self.rp.create_container_registry_auth, "run-comfy-ghcr", username, token)
        return r["id"]


# ---------------------------------------------------------------- the orchestrator
@dataclass
class Report:
    actions: list[str]
    plan: dict


class Infra:
    def __init__(self, cfg: Config, registry: dict[str, Workflow], data_dir: Path, *,
                 rest: RunPodRest | None = None, pods=None, endpoint_ops=None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 on_event: Callable[[dict], None] | None = None, seed_poll_s: float = 15):
        self.cfg, self.registry, self.data_dir = cfg, registry, Path(data_dir)
        self._rest, self._pods, self._ops = rest, pods, endpoint_ops
        self.transport = transport
        self.on_event = on_event or (lambda e: None)
        self.seed_poll_s = seed_poll_s
        self.state_path = self.data_dir / "infra.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    # -- lazy clients (so plan() works with no keys at all)
    def rest(self) -> RunPodRest:
        if self._rest is None:
            self._rest = RunPodRest(self.cfg.api_key, self.transport)
        return self._rest

    def pods(self):
        if self._pods is None:
            from .pods import PodManager
            self._pods = PodManager(self.cfg.runpod, self.cfg.api_key, self.data_dir / "pods.json",
                                    require_image=False)
        return self._pods

    def ops(self):
        if self._ops is None:
            self._ops = SdkEndpointOps(self.cfg.api_key)
        return self._ops

    def _emit(self, msg: str, **kw):
        log.info("setup: %s", msg)
        self.on_event({"type": "setup", "message": msg, **kw})

    def _save_state(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def _persist(self, section: str, key: str, value: str):
        if self.cfg.config_file:
            set_value(self.cfg.config_file, section, key, value)

    # -- what's needed
    def image_for(self, workflows: list[str] | None) -> tuple[str, bool]:
        sc = self.cfg.serverless
        if needs_custom_nodes(self.registry, workflows):
            repo = sc.custom_image_repo
            if not repo:
                return "", True
            return f"{repo}:{image_tag(self.registry, sc.base_image)}", True
        return sc.image or sc.base_image, False

    def plan(self, workflows: list[str] | None = None, backend: str = "serverless") -> dict:
        rp = self.cfg.runpod
        models, nodes = collect(self.registry, workflows)
        all_models, _ = collect(self.registry)
        creates_volume = not rp.network_volume_id
        ledger = Ledger(self.data_dir, rp.network_volume_id)
        missing = models if creates_volume else ledger.missing(models)
        dl_gb = sum(size_gb(m) for m in missing)
        minutes = (dl_gb * 1000 / DOWNLOAD_MB_S / 60 + SEED_BOOT_MIN) if missing else 0
        volume_gb = max(MIN_VOLUME_GB, math.ceil(sum(size_gb(m) for m in all_models)
                                                 * self.cfg.factory.volume_headroom))
        image, custom = self.image_for(workflows)
        warnings = []
        if custom and not image:
            warnings.append("workflows need custom nodes: set [serverless].custom_image_repo "
                            "(e.g. ghcr.io/<you>/run-comfy-worker) and let the build-worker GitHub Action build it")
        gated = [m for m in missing if m.get("gated")]
        if gated and not os.environ.get("HF_TOKEN"):
            warnings.append("gated models (" + ", ".join(sorted({hf_page(m['url']) for m in gated}))
                            + ") need HF_TOKEN and an accepted licence")
        if not self.cfg.api_key:
            warnings.append("RUNPOD_API_KEY is not set")
        return {
            "backend": backend,
            "creates_volume": creates_volume,
            "volume_id": rp.network_volume_id or None,
            "volume_gb": volume_gb if creates_volume else None,
            "data_center": rp.data_center_id or self.cfg.factory.default_data_center,
            "volume_monthly_usd": round((volume_gb if creates_volume else 0) * VOLUME_USD_PER_GB_MONTH, 2),
            "missing_models": [m["path"] for m in missing],
            "download_gb": round(dl_gb, 1),
            "est_minutes": round(minutes),
            "est_usd": round(minutes / 60 * rp.seed_hourly_rate_usd, 2),
            "image": image,
            "custom_nodes": [n["repo"] for n in nodes],
            "creates_endpoint": backend == "serverless" and not self.cfg.serverless.endpoint_id,
            "ready": not (creates_volume or missing) and (backend != "serverless" or bool(
                self.cfg.serverless.endpoint_id and self.state.get("endpoint_image") in (None, image))),
            "warnings": warnings,
        }

    # -- do it
    async def ensure(self, workflows: list[str] | None = None, backend: str = "serverless") -> Report:
        if not self.cfg.api_key:
            raise InfraError("RUNPOD_API_KEY is not set")
        actions: list[str] = []
        rp, sc = self.cfg.runpod, self.cfg.serverless
        models, _ = collect(self.registry, workflows)

        # 1. network volume
        if not rp.network_volume_id:
            all_models, _ = collect(self.registry)
            real = await probe_models(all_models, os.environ.get("HF_TOKEN"), self.transport)
            total = sum(real.get(m["path"], size_gb(m)) for m in all_models)
            size = max(MIN_VOLUME_GB, math.ceil(total * self.cfg.factory.volume_headroom))
            dc = rp.data_center_id or self.cfg.factory.default_data_center
            self._emit(f"creating a {size} GB network volume in {dc}")
            vol = await self.rest().create_volume(f"{rp.name_prefix}-models", size, dc)
            rp.network_volume_id, rp.data_center_id = vol["id"], dc
            sc.network_volume_id = vol["id"]
            for section, key, value in (("runpod", "network_volume_id", vol["id"]), ("runpod", "data_center_id", dc),
                                        ("serverless", "network_volume_id", vol["id"])):
                self._persist(section, key, value)
            actions.append(f"created network volume {vol['id']} ({size} GB, {dc})")
        elif self.state.get("volume_checked") != rp.network_volume_id:
            if await self.rest().get_volume(rp.network_volume_id) is None:
                raise InfraError(f"network volume {rp.network_volume_id} not found in your RunPod account")
            self.state["volume_checked"] = rp.network_volume_id
            self._save_state()
        if not sc.network_volume_id:
            sc.network_volume_id = rp.network_volume_id

        # 2. models
        ledger = Ledger(self.data_dir, rp.network_volume_id)
        missing = ledger.missing(models)
        if missing:
            await probe_models(missing, os.environ.get("HF_TOKEN"), self.transport)  # fail fast on gated
            gb = sum(size_gb(m) for m in missing)
            self._emit(f"downloading {len(missing)} model files (~{gb:.0f} GB) onto the volume")
            env = {k: os.environ[k] for k in ("HF_TOKEN", "CIVITAI_TOKEN") if os.environ.get(k)}
            status = await seed_volume(self.pods(), missing, env=env, poll_s=self.seed_poll_s,
                                       transport=self.transport)
            ledger.record(status, missing)
            if status.get("failed"):
                raise InfraError("some models failed to download: " + "; ".join(
                    f"{f['path']}: {f['error']}" for f in status["failed"]))
            actions.append(f"downloaded {len(status.get('done', []))} model files")

        # 3 + 4. image and compute
        image, custom = self.image_for(workflows)
        if custom and not image:
            raise InfraError("these workflows need custom nodes: set [serverless].custom_image_repo "
                             "(e.g. ghcr.io/<you>/run-comfy-worker) and push to GitHub to build it")
        if custom:
            ok = await image_available(image, self.transport)
            if ok is False:
                raise InfraError(f"image {image} isn't built yet - push to main (build-worker Action) "
                                 "or run that Action manually, then retry")
        auth_id = await self._registry_auth(image)
        if backend == "serverless":
            if not sc.endpoint_id:
                self._emit(f"creating serverless endpoint with {image}")
                sc.endpoint_id = await self.ops().create(self.cfg, image, rp.network_volume_id, auth_id)
                self._persist("serverless", "endpoint_id", sc.endpoint_id)
                actions.append(f"created serverless endpoint {sc.endpoint_id}")
            elif self.state.get("endpoint_image") not in (None, image):
                self._emit(f"switching endpoint to {image}")
                await self.ops().set_image(self.cfg, sc.endpoint_id, image, auth_id)
                actions.append(f"endpoint now runs {image}")
            self.state["endpoint_image"] = image
        elif backend == "pod" and not (rp.image or rp.template_id):
            rp.image, rp.docker_args = image, POD_COMFY_CMD
            self._persist("runpod", "image", image)
            self._persist("runpod", "docker_args", POD_COMFY_CMD.replace('"', "'"))
            actions.append(f"pods will run {image}")
        self._save_state()
        if not actions:
            actions.append("everything already in place")
        return Report(actions=actions, plan=self.plan(workflows, backend))

    async def _registry_auth(self, image: str) -> str | None:
        user, token = os.environ.get("GHCR_USER"), os.environ.get("GHCR_PULL_TOKEN")
        if not (image.startswith("ghcr.io/") and user and token):
            return None
        if not self.state.get("registry_auth_id"):
            self.state["registry_auth_id"] = await self.ops().registry_auth(user, token)
            self._save_state()
        return self.state["registry_auth_id"]
