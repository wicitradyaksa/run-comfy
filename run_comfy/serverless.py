"""Serverless backend: RunPod Serverless endpoint running worker-comfyui.

No pods to manage: the endpoint scales workers up for the batch and back to zero.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path

import httpx

from .config import Config, ServerlessCfg
from .jobs import Job, job_values, render
from .runner import BaseRunner

log = logging.getLogger("run_comfy")
API = "https://api.runpod.ai/v2"
MAX_PAYLOAD = 9_500_000  # /run rejects bodies over 10 MB
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class ServerlessError(Exception):
    """Retryable job failure."""


class FatalJobError(Exception):
    """Retrying can't help (e.g. payload too large)."""


class EndpointClient:
    def __init__(self, endpoint_id: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=f"{API}/{endpoint_id}", timeout=60, transport=transport,
                                      headers={"Authorization": f"Bearer {api_key}"})
        self.files = httpx.AsyncClient(timeout=600, transport=transport, follow_redirects=True)

    async def close(self):
        await self.http.aclose()
        await self.files.aclose()

    async def _req(self, method: str, url: str, **kw) -> dict:
        for attempt in range(5):
            try:
                r = await self.http.request(method, url, **kw)
            except httpx.TransportError as e:
                err = repr(e)
            else:
                if r.status_code < 300:
                    return r.json()
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise ServerlessError(f"{method} {url}: HTTP {r.status_code} {r.text[:500]}")
                err = f"HTTP {r.status_code}"
            await asyncio.sleep(min(2 ** attempt, 15))
        raise ServerlessError(f"{method} {url}: {err}")

    async def submit(self, payload: dict) -> str:
        return (await self._req("POST", "/run", json=payload))["id"]

    async def status(self, rid: str) -> dict:
        return await self._req("GET", f"/status/{rid}")

    async def cancel(self, rid: str):
        try:
            await self._req("POST", f"/cancel/{rid}")
        except ServerlessError:
            pass

    async def purge_queue(self) -> dict:
        return await self._req("POST", "/purge-queue")

    async def fetch(self, url: str, dest: Path) -> Path:
        tmp = dest.with_suffix(dest.suffix + ".part")
        async with self.files.stream("GET", url) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
        tmp.replace(dest)
        return dest


def output_items(output) -> list[dict]:
    """Normalise worker-comfyui output to [{filename, type: base64|s3_url, data}]."""
    if not isinstance(output, dict):
        raise ServerlessError(f"unexpected output: {str(output)[:300]}")
    items = []
    for key in ("images", "videos", "gifs", "files"):
        for it in output.get(key) or []:
            if isinstance(it, dict) and it.get("data"):
                items.append({"filename": it.get("filename", f"output_{len(items)}.png"),
                              "type": it.get("type", "base64"), "data": it["data"]})
    msg = output.get("message")  # worker-comfyui < 5
    if not items and isinstance(msg, str) and output.get("status") == "success":
        is_url = msg.startswith("http")
        name = Path(msg.split("?")[0]).name if is_url else "output.png"
        items.append({"filename": name, "type": "s3_url" if is_url else "base64", "data": msg})
    if not items:
        errs = output.get("errors") or output.get("error") or output
        raise ServerlessError(f"no output files: {str(errs)[:800]}")
    return items


class ServerlessRunner(BaseRunner):
    def __init__(self, cfg: Config, client: EndpointClient | None = None):
        super().__init__(cfg)
        self.sc: ServerlessCfg = cfg.serverless
        if client is None:
            if not cfg.api_key:
                raise SystemExit("RUNPOD_API_KEY is not set (export it or put it in .env)")
            if not self.sc.endpoint_id:
                raise SystemExit("[serverless].endpoint_id is empty - run `run_comfy serverless-setup` first")
            client = EndpointClient(self.sc.endpoint_id, cfg.api_key)
        self.client = client
        self.billed_s = 0.0                    # finished worker-seconds
        self.running: dict[str, float] = {}    # request id -> when we first saw it IN_PROGRESS

    def spend(self) -> float:
        now = time.monotonic()
        live = sum(now - t for t in self.running.values())
        return (self.billed_s + live) * self.sc.cost_per_sec_usd

    async def run(self) -> dict:
        pending = self._pending()
        if pending:
            sem = asyncio.Semaphore(self.sc.max_concurrent)
            main = asyncio.ensure_future(asyncio.gather(*(self._job(j, sem) for j in pending)))
            dog = asyncio.create_task(self._watchdog(main))
            try:
                await main
            except asyncio.CancelledError:
                log.error("run cancelled - in-flight requests cancelled on the endpoint")
            finally:
                dog.cancel()
                await self.client.close()
        return self._report()

    async def _job(self, job: Job, sem: asyncio.Semaphore):
        async with sem:
            while True:
                try:
                    return await self._attempt(job)
                except FatalJobError as e:
                    job.attempts += 1
                    return self._failed(job, e)
                except (ServerlessError, httpx.HTTPError, ValueError) as e:
                    job.attempts += 1
                    if job.attempts > self.pc.max_retries:
                        return self._failed(job, e)
                    log.warning("job %s: %s - retry %d/%d", job.id, e, job.attempts, self.pc.max_retries)

    def _payload(self, job: Job) -> dict:
        images, names = [], {}
        for col, path in job.uploads.items():
            name = re.sub(r"[^\w.-]", "_", f"{job.id}_{col}{path.suffix}")
            images.append({"name": name, "image": base64.b64encode(path.read_bytes()).decode()})
            names[col] = name
        inp = {"workflow": render(self.workflow, job_values(job, names))}
        if images:
            inp["images"] = images
        payload = {"input": inp, "policy": {"executionTimeout": self.pc.job_timeout_s * 1000}}
        size = len(json.dumps(payload))
        if size > MAX_PAYLOAD:
            raise FatalJobError(f"request is {size / 1e6:.1f} MB (limit 10 MB) - use smaller input images")
        return payload

    async def _attempt(self, job: Job):
        t = time.monotonic()
        rid = await self.client.submit(self._payload(job))
        deadline = t + self.pc.job_timeout_s + 3600  # extra hour for queueing / cold starts
        try:
            while True:
                st = await self.client.status(rid)
                status = st.get("status")
                if status == "IN_PROGRESS":
                    self.running.setdefault(rid, time.monotonic())
                if status in TERMINAL:
                    break
                if time.monotonic() > deadline:
                    await self.client.cancel(rid)
                    raise ServerlessError(f"request {rid} still {status} after {deadline - t:.0f}s")
                await asyncio.sleep(self.sc.poll_interval_s)
        except asyncio.CancelledError:
            await asyncio.shield(self.client.cancel(rid))
            raise
        finally:
            started = self.running.pop(rid, None)
        exec_s = (st.get("executionTime") or 0) / 1000 or (time.monotonic() - started if started else 0)
        self.billed_s += exec_s
        if status != "COMPLETED":
            raise ServerlessError(f"{status}: {str(st.get('error') or st.get('output'))[:800]}")
        items = output_items(st.get("output"))
        saved = []
        for i, it in enumerate(items):
            dest = self._output_path(job, i, len(items), it["filename"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            if it["type"] == "s3_url" or str(it["data"]).startswith("http"):
                await self.client.fetch(it["data"], dest)
            else:
                data = it["data"].split(",", 1)[1] if it["data"].startswith("data:") else it["data"]
                dest.write_bytes(base64.b64decode(data))
            saved.append(dest)
        self._done(job, saved, time.monotonic() - t, f"serverless ({exec_s:.0f}s GPU)")


def setup_endpoint(sc: ServerlessCfg, api_key: str) -> str:
    """Create a serverless template + endpoint for worker-comfyui. Returns the endpoint id."""
    import runpod

    if not api_key:
        raise SystemExit("RUNPOD_API_KEY is not set")
    if not sc.image:
        raise SystemExit("set [serverless].image, e.g. runpod/worker-comfyui:<version>-base "
                         "(see github.com/runpod-workers/worker-comfyui/releases)")
    runpod.api_key = api_key
    for ep in runpod.get_endpoints() or []:
        if ep.get("name", "").split(" ")[0] == sc.name:
            log.info("endpoint %s already exists: %s", sc.name, ep["id"])
            return ep["id"]
    tpl = runpod.create_template(name=f"{sc.name}-tpl-{int(time.time())}", image_name=sc.image,
                                 is_serverless=True, container_disk_in_gb=sc.container_disk_gb,
                                 env=sc.env or None)
    kw = dict(name=sc.name, template_id=tpl["id"], gpu_ids=sc.gpu_ids, workers_min=0,
              workers_max=sc.workers_max, idle_timeout=sc.idle_timeout_s, flashboot=True)
    if sc.network_volume_id:
        kw["network_volume_id"] = sc.network_volume_id
    ep = runpod.create_endpoint(**kw)
    return ep["id"]
