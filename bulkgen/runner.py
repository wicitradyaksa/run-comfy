"""The orchestrator: pods up -> drain queue -> download -> pods down. Always tears down."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from pathlib import Path
from typing import Callable

from .comfy import ComfyClient, ComfyError, ComfyUnreachable, output_files
from .config import Config
from .jobs import Job, State, job_values, load_jobs, render, validate
from .pods import PodManager

log = logging.getLogger("bulkgen")


class _StopBoot(Exception):
    pass


class Runner:
    def __init__(self, cfg: Config, pods: PodManager | None = None, base_url: str | None = None,
                 client_factory: Callable[[str], ComfyClient] = ComfyClient,
                 poll_interval: float = 3, boot_interval: float = 10):
        self.cfg, self.pc = cfg, cfg.pipeline
        self.pods, self.base_url = pods, base_url
        self.client_factory = client_factory
        self.poll_interval, self.boot_interval = poll_interval, boot_interval
        self.out = cfg.path(self.pc.output_dir)
        self.state = State(self.out / "state.jsonl")
        self.workflow = json.loads(cfg.path(self.pc.workflow).read_text())
        self.jobs = load_jobs(cfg.path(self.pc.jobs))
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.in_flight = 0
        self.clock: dict[str, tuple[float, float]] = {}  # pod -> (start, $/hr)
        self.spent_closed = 0.0
        self.t0 = time.monotonic()
        self.watchdog_interval = 5.0

    # ---------- money ----------
    def spend(self) -> float:
        now = time.monotonic()
        return self.spent_closed + sum((now - t) / 3600 * r for t, r in self.clock.values())

    def _close_clock(self, pid: str):
        if pid in self.clock:
            t, r = self.clock.pop(pid)
            self.spent_closed += (time.monotonic() - t) / 3600 * r

    async def _watchdog(self, main: asyncio.Task):
        while not main.done():
            await asyncio.sleep(self.watchdog_interval)
            mins = (time.monotonic() - self.t0) / 60
            if self.spend() > self.pc.max_spend_usd:
                log.error("budget hit ($%.2f > $%.2f) - stopping", self.spend(), self.pc.max_spend_usd)
                main.cancel()
            elif mins > self.pc.max_runtime_min:
                log.error("max runtime %d min hit - stopping", self.pc.max_runtime_min)
                main.cancel()

    # ---------- pods ----------
    def _work_left(self) -> bool:
        return not self.queue.empty() or self.in_flight > 0

    async def _bring_up(self, idx: int) -> tuple[ComfyClient, str | None]:
        if self.pods is None:
            client = self.client_factory(self.base_url)
            await client.wait_healthy(self.pc.boot_timeout_s, self.boot_interval)
            return client, None
        pid, rate = await self.pods.create(idx)
        self.clock[pid] = (time.monotonic(), rate)
        client = self.client_factory(self.pods.url(pid))
        t = time.monotonic()

        async def tick():
            if not self._work_left():
                raise _StopBoot("queue already drained by other pods")
            st = await self.pods.status(pid)
            if st in ("EXITED", "TERMINATED"):
                raise RuntimeError(f"pod {pid} went {st} while booting")
            log.info("pod %d: waiting for ComfyUI (%ds)", idx, time.monotonic() - t)

        try:
            await client.wait_healthy(self.pc.boot_timeout_s, self.boot_interval, tick)
        except BaseException:
            await client.close()
            await self._down(pid)
            raise
        log.info("pod %d: ComfyUI ready after %ds", idx, time.monotonic() - t)
        return client, pid

    async def _down(self, pid: str | None):
        if pid and self.pods:
            await self.pods.terminate(pid)
            self._close_clock(pid)

    async def _pod_worker(self, idx: int):
        restarts = 0
        while not self.queue.empty():
            try:
                client, pid = await self._bring_up(idx)
            except _StopBoot as e:
                log.info("pod %d: %s", idx, e)
                return
            except Exception as e:
                log.error("pod %d: failed to start: %s", idx, e)
                restarts += 1
                if restarts > self.pc.max_pod_restarts:
                    return
                continue
            try:
                dead = await self._drain(idx, client)
            finally:
                await client.close()
                await asyncio.shield(self._down(pid))
            if not dead:
                return
            restarts += 1
            if restarts > self.pc.max_pod_restarts or self.pods is None:
                log.error("pod %d: unreachable, out of restarts", idx)
                return
            log.warning("pod %d: unreachable - replacing it (%d/%d)", idx, restarts, self.pc.max_pod_restarts)

    async def _drain(self, idx: int, client: ComfyClient) -> bool:
        dead = asyncio.Event()

        async def lane():
            while not dead.is_set():
                try:
                    job = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                self.in_flight += 1
                try:
                    await self._process(idx, client, job)
                except ComfyUnreachable as e:
                    log.warning("job %s: pod unreachable (%s) - requeued", job.id, e)
                    self.queue.put_nowait(job)
                    dead.set()
                except Exception as e:
                    job.attempts += 1
                    if job.attempts <= self.pc.max_retries:
                        log.warning("job %s: %s - retry %d/%d", job.id, e, job.attempts, self.pc.max_retries)
                        await client.free_memory()
                        self.queue.put_nowait(job)
                    else:
                        log.error("job %s: FAILED after %d attempts: %s", job.id, job.attempts, e)
                        self.state.record({"id": job.id, "status": "failed", "error": str(e)[:1000]})
                finally:
                    self.in_flight -= 1

        await asyncio.gather(*(lane() for _ in range(self.pc.inflight_per_pod)))
        return dead.is_set()

    # ---------- one job ----------
    async def _process(self, idx: int, client: ComfyClient, job: Job):
        t = time.monotonic()
        uploaded = {k: await client.upload(p) for k, p in job.uploads.items()}
        wf = render(self.workflow, job_values(job, uploaded))
        prompt_id = await client.submit(wf)
        outputs = await client.wait(prompt_id, self.pc.job_timeout_s, self.poll_interval)
        files = output_files(outputs)
        if not files:
            raise ComfyError("workflow finished but produced no output files (missing Save node?)")
        saved = []
        for i, f in enumerate(files):
            suffix = Path(f["filename"]).suffix
            name = f"{job.id}{suffix}" if len(files) == 1 else f"{job.id}_{i:02d}{suffix}"
            saved.append(str((await client.download(f, self.out / name)).relative_to(self.out)))
        secs = time.monotonic() - t
        self.state.record({"id": job.id, "status": "done", "files": saved, "seconds": round(secs, 1)})
        n_done = sum(1 for j in self.jobs if j.id in self.state.done)
        log.info("job %s done in %.0fs on pod %d -> %s  [%d/%d, $%.2f so far]",
                 job.id, secs, idx, ", ".join(saved), n_done, len(self.jobs), self.spend())

    # ---------- entry ----------
    async def run(self) -> dict:
        errs = validate(self.workflow, self.jobs)
        if errs:
            raise SystemExit("validation failed:\n  " + "\n  ".join(errs))
        pending = [j for j in self.jobs if j.id not in self.state.done]
        log.info("%d jobs, %d already done, %d to run", len(self.jobs), len(self.jobs) - len(pending), len(pending))
        for j in pending:
            self.queue.put_nowait(j)
        if pending:
            n = 1 if self.pods is None else max(1, min(self.pc.pods, len(pending)))
            main = asyncio.ensure_future(asyncio.gather(*(self._pod_worker(i) for i in range(n))))
            dog = asyncio.create_task(self._watchdog(main))
            try:
                await main
            except asyncio.CancelledError:
                log.error("run cancelled - tearing down")
            finally:
                dog.cancel()
                if self.pods:
                    for pid in list(self.pods.live):
                        await asyncio.shield(self._down(pid))
        return self._report()

    def _report(self) -> dict:
        rows = []
        for j in self.jobs:
            rec = self.state.done.get(j.id) or self.state.failed.get(j.id) or {"status": "not_run"}
            rows.append({"id": j.id, "status": rec["status"], "files": ";".join(rec.get("files", [])),
                         "seconds": rec.get("seconds", ""), "error": rec.get("error", "")})
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / "report.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        summary = {s: sum(r["status"] == s for r in rows) for s in ("done", "failed", "not_run")}
        summary["gpu_cost_usd"] = round(self.spend(), 2)
        summary["ordered_files"] = [self.out / f for r in rows if r["status"] == "done"
                                    for f in r["files"].split(";") if f]
        return summary
