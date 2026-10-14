"""Stage DAG executor with content-addressed caching.

A recipe turns one ad variant into a small DAG of stages:

    voice ─┐
    avatar ┴─> talk ─┐
    broll ───────────┴─> assemble

Each stage is either a registered ComfyUI workflow (runs on the GPU backend) or a local Python
function (ffmpeg assembly, captions...). Its cache key is a hash of *what it computes from*:
the workflow version (or function version) plus every input value, with input files hashed by
content. So when only the hook changes, the avatar and b-roll are reused, and identical stages
requested concurrently by different variants render exactly once.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .backends.base import Backend, BackendDown, StageFailed
from .registry import Workflow
from .store import StageRun, Store

log = logging.getLogger("run_comfy")


@dataclass(frozen=True)
class Ref:
    """Use output file #index of another stage as an input."""
    stage: str
    index: int = 0


LocalFn = Callable[["StageContext", dict], Awaitable[list[Path]] | list[Path]]


@dataclass
class Stage:
    name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    workflow: str | None = None      # registry name -> GPU backend
    fn: LocalFn | None = None        # or a local function
    version: str = "1"               # bump when a local fn's behaviour changes
    retries: int = 2
    cache: bool = True

    def deps(self) -> set[str]:
        return {v.stage for v in _walk(self.inputs) if isinstance(v, Ref)}


@dataclass
class StageContext:
    stage: Stage
    out_dir: Path
    variant_id: int | None
    progress: Callable[[float, str], None]


def _walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


class _Hasher:
    def __init__(self):
        self._memo: dict[tuple, str] = {}

    def file(self, p: Path) -> str:
        st = p.stat()
        k = (str(p.resolve()), st.st_size, st.st_mtime_ns)
        if k not in self._memo:
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            self._memo[k] = h.hexdigest()
        return self._memo[k]

    def norm(self, v):
        if isinstance(v, Path):
            return {"file": self.file(v)}
        if isinstance(v, dict):
            return {k: self.norm(x) for k, x in sorted(v.items())}
        if isinstance(v, (list, tuple)):
            return [self.norm(x) for x in v]
        return v


class Executor:
    def __init__(self, store: Store, registry: dict[str, Workflow], backend: Backend,
                 on_event: Callable[[dict], None] | None = None, gpu_parallel: int = 4):
        self.store, self.registry, self.backend = store, registry, backend
        self.on_event = on_event or (lambda e: None)
        self.cache_dir = store.data_dir / "cache"
        self.hasher = _Hasher()
        self._inflight: dict[str, asyncio.Future] = {}
        self._gpu = asyncio.Semaphore(gpu_parallel)
        self.stats = {"rendered": 0, "cached": 0, "deduped": 0}

    def cache_key(self, st: Stage, resolved: dict) -> str:
        if st.workflow:
            ident = {"workflow": st.workflow, "version": self.registry[st.workflow].version}
        else:
            ident = {"fn": f"{st.fn.__module__}.{st.fn.__qualname__}", "version": st.version}
        blob = json.dumps({"id": ident, "inputs": self.hasher.norm(resolved)}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    async def run(self, stages: list[Stage], variant_id: int | None = None) -> dict[str, list[Path]]:
        by_name = {s.name: s for s in stages}
        for s in stages:
            missing = s.deps() - set(by_name)
            if missing:
                raise ValueError(f"stage {s.name} depends on unknown {sorted(missing)}")
            if bool(s.workflow) == bool(s.fn):
                raise ValueError(f"stage {s.name}: set exactly one of workflow / fn")
            if s.workflow and s.workflow not in self.registry:
                raise ValueError(f"stage {s.name}: workflow {s.workflow!r} not in registry")
        results: dict[str, list[Path]] = {}
        tasks: dict[str, asyncio.Task] = {}

        async def run_one(name: str):
            st = by_name[name]
            await asyncio.gather(*(tasks[d] for d in st.deps()))
            resolved = self._resolve(st.inputs, results)
            results[name] = await self._execute(st, resolved, variant_id)

        # create tasks in topological order so every dep task exists before it's awaited
        for name in _topo(by_name):
            tasks[name] = asyncio.create_task(run_one(name))
        try:
            await asyncio.gather(*tasks.values())
        except BaseException:
            for t in tasks.values():
                t.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise
        return results

    def _resolve(self, obj, results):
        if isinstance(obj, Ref):
            outs = results[obj.stage]
            if obj.index >= len(outs):
                raise StageFailed(f"stage {obj.stage} produced {len(outs)} files, needed #{obj.index}")
            return outs[obj.index]
        if isinstance(obj, dict):
            return {k: self._resolve(v, results) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve(v, results) for v in obj]
        return obj

    async def _execute(self, st: Stage, resolved: dict, variant_id) -> list[Path]:
        key = self.cache_key(st, resolved)
        rec = self.store.add(StageRun(variant_id=variant_id or 0, stage=st.name, cache_key=key))
        self.on_event({"type": "stage", "variant_id": variant_id, "stage": st.name, "status": "running"})
        t = time.monotonic()
        try:
            if st.cache and (hit := self.store.cache_get(key)):
                outs = [self.store.abs(p) for p in hit.outputs]
                self.stats["cached"] += 1
                self.store.update(StageRun, rec.id, status="done", cached=True, outputs=hit.outputs)
                self.on_event({"type": "stage", "variant_id": variant_id, "stage": st.name,
                               "status": "done", "cached": True})
                return outs
            if key in self._inflight:  # another variant is rendering the identical stage right now
                self.stats["deduped"] += 1
                outs = await asyncio.shield(self._inflight[key])
                self.store.update(StageRun, rec.id, status="done", cached=True,
                                  outputs=[self.store.rel(p) for p in outs])
                self.on_event({"type": "stage", "variant_id": variant_id, "stage": st.name,
                               "status": "done", "cached": True})
                return outs
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            try:
                outs, gpu_s = await self._render(st, resolved, key, variant_id)
                fut.set_result(outs)
            except BaseException as e:
                fut.set_exception(e)
                fut.exception()  # mark retrieved
                raise
            finally:
                self._inflight.pop(key, None)
            rels = [self.store.rel(p) for p in outs]
            if st.cache:
                self.store.cache_put(key, st.name, rels)
            self.stats["rendered"] += 1
            cost = self.backend.cost_of(gpu_s) if st.workflow else 0.0
            self.store.update(StageRun, rec.id, status="done", outputs=rels,
                              seconds=round(time.monotonic() - t, 2), cost_usd=round(cost, 4))
            self.on_event({"type": "stage", "variant_id": variant_id, "stage": st.name, "status": "done",
                           "cached": False, "seconds": round(time.monotonic() - t, 1), "cost_usd": cost})
            return outs
        except BaseException as e:
            self.store.update(StageRun, rec.id, status="failed", error=str(e)[:1000] or type(e).__name__,
                              seconds=round(time.monotonic() - t, 2))
            self.on_event({"type": "stage", "variant_id": variant_id, "stage": st.name, "status": "failed",
                           "error": str(e)[:300]})
            raise

    async def _render(self, st: Stage, resolved: dict, key: str, variant_id) -> tuple[list[Path], float]:
        out_dir = self.cache_dir / key
        last: Exception | None = None
        for attempt in range(st.retries + 1):
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True)

            def progress(frac: float, msg: str = ""):
                self.on_event({"type": "progress", "variant_id": variant_id, "stage": st.name,
                               "progress": round(frac, 3), "message": msg})

            t = time.monotonic()
            try:
                if st.workflow:
                    async with self._gpu:
                        outs = await self.backend.run(self.registry[st.workflow], resolved, out_dir, progress)
                else:
                    ctx = StageContext(st, out_dir, variant_id, progress)
                    r = st.fn(ctx, resolved)
                    outs = await r if asyncio.iscoroutine(r) else r
                return [Path(p) for p in outs], time.monotonic() - t
            except (StageFailed, BackendDown) as e:
                last = e
                log.warning("stage %s attempt %d/%d failed: %s", st.name, attempt + 1, st.retries + 1, e)
                await asyncio.sleep(min(2 ** attempt, 10) * (0 if isinstance(e, StageFailed) else 1))
        raise StageFailed(f"{st.name}: {last}")


def _topo(by_name: dict[str, Stage]) -> list[str]:
    order, seen, temp = [], set(), set()

    def visit(n):
        if n in seen:
            return
        if n in temp:
            raise ValueError(f"cycle at stage {n}")
        temp.add(n)
        for d in sorted(by_name[n].deps()):
            visit(d)
        temp.discard(n)
        seen.add(n)
        order.append(n)

    for n in by_name:
        visit(n)
    return order
