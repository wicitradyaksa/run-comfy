"""Stage builders shared by recipes."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from ...stages import Ref, Stage
from ..assembly import compose_static, concat_scenes, hook_end, render_video_ad, scene_durations, words_for
from ..brand import Brand
from ..voice import voice_stage


def det_seed(*parts) -> int:
    """Deterministic per-choice seed: same choice -> same seed -> cache hit across variants."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def voice(ctx, text: str, voice_id: str) -> Stage:
    provider = ctx.settings.get("voice_provider", "demo")
    if provider == "comfy":
        return Stage("voice", workflow=ctx.settings.get("tts_workflow", "tts"), inputs=ctx.ov("voice", {"text": text}))
    fn = voice_stage(provider, ctx.settings.get("voice_model", ""))
    return Stage("voice", fn=fn, inputs=ctx.ov("voice", {"text": text, "voice": voice_id}), version=provider)


def words_ref(ctx):
    return Ref("voice", 1) if ctx.settings.get("voice_provider", "demo") != "comfy" else None


async def _assemble_video(sctx, inp):
    """Local stage: finished ad in every requested aspect."""
    brand = Brand(**inp["brand"])
    voice_p = Path(inp["voice"])
    words = words_for(voice_p, Path(inp["words"]) if inp.get("words") else None, inp["script"])
    main = Path(inp["main"])
    if inp.get("scenes"):
        lines = inp["scene_lines"]
        main = concat_scenes([Path(p) for p in inp["scenes"]],
                             scene_durations(lines, max(words[-1]["end"] if words else 3.0, 2.0) + 0.3),
                             sctx.out_dir / "scenes.mp4")
    broll = []
    if inp.get("broll"):
        start = min(hook_end(words, inp.get("hook", "")) + 0.2, 4.0)
        broll = [(Path(inp["broll"]), start, float(inp.get("broll_secs", 2.5)))]
    outs = []
    for aspect in inp["aspects"]:
        out = sctx.out_dir / f"ad_{aspect.replace(':', 'x')}.mp4"
        sctx.progress(len(outs) / len(inp["aspects"]), f"rendering {aspect}")
        await asyncio.to_thread(
            render_video_ad, main=main, voice=voice_p, words=words, out=out, aspect=aspect, brand=brand,
            broll=broll, music=Path(inp["music"]) if inp.get("music") else None,
            caption_style=inp.get("caption_style", "bold_pop"), cta=inp.get("cta", ""),
            logo=Path(inp["logo"]) if inp.get("logo") else None,
            endcard=Path(inp["endcard"]) if inp.get("endcard") else None, lufs=inp.get("lufs", -14.0))
        outs.append(out)
    return outs


def assemble_video(ctx, **inputs) -> Stage:
    s = ctx.settings
    a = ctx.assets
    base = {"brand": ctx.brand.model_dump(), "aspects": s.get("aspects") or ctx.brand.aspects,
            "caption_style": s.get("caption_style") or ctx.brand.caption_style or "bold_pop",
            "music": a.get("music"), "logo": a.get("logo"), "endcard": a.get("endcard"),
            "lufs": s.get("lufs", -14.0)}
    return Stage("assemble", fn=_assemble_video, inputs={**base, **inputs}, version="2", retries=0)


def _compose(sctx, inp):
    brand = Brand(**inp["brand"])
    outs = []
    for aspect in inp["aspects"]:
        out = sctx.out_dir / f"ad_{aspect.replace(':', 'x')}.jpg"
        compose_static(Path(inp["image"]), out, aspect, brand, inp["headline"], inp.get("cta", ""),
                       Path(inp["logo"]) if inp.get("logo") else None)
        outs.append(out)
    return outs


def compose_static_stage(ctx, image, headline: str, cta: str) -> Stage:
    return Stage("compose", fn=_compose, version="1", retries=0, inputs={
        "image": image, "headline": headline, "cta": cta, "brand": ctx.brand.model_dump(),
        "aspects": ctx.settings.get("aspects") or ctx.brand.aspects, "logo": ctx.assets.get("logo")})
