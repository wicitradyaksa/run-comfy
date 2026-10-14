"""UGC talking-head ad: hook + script + CTA spoken by a presenter, with product b-roll cutaways.

    voice ───────────────┐
    avatar ──> talk ─────┼──> (lipsync) ──> assemble   (all aspects, captions, music, end card)
    product ─> broll_key ─> broll ──────────┘
Avatar, b-roll and voice are cached independently, so changing the hook re-renders only voice+talk.
"""
from __future__ import annotations

from ...stages import Ref, Stage
from ..voice import DEMO_VOICES
from . import Option, Recipe, VariantCtx
from .common import assemble_video, det_seed, voice, words_ref


class UGCRecipe(Recipe):
    name = "ugc"
    title = "UGC talking-head ad"
    description = "A presenter speaks a hook, script and CTA to camera, with product b-roll cutaways."
    axes = [("hook", "Hook"), ("body", "Script"), ("cta", "CTA"), ("avatar", "Presenter"), ("voice", "Voice")]
    requires = []
    qa_stages = ["avatar", "talk", "broll"]

    def options(self, plan, assets, settings):
        avatars = [Option(f"Photo: {p.stem}", {"asset": str(p)}) for p in assets.get("avatars", [])]
        face = assets.get("face")
        if not avatars or settings.get("synthetic_presenters"):
            avatars += [Option(f"AI: {p[:48]}", {"prompt": p, "face": str(face) if face else None})
                        for p in plan.presenter_prompts]
        voices = settings.get("voices") or (DEMO_VOICES if settings.get("voice_provider", "demo") == "demo" else [])
        return {
            "hook": [Option(h, h) for h in plan.hooks],
            "body": [Option(b[:60] + "…", b) for b in plan.bodies],
            "cta": [Option(c, c) for c in plan.ctas],
            "avatar": avatars,
            "voice": [Option(v, v) for v in voices] or [Option("default", "")],
        }

    def stages(self, ctx: VariantCtx) -> list[Stage]:
        c, s = ctx.choice, ctx.settings
        script = f"{c['hook']} {c['body']} {c['cta']}"
        stages = [voice(ctx, script, c["voice"])]
        av = c["avatar"]
        if "asset" in av:
            avatar_img = av["asset"]
        else:
            wf = "avatar_pulid" if av.get("face") else "flux_txt2img"
            inp = {"prompt": av["prompt"] + ", photorealistic selfie video frame, looking at camera, vertical",
                   "seed": det_seed(ctx.seed, "avatar", av["prompt"]), "width": 768, "height": 1344}
            if av.get("face"):
                inp["face"] = av["face"]
            stages.append(Stage("avatar", workflow=wf, inputs=ctx.ov("avatar", inp)))
            avatar_img = Ref("avatar")
        stages.append(Stage("talk", workflow=s.get("talk_workflow", "wan22_s2v"), inputs=ctx.ov("talk", {
            "image": avatar_img, "audio": Ref("voice"), "seed": det_seed(ctx.seed, "talk")})))
        main = Ref("talk")
        if s.get("lipsync"):
            stages.append(Stage("lipsync", workflow="latentsync", inputs=ctx.ov("lipsync", {
                "video": Ref("talk"), "audio": Ref("voice"), "seed": det_seed(ctx.seed, "lipsync")})))
            main = Ref("lipsync")
        broll = None
        product = ctx.assets.get("product")
        if product and s.get("broll", True):
            style = s.get("broll_style") or (ctx.plan.scene_styles[0] if ctx.plan.scene_styles else "studio")
            stages.append(Stage("broll_key", workflow="flux_kontext_scene", inputs=ctx.ov("broll_key", {
                "image": product, "prompt": f"place this exact product in: {style}. Keep the product unchanged.",
                "seed": det_seed(ctx.seed, "broll_key", style)})))
            stages.append(Stage("broll", workflow="wan22_i2v", inputs=ctx.ov("broll", {
                "image": Ref("broll_key"), "prompt": ctx.plan.broll_motion, "seed": det_seed(ctx.seed, "broll"),
                "length": 73, "seconds": 3})))
            broll = Ref("broll")
        stages.append(assemble_video(ctx, main=main, voice=Ref("voice"), words=words_ref(ctx), script=script,
                                     hook=c["hook"], cta=c["cta"], broll=broll))
        return stages
