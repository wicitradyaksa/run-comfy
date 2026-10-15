"""MCP server: lets Claude Code / Claude Desktop create, render, review and export ad batches.

    claude mcp add run-comfy -- python -m run_comfy.mcp          (or use the project .mcp.json)

Config is read from $RUN_COMFY_CONFIG or ./config.toml. Renders started here run in this server
process, so keep the client session open until `batch_status` says the batch is done.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import config as config_mod
from .cli import _load_dotenv
from .factory.brand import Brand
from .factory.service import Factory, FactoryError
from .store import BrandKit

INSTRUCTIONS = """run-comfy turns a product brief into a matrix of AI ad variants rendered on RunPod GPUs.
Typical flow: create_batch (writes hooks/scripts) -> get_plan / update_plan -> build_matrix (shows the cost
estimate incl. first-run setup: volume, model downloads, endpoint; get the user's OK before spending real
money) -> run_batch (sets up RunPod automatically if needed) -> batch_status until done ->
review_variant -> export_batch. The 'demo' backend is free and renders placeholder media."""

_server = MCPServer(name="run-comfy", instructions=INSTRUCTIONS)
_factory: Factory | None = None


class _Server:
    """Registers tools so that FactoryError (bad input, wrong state) reaches the model as a clear ToolError."""

    def __getattr__(self, name):
        return getattr(_server, name)

    def tool(self, **kw):
        def deco(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def wrapped(*a, **k):
                    try:
                        return await fn(*a, **k)
                    except FactoryError as e:
                        raise ToolError(str(e)) from e
            else:
                @functools.wraps(fn)
                def wrapped(*a, **k):
                    try:
                        return fn(*a, **k)
                    except FactoryError as e:
                        raise ToolError(str(e)) from e
            return _server.tool(**kw)(wrapped)
        return deco


server = _Server()


def factory() -> Factory:
    global _factory
    if _factory is None:
        path = Path(os.environ.get("RUN_COMFY_CONFIG", "config.toml")).resolve()
        _load_dotenv(path.parent)
        _factory = Factory(config_mod.load(path))
    return _factory


def _summary(batch_id: int) -> dict[str, Any]:
    v = factory().batch_view(batch_id)
    return {
        "batch_id": v["id"], "name": v["name"], "recipe": v["recipe"], "status": v["status"], "error": v["error"],
        "cost_usd": v["cost_usd"], "budget_usd": factory().settings_for(factory()._batch(batch_id))["max_spend_usd"],
        "variants": [{"id": x["id"], "code": x["code"], "status": x["status"], "review": x["review"],
                      "qa_score": (x["qa"] or {}).get("score"), "qa_passed": (x["qa"] or {}).get("passed"),
                      "files": {a: str(factory().store.abs(p)) for a, p in x["outputs"].items()},
                      "choices": x["labels"], "error": x["error"][:200]} for x in v["variants"]],
    }


@server.tool()
def list_recipes() -> list[dict[str, Any]]:
    """Ad formats available (ugc talking head, product statics, short-form clip) and their test axes."""
    return [r.public() for r in factory().recipes.values()]


@server.tool()
def list_brands() -> list[dict[str, Any]]:
    """Saved brand kits."""
    return [{"id": k.id, **k.data} for k in factory().store.list(BrandKit, order=BrandKit.id)]


@server.tool()
def save_brand(name: str, product: str = "", audience: str = "", facts: list[str] | None = None,
               banned: list[str] | None = None, tone: str = "friendly, direct, conversational UGC",
               colors: list[str] | None = None, cta_default: str = "Shop now",
               aspects: list[str] | None = None, brand_id: int | None = None) -> dict[str, Any]:
    """Create or update a brand kit. `facts` are the only claims the writer may make; `banned` are never used."""
    b = Brand(name=name, product=product, audience=audience, facts=facts or [], banned=banned or [], tone=tone,
              colors=colors or ["#111111", "#ffffff", "#8b5cf6"], cta_default=cta_default,
              aspects=aspects or ["9:16", "1:1", "16:9"])
    k = factory().save_brand(b, brand_id)
    return {"id": k.id, **k.data}


@server.tool()
async def create_batch(name: str, recipe: str = "ugc", brief: str = "", url: str | None = None,
                       brand_id: int | None = None, product_image: str | None = None,
                       presenter_images: list[str] | None = None, face_image: str | None = None,
                       backend: str | None = None, aspects: list[str] | None = None,
                       budget_usd: float | None = None) -> dict[str, Any]:
    """Create a batch and have Claude write its plan (hooks, scripts, CTAs, presenter and scene prompts).

    Image arguments are local file paths. backend: demo | serverless | pod | url (default from config)."""
    f = factory()
    up = lambda kind, p: f.add_asset(kind, Path(p).name, Path(p)).id if p else None  # noqa: E731
    settings = {k: v for k, v in {"backend": backend, "aspects": aspects, "max_spend_usd": budget_usd}.items() if v}
    b = f.create_batch(name, recipe, brief=brief, url=url, brand_kit_id=brand_id,
                       product_asset_id=up("product", product_image),
                       avatar_asset_ids=[up("avatar", p) for p in presenter_images or []],
                       face_asset_id=up("face", face_image), settings=settings)
    plan = await f.plan(b.id)
    return {"batch_id": b.id, "plan": plan.model_dump()}


@server.tool()
def get_plan(batch_id: int) -> dict[str, Any]:
    """The batch's creative plan and the options each test axis offers."""
    v = factory().batch_view(batch_id)
    return {"plan": v["plan"], "options": v["options"]}


@server.tool()
def update_plan(batch_id: int, plan: dict) -> dict[str, Any]:
    """Replace the plan (same shape as get_plan's `plan`). Clears any unrendered matrix."""
    factory().set_plan(batch_id, plan)
    return get_plan(batch_id)


@server.tool()
def build_matrix(batch_id: int, selected: dict[str, list[int]] | None = None, mode: str = "pairwise",
                 max_variants: int | None = None) -> dict[str, Any]:
    """Choose which combinations to render and get the cost estimate.

    selected: axis -> option indexes to include (omit an axis to include all of its options).
    mode: pairwise (every pair of choices together at least once) | full | random."""
    f = factory()
    vs = f.build_matrix(batch_id, selected, mode, max_variants)
    return {"variants": [{"id": v.id, "code": v.code} for v in vs], "estimate": f.estimate(batch_id)}


@server.tool()
async def run_batch(batch_id: int, wait: bool = False) -> dict[str, Any]:
    """Start rendering. With wait=false it returns immediately - poll batch_status."""
    f = factory()
    task = f.start(batch_id)
    if wait:
        await task
    else:
        await asyncio.sleep(0.2)
    return _summary(batch_id)


@server.tool()
def batch_status(batch_id: int) -> dict[str, Any]:
    """Progress, spend, QA scores and finished file paths for every variant."""
    return _summary(batch_id)


@server.tool()
def cancel_batch(batch_id: int) -> dict[str, Any]:
    """Stop a running batch and release GPUs."""
    return {"cancelled": factory().cancel(batch_id)}


@server.tool()
def review_variant(variant_id: int, decision: str) -> dict[str, Any]:
    """decision: approved | rejected | pending."""
    v = factory().set_review(variant_id, decision)
    return {"id": v.id, "code": v.code, "review": v.review}


@server.tool()
async def regenerate_variant(variant_id: int, stage: str, prompt: str | None = None) -> dict[str, Any]:
    """Re-render one stage of a variant with a new seed (optionally a new prompt); everything else is reused."""
    v = await factory().regenerate(variant_id, stage, {"prompt": prompt} if prompt else None)
    return {"id": v.id, "code": v.code, "status": v.status, "error": v.error}


@server.tool()
async def setup_infra(backend: str = "serverless", workflows: list[str] | None = None,
                      dry_run: bool = True) -> dict[str, Any]:
    """Create the RunPod network volume, download the models the workflows need, and set up the
    serverless endpoint (or pod image). dry_run=true (default) only returns what would be created and
    its cost - show that to the user and get an OK before calling again with dry_run=false.
    Paid batches also run this automatically before rendering."""
    f = factory()
    if dry_run:
        return {"plan": f.infra().plan(workflows, backend)}
    rep = await f.provision(backend=backend, workflows=workflows)
    return {"actions": rep.actions, "plan": rep.plan}


@server.tool()
def export_batch(batch_id: int, approved_only: bool = True) -> dict[str, Any]:
    """Zip finished variants (named for ad-platform upload) plus manifest.csv; returns the path (and URL if S3)."""
    return factory().export(batch_id, approved_only)


def main():
    _server.run("stdio")


if __name__ == "__main__":
    main()
