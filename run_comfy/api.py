"""HTTP API + dashboard host. `run_comfy serve` starts it; the React app in web/ talks to /api/*."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Config
from .factory.brand import Brand
from .factory.service import Factory, FactoryError
from .store import Asset, Batch, BrandKit

WEB_DIST = Path(__file__).parent / "web_dist"


class BatchIn(BaseModel):
    name: str
    recipe: str
    brief: str = ""
    url: str | None = None
    brand_kit_id: int | None = None
    product_asset_id: int | None = None
    avatar_asset_ids: list[int] = []
    face_asset_id: int | None = None
    settings: dict = {}


class PlanIn(BaseModel):
    counts: dict | None = None


class MatrixIn(BaseModel):
    selected: dict[str, list[int]] | None = None
    mode: str = "pairwise"
    n: int | None = None


class ReviewIn(BaseModel):
    decision: str


class RegenIn(BaseModel):
    stage: str | None = None
    tweak: dict = {}


class ExportIn(BaseModel):
    approved_only: bool = True


def create_app(cfg: Config, factory: Factory | None = None) -> FastAPI:
    app = FastAPI(title="run-comfy", version="2.0")
    f = factory or Factory(cfg)
    app.state.factory = f

    @app.exception_handler(FactoryError)
    async def _factory_error(_, e: FactoryError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value_error(_, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # ---------------- meta ----------------
    @app.get("/api/status")
    def status():
        return {"backend": cfg.factory.backend, "voice_provider": cfg.voice.provider,
                "model": cfg.factory.anthropic_model,
                "keys": {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")), "runpod": bool(cfg.api_key),
                         "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY"))},
                "serverless_endpoint": bool(cfg.serverless.endpoint_id), "storage": f.storage.enabled,
                "webhooks": bool(cfg.webhooks.url), "workflows": len(f.registry),
                "budget_default": cfg.factory.max_spend_usd}

    @app.get("/api/recipes")
    def recipes():
        return [r.public() for r in f.recipes.values()]

    @app.get("/api/workflows")
    def workflows():
        return [wf.public() for wf in f.registry.values()]

    # ---------------- brands ----------------
    @app.get("/api/brands")
    def brands():
        return [{"id": k.id, "name": k.name, **k.data} for k in f.store.list(BrandKit, order=BrandKit.id)]

    @app.post("/api/brands")
    def create_brand(brand: Brand):
        k = f.save_brand(brand)
        return {"id": k.id, **k.data}

    @app.put("/api/brands/{kit_id}")
    def update_brand(kit_id: int, brand: Brand):
        k = f.save_brand(brand, kit_id)
        return {"id": k.id, **k.data}

    # ---------------- assets ----------------
    @app.get("/api/assets")
    def assets(kind: str | None = None):
        q = {"kind": kind} if kind else {}
        return [_asset(a) for a in f.store.list(Asset, order=Asset.id, **q)]

    @app.post("/api/assets")
    async def upload(kind: str = Form(...), file: UploadFile = File(...)):
        if kind not in ("avatar", "product", "face", "music", "logo", "endcard", "image", "video", "audio"):
            raise HTTPException(400, f"unknown asset kind {kind}")
        data = await file.read()
        if len(data) > 200 * 1024 * 1024:
            raise HTTPException(413, "file too large (200 MB max)")
        return _asset(f.add_asset(kind, file.filename or "upload", data))

    @app.delete("/api/assets/{asset_id}")
    def delete_asset(asset_id: int):
        f.store.delete(Asset, asset_id)
        return {"ok": True}

    def _asset(a: Asset) -> dict:
        return {**a.model_dump(), "url": f"/files/{a.path}"}

    @app.get("/files/{rel:path}")
    def files(rel: str):
        try:
            p = f.store.abs(rel)
        except ValueError:
            raise HTTPException(404)
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    # ---------------- batches ----------------
    @app.get("/api/batches")
    def batches():
        out = []
        for b in reversed(f.store.list(Batch, order=Batch.id)):
            vs = f.store.variants(b.id)
            out.append({**b.model_dump(exclude={"plan"}), "variants": len(vs),
                        "done": sum(v.status == "done" for v in vs), "failed": sum(v.status == "failed" for v in vs),
                        "approved": sum(v.review == "approved" for v in vs),
                        "thumb": next((v.outputs.get(next(iter(v.outputs))) for v in vs if v.outputs), None)})
        return out

    @app.post("/api/batches")
    def create_batch(body: BatchIn):
        b = f.create_batch(body.name, body.recipe, brief=body.brief, url=body.url, brand_kit_id=body.brand_kit_id,
                           product_asset_id=body.product_asset_id, avatar_asset_ids=body.avatar_asset_ids,
                           face_asset_id=body.face_asset_id, settings=body.settings)
        return f.batch_view(b.id)

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: int):
        return f.batch_view(batch_id)

    @app.patch("/api/batches/{batch_id}/settings")
    def patch_settings(batch_id: int, settings: dict):
        b = f.store.get(Batch, batch_id)
        if not b:
            raise HTTPException(404)
        f.store.update(Batch, batch_id, settings={**b.settings, **settings})
        return f.batch_view(batch_id)

    @app.post("/api/batches/{batch_id}/plan")
    async def write_plan(batch_id: int, body: PlanIn):
        await f.plan(batch_id, body.counts)
        return f.batch_view(batch_id)

    @app.put("/api/batches/{batch_id}/plan")
    def set_plan(batch_id: int, plan: dict):
        f.set_plan(batch_id, plan)
        return f.batch_view(batch_id)

    @app.post("/api/batches/{batch_id}/matrix")
    def matrix(batch_id: int, body: MatrixIn):
        f.build_matrix(batch_id, body.selected, body.mode, body.n)
        return {**f.batch_view(batch_id), "estimate": f.estimate(batch_id)}

    @app.get("/api/batches/{batch_id}/estimate")
    def estimate(batch_id: int):
        return f.estimate(batch_id)

    # async so they run on the event loop that owns the background render tasks
    @app.post("/api/batches/{batch_id}/run")
    async def run(batch_id: int):
        task = f.start(batch_id)
        await asyncio.sleep(0.05)  # surface immediate failures (bad backend config) as a 400
        if task.done() and not task.cancelled() and task.exception():
            raise task.exception()
        return {"ok": True}

    @app.post("/api/batches/{batch_id}/cancel")
    async def cancel(batch_id: int):
        return {"ok": f.cancel(batch_id)}

    @app.post("/api/batches/{batch_id}/export")
    def export(batch_id: int, body: ExportIn):
        out = f.export(batch_id, body.approved_only)
        return {**out, "download": f"/files/{out['rel']}"}

    @app.get("/api/batches/{batch_id}/events")
    async def events(batch_id: int, request: Request):
        q = f.bus.subscribe(batch_id)

        async def stream():
            try:
                yield "retry: 2000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        e = await asyncio.wait_for(q.get(), 15)
                        yield f"data: {json.dumps(e, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                f.bus.unsubscribe(batch_id, q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------- variants ----------------
    @app.post("/api/variants/{variant_id}/review")
    def review(variant_id: int, body: ReviewIn):
        return f.set_review(variant_id, body.decision).model_dump()

    @app.post("/api/variants/{variant_id}/regenerate")
    async def regenerate(variant_id: int, body: RegenIn):
        task = asyncio.create_task(f.regenerate(variant_id, body.stage, body.tweak))
        await asyncio.sleep(0.05)
        if task.done() and task.exception():
            raise task.exception()
        return {"ok": True}

    # ---------------- dashboard ----------------
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")

        @app.get("/{path:path}")
        def spa(path: str):
            p = WEB_DIST / path
            if path and p.is_file() and p.resolve().is_relative_to(WEB_DIST.resolve()):
                return FileResponse(p)
            return FileResponse(WEB_DIST / "index.html")

    return app
