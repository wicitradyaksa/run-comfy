"""Factory service: the one API the CLI, dashboard and MCP server all drive.

Lifecycle of a batch:
    create_batch -> plan (Claude writes hooks/scripts) -> [edit plan] -> build_matrix -> estimate
    -> run (stages on the chosen backend, QA, auto-regenerate) -> review -> export
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import shutil
import uuid
from pathlib import Path

from .. import webhooks
from ..backends.base import Backend
from ..config import Config
from ..events import EventBus
from ..registry import load_registry
from ..stages import Executor, Ref, Stage
from ..storage import Storage
from ..store import Asset, Batch, BrandKit, Project, Store, Variant, now
from . import matrix as mx
from .brand import Brand
from .export import export_batch
from .qa import review as qa_review
from .recipes import Option, VariantCtx, get_recipes
from .writer import CreativePlan, write_plan

log = logging.getLogger("run_comfy")


class FactoryError(Exception):
    pass


class Factory:
    def __init__(self, cfg: Config, *, store: Store | None = None, bus: EventBus | None = None, llm=None,
                 backend_override: Backend | None = None):
        self.cfg = cfg
        fc = cfg.factory
        self.store = store or Store(cfg.path(fc.data_dir))
        self.bus = bus or EventBus()
        self.registry = load_registry(cfg.path(fc.workflows_dir))
        self.recipes = get_recipes()
        self.llm = llm
        self.backend_override = backend_override
        self.storage = Storage(cfg.storage)
        self.tasks: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------ setup
    def project(self, name: str = "Default") -> Project:
        found = self.store.list(Project, name=name)
        return found[0] if found else self.store.add(Project(name=name))

    def add_asset(self, kind: str, name: str, src: Path | bytes, project_id: int | None = None,
                  meta: dict | None = None) -> Asset:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[-80:] or "file"
        rel = Path("assets") / kind / f"{uuid.uuid4().hex[:8]}_{safe}"
        dest = self.store.data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, (bytes, bytearray)):
            dest.write_bytes(src)
        else:
            shutil.copyfile(src, dest)
        return self.store.add(Asset(project_id=project_id, kind=kind, name=name, path=str(rel), meta=meta or {}))

    def asset_path(self, asset_id: int | None) -> Path | None:
        if not asset_id:
            return None
        a = self.store.get(Asset, asset_id)
        return self.store.abs(a.path) if a else None

    def save_brand(self, brand: Brand, kit_id: int | None = None, project_id: int | None = None) -> BrandKit:
        if kit_id:
            return self.store.update(BrandKit, kit_id, name=brand.name, data=brand.model_dump())
        return self.store.add(BrandKit(project_id=project_id, name=brand.name, data=brand.model_dump()))

    def brand_for(self, batch: Batch) -> Brand:
        kit = self.store.get(BrandKit, batch.brand_kit_id) if batch.brand_kit_id else None
        return Brand(**kit.data) if kit else Brand()

    # ------------------------------------------------------------------ batches
    def create_batch(self, name: str, recipe: str, *, brief: str = "", url: str | None = None,
                     brand_kit_id: int | None = None, product_asset_id: int | None = None,
                     avatar_asset_ids: list[int] | None = None, face_asset_id: int | None = None,
                     settings: dict | None = None, project_id: int | None = None) -> Batch:
        if recipe not in self.recipes:
            raise FactoryError(f"unknown recipe {recipe!r}; choose from {list(self.recipes)}")
        r = self.recipes[recipe]
        if "product" in r.requires and not product_asset_id:
            raise FactoryError(f"recipe {recipe} needs a product image")
        settings = dict(settings or {})
        settings.setdefault("seed", random.randint(1, 10**9))
        return self.store.add(Batch(
            project_id=project_id or self.project().id, brand_kit_id=brand_kit_id, name=name, recipe=recipe,
            brief={"text": brief, "url": url, "product_asset_id": product_asset_id,
                   "avatar_asset_ids": avatar_asset_ids or [], "face_asset_id": face_asset_id},
            settings=settings))

    async def plan(self, batch_id: int, counts: dict | None = None) -> CreativePlan:
        b = self._batch(batch_id)
        if b.status == "running":
            raise FactoryError("batch is running")
        plan = await write_plan(b.brief.get("text", ""), self.brand_for(b), url=b.brief.get("url"), counts=counts,
                                model=self.cfg.factory.anthropic_model, client=self.llm)
        self.set_plan(batch_id, plan.model_dump())
        return plan

    def set_plan(self, batch_id: int, plan: dict) -> Batch:
        b = self._batch(batch_id)
        if any(v.status != "pending" for v in self.store.variants(batch_id)):
            raise FactoryError("variants already rendered - create a new batch to change the plan")
        p = CreativePlan(**plan)
        for v in self.store.variants(batch_id):  # plan change invalidates the matrix
            self.store.delete(Variant, v.id)
        return self.store.update(Batch, b.id, plan=p.model_dump(), matrix={})

    def settings_for(self, b: Batch) -> dict:
        c, brand = self.cfg, self.brand_for(b)
        return {"backend": c.factory.backend, "aspects": brand.aspects,
                "caption_style": brand.caption_style or c.factory.caption_style,
                "voice_provider": c.voice.provider, "voice_model": c.voice.elevenlabs_model,
                "tts_workflow": c.voice.comfy_workflow, "lufs": c.factory.target_lufs, "qa": c.factory.qa,
                "qa_threshold": c.factory.qa_threshold, "qa_max_regens": c.factory.qa_max_regens,
                "max_spend_usd": c.factory.max_spend_usd, **b.settings}

    def _assets(self, b: Batch) -> dict:
        brand = self.brand_for(b)
        return {"product": self.asset_path(b.brief.get("product_asset_id")),
                "face": self.asset_path(b.brief.get("face_asset_id")),
                "avatars": [p for p in (self.asset_path(i) for i in b.brief.get("avatar_asset_ids", [])) if p],
                "music": self.asset_path(brand.music_asset_id), "logo": self.asset_path(brand.logo_asset_id),
                "endcard": self.asset_path(brand.endcard_asset_id)}

    def options(self, b: Batch) -> dict[str, list[Option]]:
        if not b.plan:
            raise FactoryError("batch has no plan yet - run plan() or set_plan() first")
        return self.recipes[b.recipe].options(CreativePlan(**b.plan), self._assets(b), self.settings_for(b))

    def build_matrix(self, batch_id: int, selected: dict[str, list[int]] | None = None, mode: str = "pairwise",
                     n: int | None = None) -> list[Variant]:
        b = self._batch(batch_id)
        if any(v.status != "pending" for v in self.store.variants(batch_id)):
            raise FactoryError("batch already has rendered variants")
        opts = self.options(b)
        recipe = self.recipes[b.recipe]
        keep = {}
        for axis, _ in recipe.axes:
            idxs = (selected or {}).get(axis)
            idxs = list(range(len(opts.get(axis, [])))) if idxs is None else [i for i in idxs
                                                                              if i < len(opts.get(axis, []))]
            if not idxs:
                raise FactoryError(f"axis {axis!r} has no options selected")
            keep[axis] = idxs
        combos = mx.build({a: len(v) for a, v in keep.items()}, mode, n, seed=b.settings.get("seed", 0))
        for v in self.store.variants(batch_id):
            self.store.delete(Variant, v.id)
        out = []
        for c in combos:
            real = {a: keep[a][i] for a, i in c.items()}
            out.append(self.store.add(Variant(batch_id=b.id, code=mx.code(real), axes=real)))
        self.store.update(Batch, b.id, matrix={"selected": keep, "mode": mode, "n": n})
        return out

    def _ctx(self, b: Batch, v: Variant, opts=None) -> VariantCtx:
        opts = opts or self.options(b)
        choice = {axis: opts[axis][i].value for axis, i in v.axes.items()}
        s = self.settings_for(b)
        return VariantCtx(brand=self.brand_for(b), plan=CreativePlan(**b.plan), choice=choice,
                          assets=self._assets(b), settings=s, overrides=v.overrides, seed=s.get("seed", b.id))

    def labels(self, b: Batch, v: Variant, opts=None) -> dict:
        opts = opts or self.options(b)
        return {axis: opts[axis][i].label for axis, i in v.axes.items()}

    # ------------------------------------------------------------------ estimate
    def estimate(self, batch_id: int) -> dict:
        b = self._batch(batch_id)
        variants = self.store.variants(batch_id)
        opts = self.options(b)
        s = self.settings_for(b)
        unique: dict[str, str] = {}
        naive: dict[str, int] = {}
        for v in variants:
            stages = self.recipes[b.recipe].stages(self._ctx(b, v, opts))
            sigs: dict[str, str] = {}
            for st in _order(stages):
                sigs[st.name] = _signature(st, sigs)
                if st.workflow:
                    unique[sigs[st.name]] = st.workflow
                    naive[st.workflow] = naive.get(st.workflow, 0) + 1
        renders: dict[str, int] = {}
        for wf in unique.values():
            renders[wf] = renders.get(wf, 0) + 1
        rate = self._rate(s["backend"])
        secs = sum(self.registry[w].est_seconds * n for w, n in renders.items())
        naive_secs = sum(self.registry[w].est_seconds * n for w, n in naive.items())
        warnings = []
        for w in renders:
            wf = self.registry[w]
            if not wf.commercial_ok:
                warnings.append(f"{w}: licence may not allow commercial use ({wf.license})")
            if not wf.verified and s["backend"] != "demo":
                warnings.append(f"{w}: not yet verified on a real GPU - run `workflows check` first")
        if s.get("voice_provider") == "elevenlabs":
            warnings.append("ElevenLabs voice cost is billed separately by ElevenLabs")
        return {"variants": len(variants), "renders": renders, "gpu_seconds": round(secs),
                "gpu_seconds_without_cache": round(naive_secs), "usd": round(secs * rate, 2),
                "usd_without_cache": round(naive_secs * rate, 2), "backend": s["backend"],
                "rate_per_gpu_second": rate, "warnings": sorted(set(warnings))}

    def _rate(self, backend: str) -> float:
        if backend == "serverless":
            return self.cfg.serverless.cost_per_sec_usd
        if backend == "pod":
            return self.cfg.runpod.hourly_rate_estimate_usd / 3600 / max(1, self.cfg.pipeline.inflight_per_pod)
        return 0.0

    # ------------------------------------------------------------------ run
    def make_backend(self, name: str) -> Backend:
        if self.backend_override is not None:
            return self.backend_override
        c = self.cfg
        if name == "demo":
            from ..backends.demo import DemoBackend
            return DemoBackend(c.factory.demo_delay_s)
        if name == "url":
            from ..backends.comfy_pool import ComfyPoolBackend
            if not c.factory.comfy_url:
                raise FactoryError("[factory].comfy_url is empty")
            return ComfyPoolBackend(base_url=c.factory.comfy_url, job_timeout_s=c.pipeline.job_timeout_s,
                                    inflight=c.pipeline.inflight_per_pod)
        if name == "pod":
            from ..backends.comfy_pool import ComfyPoolBackend
            from ..pods import PodManager
            pm = PodManager(c.runpod, c.api_key, self.store.data_dir / "pods.json")
            return ComfyPoolBackend(pods=pm, n_pods=c.pipeline.pods, inflight=c.pipeline.inflight_per_pod,
                                    job_timeout_s=c.pipeline.job_timeout_s, boot_timeout_s=c.pipeline.boot_timeout_s,
                                    max_restarts=c.pipeline.max_pod_restarts)
        if name == "serverless":
            from ..backends.serverless_backend import ServerlessBackend
            from ..serverless import EndpointClient
            if not (c.api_key and c.serverless.endpoint_id):
                raise FactoryError("serverless needs RUNPOD_API_KEY and [serverless].endpoint_id")
            return ServerlessBackend(EndpointClient(c.serverless.endpoint_id, c.api_key),
                                     c.serverless.cost_per_sec_usd, c.pipeline.job_timeout_s,
                                     c.serverless.poll_interval_s)
        raise FactoryError(f"unknown backend {name!r}")

    def start(self, batch_id: int) -> asyncio.Task:
        """Run in the background (dashboard / MCP)."""
        if batch_id in self.tasks and not self.tasks[batch_id].done():
            raise FactoryError("batch is already running")
        t = asyncio.create_task(self.run(batch_id))
        self.tasks[batch_id] = t
        return t

    def cancel(self, batch_id: int) -> bool:
        t = self.tasks.get(batch_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    async def run(self, batch_id: int, variant_ids: list[int] | None = None) -> Batch:
        b = self._batch(batch_id)
        if not b.plan:
            raise FactoryError("batch has no plan")
        todo = [v for v in self.store.variants(batch_id)
                if (variant_ids is None and v.status != "done") or (variant_ids and v.id in variant_ids)]
        if not todo:
            if not self.store.variants(batch_id):
                raise FactoryError("batch has no variants - build the matrix first")
            return b
        s = self.settings_for(b)
        self.store.update(Batch, b.id, status="running", started=now(), error="")
        self._emit(b.id, {"type": "batch", "status": "running", "variants": len(todo)})
        backend = self.make_backend(s["backend"])
        ex = Executor(self.store, self.registry, backend, on_event=lambda e: self._emit(b.id, e),
                      gpu_parallel=max(4, self.cfg.pipeline.pods * self.cfg.pipeline.inflight_per_pod))
        sem = asyncio.Semaphore(self.cfg.factory.max_parallel_variants)
        opts = self.options(b)
        base_cost = b.cost_usd

        async def one(v: Variant):
            async with sem:
                await self._run_variant(b, v, ex, opts, s)

        main = asyncio.ensure_future(asyncio.gather(*(one(v) for v in todo)))
        status, err = "done", ""

        async def watchdog():
            nonlocal err
            while not main.done():
                await asyncio.sleep(2)
                spent = base_cost + backend.spend()
                self.store.update(Batch, b.id, cost_usd=round(spent, 4))
                if spent > float(s["max_spend_usd"]):
                    log.error("batch %s hit its budget ($%.2f) - stopping", b.id, spent)
                    err = f"budget ${s['max_spend_usd']} reached"
                    main.cancel()

        dog = asyncio.create_task(watchdog())
        try:
            await main
        except asyncio.CancelledError:
            status = "cancelled"
            err = err or "cancelled"
        finally:
            dog.cancel()
            if backend is not self.backend_override:
                await asyncio.shield(backend.close())
            for v in self.store.variants(batch_id):
                if v.status == "running":
                    self.store.update(Variant, v.id, status="cancelled")
            vs = self.store.variants(batch_id)
            if status == "done" and vs and all(v.status == "failed" for v in vs):
                status, err = "failed", "every variant failed"
            spent = base_cost + backend.spend()
            b = self.store.update(Batch, b.id, status=status, finished=now(), error=err, cost_usd=round(spent, 4))
            summary = {"type": "batch", "status": status, "error": err, "cost_usd": b.cost_usd,
                       "done": sum(v.status == "done" for v in vs), "failed": sum(v.status == "failed" for v in vs),
                       "cache": ex.stats}
            self._emit(b.id, summary)
            self._hook(f"batch.{status}", {"batch_id": b.id, "name": b.name, **summary})
        return b

    async def _run_variant(self, b: Batch, v: Variant, ex: Executor, opts, s: dict):
        recipe = self.recipes[b.recipe]
        v = self.store.update(Variant, v.id, status="running", error="")
        self._emit(b.id, {"type": "variant", "variant_id": v.id, "code": v.code, "status": "running"})
        regens = 0
        try:
            while True:
                ctx = self._ctx(b, v, opts)
                stages = recipe.stages(ctx)
                results = await ex.run(stages, variant_id=v.id)
                final = results[stages[-1].name]
                aspects = ctx.settings.get("aspects") or ctx.brand.aspects
                outputs = {a: self.store.rel(p) for a, p in zip(aspects, final)}
                qa = {}
                if s.get("qa") and final:
                    qa = await qa_review(final[0], aspect=aspects[0], kind=recipe.output, brand=ctx.brand,
                                         script=" ".join(str(ctx.choice.get(k, "")) for k in ("hook", "body", "cta")),
                                         stages=[st.name for st in stages if st.name in recipe.qa_stages],
                                         threshold=int(s.get("qa_threshold", 7)), target_lufs=float(s.get("lufs", -14)),
                                         work_dir=self.store.data_dir / "qa" / str(v.id), client=self.llm,
                                         model=self.cfg.factory.anthropic_model)
                    suspect = qa.get("suspect_stage", "none")
                    if not qa["passed"] and regens < int(s.get("qa_max_regens", 1)) and suspect != "none":
                        regens += 1
                        ov = dict(v.overrides)
                        ov[suspect] = {**ov.get(suspect, {}), "seed": random.randint(1, 2**31)}
                        v = self.store.update(Variant, v.id, overrides=ov, attempts=v.attempts + 1,
                                              qa={**qa, "regenerating": suspect})
                        self._emit(b.id, {"type": "variant", "variant_id": v.id, "code": v.code, "status": "running",
                                          "message": f"QA {qa.get('score')}/10 - re-rendering {suspect}"})
                        continue
                cost = sum(r.cost_usd for r in self.store.stage_runs(v.id) if r.status == "done" and not r.cached)
                v = self.store.update(Variant, v.id, status="done", outputs=outputs, qa=qa, cost_usd=round(cost, 4))
                self._emit(b.id, {"type": "variant", "variant_id": v.id, "code": v.code, "status": "done",
                                  "outputs": outputs, "qa": qa})
                self._hook("variant.completed", {"batch_id": b.id, "variant_id": v.id, "code": v.code,
                                                 "outputs": outputs, "qa": qa})
                return
        except asyncio.CancelledError:
            self.store.update(Variant, v.id, status="cancelled")
            raise
        except Exception as e:
            log.exception("variant %s failed", v.code)
            self.store.update(Variant, v.id, status="failed", error=str(e)[:1000])
            self._emit(b.id, {"type": "variant", "variant_id": v.id, "code": v.code, "status": "failed",
                              "error": str(e)[:300]})

    async def regenerate(self, variant_id: int, stage: str | None = None, tweak: dict | None = None) -> Variant:
        """Re-render one variant: a stage with a new seed (and optional input tweaks), reusing everything else."""
        v = self.store.get(Variant, variant_id)
        b = self._batch(v.batch_id)
        if b.status == "running":
            raise FactoryError("batch is running")
        ov = dict(v.overrides)
        if stage:
            ov[stage] = {**ov.get(stage, {}), "seed": random.randint(1, 2**31), **(tweak or {})}
        self.store.update(Variant, v.id, overrides=ov, status="pending", review="pending",
                          attempts=v.attempts + 1)
        await self.run(b.id, variant_ids=[v.id])
        return self.store.get(Variant, v.id)

    def set_review(self, variant_id: int, decision: str) -> Variant:
        if decision not in ("approved", "rejected", "pending"):
            raise FactoryError("review must be approved | rejected | pending")
        return self.store.update(Variant, variant_id, review=decision)

    def export(self, batch_id: int, approved_only: bool = True) -> dict:
        b = self._batch(batch_id)
        vs = self.store.variants(batch_id)
        opts = self.options(b)
        path, n = export_batch(self.store, b, vs, {v.id: self.labels(b, v, opts) for v in vs}, approved_only)
        out = {"path": str(path), "rel": self.store.rel(path), "files": n}
        if self.storage.enabled and n:
            out["url"] = self.storage.upload(path, f"exports/{path.name}")
        return out

    # ------------------------------------------------------------------ views
    def batch_view(self, batch_id: int) -> dict:
        b = self._batch(batch_id)
        vs = self.store.variants(batch_id)
        opts = None
        try:
            opts = self.options(b) if b.plan else None
        except FactoryError:
            pass
        variants = []
        for v in vs:
            runs = self.store.stage_runs(v.id)
            latest = {}
            for r in runs:
                latest[r.stage] = {"status": r.status, "cached": r.cached, "seconds": r.seconds,
                                   "cost_usd": r.cost_usd, "error": r.error}
            variants.append({**v.model_dump(), "labels": self.labels(b, v, opts) if opts else {}, "stages": latest})
        return {**b.model_dump(), "brand": self.brand_for(b).model_dump(), "variants": variants,
                "options": {k: [o.label for o in lst] for k, lst in opts.items()} if opts else {},
                "recipe_info": self.recipes[b.recipe].public()}

    def _batch(self, batch_id: int) -> Batch:
        b = self.store.get(Batch, batch_id)
        if not b:
            raise FactoryError(f"batch {batch_id} not found")
        return b

    def _emit(self, batch_id: int, event: dict):
        self.bus.publish(batch_id, event)

    def _hook(self, kind: str, data: dict):
        if self.cfg.webhooks.url:
            try:
                asyncio.get_running_loop().create_task(
                    webhooks.send(self.cfg.webhooks.url, self.cfg.webhooks.secret, kind, data))
            except RuntimeError:
                pass


def _order(stages: list[Stage]) -> list[Stage]:
    from ..stages import _topo
    by = {s.name: s for s in stages}
    return [by[n] for n in _topo(by)]


def _signature(st: Stage, sigs: dict[str, str]) -> str:
    def norm(x):
        if isinstance(x, Ref):
            return {"ref": sigs[x.stage], "i": x.index}
        if isinstance(x, dict):
            return {k: norm(v) for k, v in sorted(x.items())}
        if isinstance(x, (list, tuple)):
            return [norm(v) for v in x]
        return str(x)

    ident = st.workflow or f"{getattr(st.fn, '__qualname__', 'fn')}:{st.version}"
    return hashlib.sha256(json.dumps([ident, norm(st.inputs)], sort_keys=True).encode()).hexdigest()[:16]
