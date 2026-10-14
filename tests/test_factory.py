"""Content factory end to end on the demo backend (no GPU, no API keys)."""
import asyncio
import csv
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from run_comfy import config as config_mod, media
from run_comfy.backends.demo import DemoBackend
from run_comfy.factory import matrix as mx
from run_comfy.factory.brand import Brand
from run_comfy.factory.captions import build_ass, chunk
from run_comfy.factory.qa import QAReport
from run_comfy.factory.service import Factory, FactoryError
from run_comfy.factory.voice import chars_to_words, elevenlabs_tts
from run_comfy.factory.writer import CreativePlan, demo_plan, fetch_page, write_plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)


def make_factory(tmp_path, backend=None, llm=None, **factory_cfg):
    (tmp_path / "config.toml").write_text(
        f'[factory]\ndata_dir = "data"\nworkflows_dir = "{ROOT / "workflows"}"\nbackend = "demo"\n'
        + "".join(f"{k} = {json.dumps(v)}\n" for k, v in factory_cfg.items()))
    cfg = config_mod.load(tmp_path / "config.toml")
    return Factory(cfg, backend_override=backend, llm=llm)


def assets(f: Factory, tmp_path):
    prod = media.text_card(tmp_path / "product.png", 800, 800, "SERUM", bg="#7c3aed")
    media.ffmpeg("-f", "lavfi", "-i", "sine=f=262:d=3", "-c:a", "aac", str(tmp_path / "music.m4a"))
    p = f.add_asset("product", "product.png", prod)
    m = f.add_asset("music", "music.m4a", tmp_path / "music.m4a")
    kit = f.save_brand(Brand(name="Glow", product="vitamin C serum", audience="busy skincare fans",
                             facts=["absorbs in 10 seconds"], banned=["cure"], music_asset_id=m.id,
                             aspects=["9:16"], colors=["#0f172a", "#ffffff", "#facc15"]))
    return p, kit


# ---------------- pure units ----------------
def test_pairwise_covers_every_pair_with_fewer_variants():
    sizes = {"hook": 4, "body": 2, "avatar": 3, "voice": 2}
    combos = mx.build(sizes, "pairwise")
    assert len(combos) < 48 and len(combos) >= 12
    import itertools
    for a, b in itertools.combinations(sizes, 2):
        pairs = {(c[a], c[b]) for c in combos}
        assert len(pairs) == sizes[a] * sizes[b]
    assert len(mx.build(sizes, "full")) == 48
    assert len(mx.build(sizes, "random", n=5)) == 5
    assert mx.code({"hook": 1, "avatar": 0, "voice": 1}) == "H2-A1-V2"


def test_writer_guardrails_drop_banned_lines():
    brand = Brand(product="serum", banned=["cure"])
    plan = demo_plan("", brand, None, {"hooks": 9, "bodies": 9, "ctas": 9, "headlines": 9,
                                       "presenter_prompts": 9, "scene_styles": 9})
    plan.hooks.append("This will cure everything")
    from run_comfy.factory.writer import _clean, DEFAULT_COUNTS
    cleaned = _clean(plan, brand, {**DEFAULT_COUNTS, "hooks": 10})
    assert not any("cure" in h.lower() for h in cleaned.hooks)


async def test_writer_uses_claude_structured_output():
    class FakeMessages:
        async def parse(self, **kw):
            self.kw = kw
            plan = demo_plan("", Brand(), None, {})
            return type("R", (), {"parsed_output": plan, "stop_reason": "end_turn"})()

    fm = FakeMessages()
    client = type("C", (), {"beta": type("B", (), {"messages": fm})()})()
    page = {"url": "https://shop.example/serum", "title": "Glow Serum", "description": "Bright skin",
            "image": "", "text": "Vitamin C serum that absorbs fast"}
    plan = await write_plan("launch ad", Brand(name="Glow", banned=["cure"]), client=client, page=page)
    assert plan.hooks and fm.kw["output_format"] is CreativePlan and fm.kw["model"] == "claude-opus-5-5"
    assert fm.kw["fallbacks"] == "default" and "Glow Serum" in fm.kw["messages"][0]["content"]
    assert "cure" in fm.kw["messages"][0]["content"]  # banned list passed to the model


def test_fetch_page_extracts_text():
    html = ('<html><head><title>Glow Serum</title><meta property="og:description" content="Bright &amp; fast">'
            '<script>var x=1</script></head><body><h1>Vitamin C</h1><p>Absorbs fast</p></body></html>')
    page = fetch_page("https://x.example", transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
    assert page["title"] == "Glow Serum" and page["description"] == "Bright & fast"
    assert "Absorbs fast" in page["text"] and "var x" not in page["text"]


def test_elevenlabs_alignment_to_words(tmp_path):
    import base64
    chars = list("Hi there")
    resp = {"audio_base64": base64.b64encode(b"MP3").decode(),
            "alignment": {"characters": chars, "character_start_times_seconds": [i * 0.1 for i in range(8)],
                          "character_end_times_seconds": [i * 0.1 + 0.08 for i in range(8)]}}
    seen = {}

    def handler(req):
        seen["url"], seen["key"] = str(req.url), req.headers["xi-api-key"]
        return httpx.Response(200, json=resp)

    audio, words = elevenlabs_tts("Hi there", "voice123", tmp_path / "v.mp3", api_key="k",
                                  transport=httpx.MockTransport(handler))
    assert audio.read_bytes() == b"MP3" and "voice123/with-timestamps" in seen["url"]
    assert [w["word"] for w in words] == ["Hi", "there"] and words[1]["start"] == pytest.approx(0.3)
    assert chars_to_words([], [], []) == []


def test_captions_chunks_and_styles(tmp_path):
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(
        "Stop scrolling. This serum changed my mornings completely".split())]
    groups = chunk(words)
    assert [len(g) for g in groups] == [2, 3, 3]  # breaks at punctuation, max 3 words
    for style in ("bold_pop", "karaoke", "minimal"):
        ass = build_ass(words, tmp_path / f"{style}.ass", 1080, 1920, style).read_text()
        assert "PlayResX: 1080" in ass and "Dialogue:" in ass
    pop = (tmp_path / "bold_pop.ass").read_text()
    assert "SCROLLING" in pop and "\\fscx112" in pop  # active word pops
    assert "\\kf" in (tmp_path / "karaoke.ass").read_text()


# ---------------- end to end ----------------
async def test_ugc_batch_end_to_end(tmp_path):
    be = DemoBackend()
    f = make_factory(tmp_path, backend=be)
    prod, kit = assets(f, tmp_path)
    b = f.create_batch("Glow launch", "ugc", brief="Launch the serum", brand_kit_id=kit.id,
                       product_asset_id=prod.id, settings={"qa": True})
    plan = await f.plan(b.id, counts={"hooks": 2, "bodies": 1, "ctas": 1, "presenter_prompts": 2})
    assert len(plan.hooks) == 2
    vs = f.build_matrix(b.id, {"voice": [0]}, mode="full")
    assert [v.code for v in vs] == ["H1-S1-C1-A1-V1", "H1-S1-C1-A2-V1", "H2-S1-C1-A1-V1", "H2-S1-C1-A2-V1"]
    est = f.estimate(b.id)
    # 2 avatars, 1 b-roll key + clip shared by all, 4 talk renders (avatar x script)
    assert est["renders"] == {"flux_txt2img": 2, "flux_kontext_scene": 1, "wan22_i2v": 1, "wan22_s2v": 4}
    assert est["gpu_seconds"] < est["gpu_seconds_without_cache"]
    assert any("licence" in w for w in est["warnings"])

    events = f.bus.subscribe(b.id)
    done = await f.run(b.id)
    assert done.status == "done", f.batch_view(b.id)
    view = f.batch_view(b.id)
    assert all(v["status"] == "done" for v in view["variants"])
    v0 = view["variants"][0]
    out = f.store.abs(v0["outputs"]["9:16"])
    assert media.video_size(out) == (1080, 1920) and media.has_stream(out, "audio")
    assert abs(media.loudness(out) + 14) < 1.5
    assert v0["qa"]["passed"] and v0["qa"]["technical"] == []
    assert v0["labels"]["hook"] == plan.hooks[0]
    # caching: the avatar and b-roll rendered once and were reused
    assert be.runs == 2 + 1 + 1 + 4
    kinds = []
    while not events.empty():
        kinds.append(events.get_nowait()["type"])
    assert {"batch", "variant", "stage"} <= set(kinds)

    # review + export with ad-platform manifest
    f.set_review(view["variants"][0]["id"], "approved")
    f.set_review(view["variants"][1]["id"], "rejected")
    ex = f.export(b.id)
    with zipfile.ZipFile(ex["path"]) as z:
        names = z.namelist()
        manifest = list(csv.DictReader(io.StringIO(z.read("manifest.csv").decode())))
    assert names == ["glow-launch_H1-S1-C1-A1-V1_9x16.mp4", "manifest.csv"]
    assert manifest[0]["hook"] == plan.hooks[0] and manifest[0]["variant"] == "H1-S1-C1-A1-V1"

    # regenerate one stage: only talk (+ assemble) re-render
    runs_before = be.runs
    v = await f.regenerate(view["variants"][2]["id"], stage="talk")
    assert v.status == "done" and be.runs == runs_before + 1


async def test_product_and_clip_recipes(tmp_path):
    f = make_factory(tmp_path, backend=DemoBackend())
    prod, kit = assets(f, tmp_path)
    b = f.create_batch("Statics", "product", brand_kit_id=kit.id, product_asset_id=prod.id,
                       settings={"aspects": ["4:5", "1:1"]})
    await f.plan(b.id)
    f.build_matrix(b.id, {"style": [0, 1], "headline": [0], "cta": [0]}, mode="full")
    assert (await f.run(b.id)).status == "done"
    v = f.batch_view(b.id)["variants"][0]
    assert set(v["outputs"]) == {"4:5", "1:1"}
    from PIL import Image
    assert Image.open(f.store.abs(v["outputs"]["4:5"])).size == (1080, 1350)

    c = f.create_batch("Clips", "clip", brand_kit_id=kit.id)
    await f.plan(c.id)
    f.build_matrix(c.id, {"hook": [0, 1], "style": [0], "voice": [0], "cta": [0]}, mode="full")
    est = f.estimate(c.id)
    assert est["renders"]["flux_txt2img"] == 3 and est["renders"]["wan22_i2v"] == 3  # scenes shared by hooks
    assert (await f.run(c.id)).status == "done"
    out = f.store.abs(f.batch_view(c.id)["variants"][0]["outputs"]["9:16"])
    assert media.duration(out) > 4


async def test_qa_failure_regenerates_suspect_stage(tmp_path):
    class Judge:
        def __init__(self):
            self.calls = 0

        async def parse(self, **kw):
            self.calls += 1
            bad = self.calls == 1
            rep = QAReport(score=3 if bad else 9, issues=["melted face"] if bad else [], brand_violations=[],
                           suspect_stage="avatar" if bad else "none", summary="")
            return type("R", (), {"parsed_output": rep, "stop_reason": "end_turn"})()

    judge = Judge()
    llm = type("C", (), {"beta": type("B", (), {"messages": judge})()})()
    be = DemoBackend()
    f = make_factory(tmp_path, backend=be, llm=llm)
    prod, kit = assets(f, tmp_path)
    b = f.create_batch("QA", "ugc", brand_kit_id=kit.id, product_asset_id=prod.id)
    f.set_plan(b.id, demo_plan("", Brand(), None, {}).model_dump())
    f.build_matrix(b.id, {"hook": [0], "body": [0], "cta": [0], "avatar": [0], "voice": [0]}, mode="full")
    await f.run(b.id)
    v = f.batch_view(b.id)["variants"][0]
    assert v["status"] == "done" and v["qa"]["score"] == 9 and judge.calls == 2
    assert "seed" in v["overrides"]["avatar"] and v["attempts"] == 1


async def test_failed_stage_marks_variant_not_batch(tmp_path):
    f = make_factory(tmp_path, backend=DemoBackend())
    prod, kit = assets(f, tmp_path)
    b = f.create_batch("Fail", "product", brand_kit_id=kit.id, product_asset_id=prod.id,
                       settings={"qa": False, "aspects": ["1:1"]})
    await f.plan(b.id)
    f.build_matrix(b.id, {"style": [0, 1], "headline": [0], "cta": [0]}, mode="full")
    v1 = f.store.variants(b.id)[1]
    f.store.update(type(v1), v1.id, overrides={"scene": {"fail": "always"}})
    done = await f.run(b.id)
    vs = f.store.variants(b.id)
    assert done.status == "done" and [v.status for v in vs] == ["done", "failed"]
    assert "demo failure" in vs[1].error


async def test_budget_stops_batch(tmp_path):
    class Pricey(DemoBackend):
        def spend(self):
            return 1000.0

        async def run(self, *a, **k):
            await asyncio.sleep(30)

    f = make_factory(tmp_path, backend=Pricey())
    prod, kit = assets(f, tmp_path)
    b = f.create_batch("Budget", "product", brand_kit_id=kit.id, product_asset_id=prod.id,
                       settings={"max_spend_usd": 1})
    await f.plan(b.id)
    f.build_matrix(b.id, mode="random", n=2)
    done = await asyncio.wait_for(f.run(b.id), 20)
    assert done.status == "cancelled" and "budget" in done.error
    assert all(v.status == "cancelled" for v in f.store.variants(b.id))


def test_errors_are_clear(tmp_path):
    f = make_factory(tmp_path)
    with pytest.raises(FactoryError, match="needs a product"):
        f.create_batch("x", "product")
    b = f.create_batch("x", "ugc")
    with pytest.raises(FactoryError, match="no plan"):
        f.build_matrix(b.id)
