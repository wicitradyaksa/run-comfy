"""The MCP server exposes the factory as tools; drive a whole batch through them."""
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mcp_mod(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "config.toml").write_text(
        f'[factory]\ndata_dir = "data"\nworkflows_dir = "{ROOT / "workflows"}"\nbackend = "demo"\nqa = false\n')
    monkeypatch.setenv("RUN_COMFY_CONFIG", str(tmp_path / "config.toml"))
    import run_comfy.mcp as m
    m = importlib.reload(m)
    return m


async def test_tools_listed_with_schemas(mcp_mod):
    tools = {t.name: t for t in await mcp_mod.server.list_tools()}
    assert {"create_batch", "build_matrix", "run_batch", "batch_status", "export_batch"} <= set(tools)
    assert "brief" in tools["create_batch"].input_schema["properties"]


async def test_batch_through_mcp_tools(mcp_mod, tmp_path):
    from run_comfy import media
    prod = media.text_card(tmp_path / "p.png", 400, 400, "P")
    call = mcp_mod.server.call_tool
    brand = await call("save_brand", {"name": "Glow", "aspects": ["1:1"]})
    assert not brand.is_error
    res = await call("create_batch", {"name": "MCP", "recipe": "product", "product_image": str(prod),
                                      "brand_id": 1})
    assert not res.is_error, res
    batch_id = res.structured_content["batch_id"]
    m = await call("build_matrix", {"batch_id": batch_id, "selected": {"style": [0], "headline": [0], "cta": [0]},
                                    "mode": "full"})
    assert m.structured_content["estimate"]["variants"] == 1
    r = await call("run_batch", {"batch_id": batch_id, "wait": True})
    s = r.structured_content
    assert s["status"] == "done" and Path(s["variants"][0]["files"]["1:1"]).exists()
    await call("review_variant", {"variant_id": s["variants"][0]["id"], "decision": "approved"})
    ex = await call("export_batch", {"batch_id": batch_id})
    assert ex.structured_content["files"] == 1
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="unknown recipe"):
        await call("create_batch", {"name": "x", "recipe": "nope"})
