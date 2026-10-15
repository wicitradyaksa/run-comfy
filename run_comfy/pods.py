"""RunPod pod lifecycle: create with GPU fallback, terminate, sweep orphans."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .config import RunPodCfg

log = logging.getLogger("run_comfy")


class PodManager:
    def __init__(self, cfg: RunPodCfg, api_key: str, registry: Path, require_image: bool = True):
        import runpod

        if not api_key:
            raise SystemExit("RUNPOD_API_KEY is not set (export it or put it in .env)")
        if not (cfg.image or cfg.template_id) and require_image:
            raise SystemExit("config.toml: set [runpod].image or [runpod].template_id")
        runpod.api_key = api_key
        self.rp = runpod
        self.cfg = cfg
        self.registry = registry  # pod ids we own, survives kill -9 for `run_comfy cleanup`
        self.live: dict[str, dict] = {}

    def _save(self):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps(sorted(self.live)))

    async def create(self, idx: int, **overrides) -> tuple[str, float]:
        """Returns (pod_id, $/hr). Walks gpu_types until one has capacity. `overrides` patch create_pod kwargs."""
        c = self.cfg
        last = None
        for gpu in overrides.pop("gpu_types", None) or c.gpu_types:
            kw = dict(
                name=f"{c.name_prefix}-{idx}", image_name=c.image, gpu_type_id=gpu,
                cloud_type=c.cloud_type, gpu_count=1, container_disk_in_gb=c.container_disk_gb,
                volume_in_gb=c.volume_gb, ports=f"{c.comfy_port}/http,22/tcp",
                env=c.env or None, support_public_ip=True,
            )
            if c.template_id:
                kw["template_id"] = c.template_id
            if c.docker_args:
                kw["docker_args"] = c.docker_args
            if c.network_volume_id:
                kw["network_volume_id"] = c.network_volume_id
            if c.data_center_id:
                kw["data_center_id"] = c.data_center_id
            kw.update(overrides)
            if "image_name" in overrides and "template_id" not in overrides:
                kw.pop("template_id", None)  # an explicit image replaces the configured template
            try:
                pod = await asyncio.to_thread(self.rp.create_pod, **kw)
            except Exception as e:  # usually "no instances available"
                last = e
                log.warning("pod %d: %s unavailable (%s)", idx, gpu, str(e)[:160])
                continue
            pid = pod["id"]
            rate = float(pod.get("costPerHr") or 0) or await self._rate(pid)
            self.live[pid] = {"gpu": gpu, "rate": rate}
            self._save()
            log.info("pod %d: created %s on %s (~$%.2f/hr)", idx, pid, gpu, rate)
            return pid, rate
        raise RuntimeError(f"no GPU from {c.gpu_types} available: {last}")

    async def _rate(self, pid: str) -> float:
        try:
            p = await asyncio.to_thread(self.rp.get_pod, pid)
            return float((p or {}).get("costPerHr") or 0) or self.cfg.hourly_rate_estimate_usd
        except Exception:
            return self.cfg.hourly_rate_estimate_usd

    async def status(self, pid: str) -> str:
        try:
            p = await asyncio.to_thread(self.rp.get_pod, pid)
            return (p or {}).get("desiredStatus", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def url(self, pid: str) -> str:
        return f"https://{pid}-{self.cfg.comfy_port}.proxy.runpod.net"

    async def terminate(self, pid: str):
        for attempt in range(5):
            try:
                await asyncio.to_thread(self.rp.terminate_pod, pid)
                log.info("pod %s terminated", pid)
                break
            except Exception as e:
                log.warning("terminate %s failed (%s), retrying", pid, e)
                await asyncio.sleep(2 ** attempt)
        else:
            log.error("!! could not terminate %s - run `run_comfy cleanup` or kill it in the console", pid)
            return
        self.live.pop(pid, None)
        self._save()

    def terminate_all_sync(self):
        """Last-resort, used from atexit when the event loop is gone."""
        for pid in list(self.live):
            try:
                self.rp.terminate_pod(pid)
                self.live.pop(pid, None)
            except Exception:
                pass
        self._save()

    def sweep(self) -> list[str]:
        """Terminate every pod named with our prefix or listed in the registry."""
        known = set(json.loads(self.registry.read_text())) if self.registry.exists() else set()
        killed = []
        for p in self.rp.get_pods() or []:
            if p["id"] in known or p.get("name", "").startswith(self.cfg.name_prefix + "-"):
                self.rp.terminate_pod(p["id"])
                killed.append(p["id"])
        self.live.clear()
        self._save()
        return killed
