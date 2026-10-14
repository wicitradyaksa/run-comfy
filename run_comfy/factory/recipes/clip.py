"""Short-form clip: Claude's scene list -> FLUX keyframes -> Wan 2.2 motion -> VO, captions, music.

    key_i -> clip_i ─┐
    voice ───────────┴─> assemble
Keyframes depend only on scene + style, so every hook/voice variant reuses the same footage.
"""
from __future__ import annotations

from ...stages import Ref, Stage
from ..voice import DEMO_VOICES
from . import Option, Recipe, VariantCtx
from .common import assemble_video, det_seed, voice, words_ref


class ClipRecipe(Recipe):
    name = "clip"
    title = "Short-form explainer clip"
    description = "3-5 generated scenes with motion, voice-over, animated captions and music."
    axes = [("hook", "Hook"), ("style", "Visual style"), ("voice", "Voice"), ("cta", "CTA")]
    qa_stages = [f"key_{i}" for i in range(5)] + [f"clip_{i}" for i in range(5)]

    def options(self, plan, assets, settings):
        voices = settings.get("voices") or (DEMO_VOICES if settings.get("voice_provider", "demo") == "demo" else [])
        return {"hook": [Option(h, h) for h in plan.hooks],
                "style": [Option(s[:60], s) for s in plan.scene_styles],
                "voice": [Option(v, v) for v in voices] or [Option("default", "")],
                "cta": [Option(c, c) for c in plan.ctas]}

    def stages(self, ctx: VariantCtx) -> list[Stage]:
        c = ctx.choice
        scenes = ctx.plan.scenes or []
        if not scenes:
            raise ValueError("plan has no scenes - regenerate the plan or edit it to add scenes")
        script = " ".join([c["hook"], *[sc.line for sc in scenes], c["cta"]])
        stages = [voice(ctx, script, c["voice"])]
        refs = []
        for i, sc in enumerate(scenes):
            stages.append(Stage(f"key_{i}", workflow="flux_txt2img", inputs=ctx.ov(f"key_{i}", {
                "prompt": f"{sc.visual}. Style: {c['style']}. Vertical 9:16, no text.",
                "seed": det_seed(ctx.seed, "key", i, c["style"]), "width": 720, "height": 1280})))
            stages.append(Stage(f"clip_{i}", workflow="wan22_i2v", inputs=ctx.ov(f"clip_{i}", {
                "image": Ref(f"key_{i}"), "prompt": sc.motion, "seed": det_seed(ctx.seed, "clip", i, c["style"]),
                "length": 97, "seconds": 4})))
            refs.append(Ref(f"clip_{i}"))
        # each scene stays on screen while its share of the VO is spoken (hook opens, CTA closes)
        lines = [sc.line for sc in scenes]
        lines[0] = f"{c['hook']} {lines[0]}"
        lines[-1] = f"{lines[-1]} {c['cta']}"
        stages.append(assemble_video(ctx, main=refs[0], scenes=refs, scene_lines=lines, voice=Ref("voice"),
                                     words=words_ref(ctx), script=script, hook=c["hook"], cta=c["cta"]))
        return stages
