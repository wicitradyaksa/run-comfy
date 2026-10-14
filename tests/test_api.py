"""HTTP API over the demo backend, driven exactly like the dashboard does."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from run_comfy import config as config_mod, media
from run_comfy.api import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "config.toml").write_text(
        f'[factory]\ndata_dir = "data"\nworkflows_dir = "{ROOT / "workflows"}"\nbackend = "demo"\nqa = false\n')
    return create_app(config_mod.load(tmp_path / "config.toml"))


async def test_dashboard_flow_over_http(app, tmp_path):
    prod = media.text_card(tmp_path / "p.png", 400, 400, "P")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/status")).json()["backend"] == "demo"
        assert {r["name"] for r in (await c.get("/api/recipes")).json()} == {"ugc", "product", "clip"}
        a = (await c.post("/api/assets", data={"kind": "product"},
                          files={"file": ("p.png", prod.read_bytes(), "image/png")})).json()
        assert (await c.get(a["url"])).content == prod.read_bytes()
        kit = (await c.post("/api/brands", json={"name": "Glow", "aspects": ["1:1"]})).json()
        b = (await c.post("/api/batches", json={"name": "API", "recipe": "product", "brand_kit_id": kit["id"],
                                                "product_asset_id": a["id"]})).json()
        b = (await c.post(f"/api/batches/{b['id']}/plan", json={})).json()
        assert b["plan"]["headlines"]
        m = (await c.post(f"/api/batches/{b['id']}/matrix",
                          json={"selected": {"style": [0], "headline": [0, 1], "cta": [0]}, "mode": "full"})).json()
        assert len(m["variants"]) == 2 and m["estimate"]["renders"]["flux_kontext_scene"] == 1
        assert (await c.post(f"/api/batches/{b['id']}/run")).status_code == 200  # background task starts
        for _ in range(300):
            view = (await c.get(f"/api/batches/{b['id']}")).json()
            if view["status"] not in ("draft", "running"):
                break
            await asyncio.sleep(0.1)
        assert view["status"] == "done", view
        vid = view["variants"][0]["id"]
        assert (await c.post(f"/api/variants/{vid}/review", json={"decision": "approved"})).json()["review"] == "approved"
        ex = (await c.post(f"/api/batches/{b['id']}/export", json={"approved_only": True})).json()
        assert ex["files"] == 1 and (await c.get(ex["download"])).status_code == 200
        # errors come back as 400 with a readable message
        r = await c.post("/api/batches", json={"name": "x", "recipe": "nope"})
        assert r.status_code == 400 and "unknown recipe" in r.json()["detail"]
        for evil in ("/files/../../etc/passwd", "/files/%2e%2e/%2e%2e/etc/passwd", "/files/..%2f..%2fetc%2fpasswd"):
            r = await c.get(evil)
            assert "root:" not in r.text and r.status_code in (200, 404)


async def test_events_stream_history(app):
    f = app.state.factory
    f.bus.publish(1, {"type": "batch", "status": "running"})
    assert json.loads(json.dumps(f.bus.history(1)))[0]["status"] == "running"
