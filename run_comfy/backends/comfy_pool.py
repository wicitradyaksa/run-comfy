"""ComfyUI backends: an existing server (url) or a self-healing pool of RunPod pods."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..comfy import ComfyClient, ComfyError, ComfyUnreachable, output_files
from ..registry import MEDIA, Workflow, bind, resolve_values
from .base import Backend, BackendDown, Progress, StageFailed

log = logging.getLogger("run_comfy")


@dataclass
class _Slot:
    client: ComfyClient
    pod_id: str | None
    sem: asyncio.Semaphore
    started: float = field(default_factory=time.monotonic)
    rate_hr: float = 0.0
    dead: bool = False
    busy: int = 0


async def run_on_comfy(client: ComfyClient, wf: Workflow, values: dict, out_dir: Path,
                       timeout_s: float, progress: Progress | None = None, poll_s: float = 2.0) -> list[Path]:
    """Upload media inputs, bind, submit, wait (with live progress), download outputs."""
    vals = resolve_values(wf, values)
    for name, spec in wf.inputs.items():
        if spec.type in MEDIA and name in vals:
            vals[name] = await client.upload(Path(vals[name]))
    graph = bind(wf, vals)
    prompt_id = await client.submit(graph)
    watcher = asyncio.create_task(client.watch_progress(prompt_id, progress)) if progress else None
    try:
        outputs = await client.wait(prompt_id, timeout_s, poll_s)
    finally:
        if watcher:
            watcher.cancel()
    files = output_files(outputs)
    if not files:
        raise ComfyError(f"{wf.name}: finished without output files (missing Save node?)")
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, f in enumerate(files):
        dest = out_dir / (f"{i:02d}_{Path(f['filename']).name}")
        saved.append(await client.download(f, dest))
    return saved


class ComfyPoolBackend(Backend):
    """`base_url` = use that server. Otherwise boot up to `pods` RunPod pods lazily and keep them healthy."""

    def __init__(self, *, base_url: str | None = None, pods=None, n_pods: int = 1, inflight: int = 2,
                 job_timeout_s: float = 1800, boot_timeout_s: float = 1200, max_restarts: int = 1,
                 client_factory: Callable[[str], ComfyClient] = ComfyClient, poll_s: float = 2.0,
                 boot_interval_s: float = 10.0):
        self.name = "url" if base_url else "pod"
        self.base_url, self.pods = base_url, pods
        self.n_pods, self.inflight = max(1, n_pods), max(1, inflight)
        self.job_timeout_s, self.boot_timeout_s = job_timeout_s, boot_timeout_s
        self.max_restarts, self.restarts = max_restarts, 0
        self.client_factory, self.poll_s, self.boot_interval_s = client_factory, poll_s, boot_interval_s
        self.slots: list[_Slot] = []
        self._lock = asyncio.Lock()
        self._booting: dict[int, asyncio.Task] = {}
        self.spent_closed = 0.0
        self.rate_per_s = 0.0

    # ---- lifecycle ----
    async def _boot(self, idx: int) -> _Slot:
        if self.base_url:
            c = self.client_factory(self.base_url)
            await c.wait_healthy(self.boot_timeout_s, self.boot_interval_s)
            return _Slot(c, None, asyncio.Semaphore(self.inflight))
        pid, rate = await self.pods.create(idx)
        c = self.client_factory(self.pods.url(pid))
        slot = _Slot(c, pid, asyncio.Semaphore(self.inflight), rate_hr=rate)
        try:
            async def tick():
                st = await self.pods.status(pid)
                if st in ("EXITED", "TERMINATED"):
                    raise BackendDown(f"pod {pid} went {st} while booting")
            await c.wait_healthy(self.boot_timeout_s, self.boot_interval_s, tick)
        except BaseException:
            await self._retire(slot)
            raise
        self.rate_per_s = rate / 3600 / self.inflight
        log.info("pod %s ready", pid)
        return slot

    async def _ensure(self):
        async with self._lock:
            live = [s for s in self.slots if not s.dead]
            want = self.n_pods - len(live)
            if want <= 0:
                return
            if self.slots and any(s.dead for s in self.slots):
                if self.restarts > self.max_restarts:
                    if not live:
                        raise BackendDown("all pods dead and out of restarts")
                    return
            results = await asyncio.gather(*(self._boot(len(self.slots) + i) for i in range(want)),
                                           return_exceptions=True)
            for r in results:
                if isinstance(r, _Slot):
                    self.slots.append(r)
                else:
                    log.error("pod boot failed: %s", r)
            if not any(not s.dead for s in self.slots):
                raise BackendDown(f"no ComfyUI available: {results[0]}")

    async def _retire(self, slot: _Slot):
        slot.dead = True
        await slot.client.close()
        if slot.pod_id and self.pods:
            await self.pods.terminate(slot.pod_id)
            self.spent_closed += (time.monotonic() - slot.started) / 3600 * slot.rate_hr
            slot.rate_hr = 0.0

    async def start(self):
        await self._ensure()

    async def close(self):
        for s in self.slots:
            if not s.dead:
                await asyncio.shield(self._retire(s))

    def spend(self) -> float:
        now = time.monotonic()
        return self.spent_closed + sum((now - s.started) / 3600 * s.rate_hr for s in self.slots if not s.dead)

    # ---- work ----
    async def run(self, wf: Workflow, values: dict, out_dir: Path, progress: Progress | None = None) -> list[Path]:
        await self._ensure()
        live = [s for s in self.slots if not s.dead]
        slot = min(live, key=lambda s: s.busy)
        slot.busy += 1
        try:
            async with slot.sem:
                return await run_on_comfy(slot.client, wf, values, out_dir, self.job_timeout_s, progress, self.poll_s)
        except ComfyUnreachable as e:
            if slot.pod_id and not slot.dead:
                log.warning("pod %s unreachable (%s) - replacing", slot.pod_id, e)
                self.restarts += 1
                await self._retire(slot)
            raise BackendDown(str(e)) from e
        except ComfyError as e:
            await slot.client.free_memory()
            raise StageFailed(str(e)) from e
        finally:
            slot.busy -= 1
