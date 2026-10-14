"""Static product ads: product photo restaged in new scenes, upscaled, composed with headline + CTA.

    product -> (cutout) -> scene -> upscale -> compose (every aspect)
"""
from __future__ import annotations

from ...stages import Ref, Stage
from . import Option, Recipe, VariantCtx
from .common import compose_static_stage, det_seed


class ProductRecipe(Recipe):
    name = "product"
    title = "Product visuals"
    description = "Your product photo restaged in on-brand scenes, upscaled, with headline + CTA in every aspect."
    output = "image"
    axes = [("style", "Scene"), ("headline", "Headline"), ("cta", "CTA")]
    requires = ["product"]
    qa_stages = ["scene"]

    def options(self, plan, assets, settings):
        return {"style": [Option(s[:60], s) for s in plan.scene_styles],
                "headline": [Option(h, h) for h in plan.headlines],
                "cta": [Option(c, c) for c in plan.ctas]}

    def stages(self, ctx: VariantCtx) -> list[Stage]:
        c, s = ctx.choice, ctx.settings
        product = ctx.assets["product"]
        stages = []
        if s.get("cutout"):
            stages.append(Stage("cutout", workflow="bg_remove", inputs=ctx.ov("cutout", {"image": product})))
            product = Ref("cutout")
        stages.append(Stage("scene", workflow="flux_kontext_scene", inputs=ctx.ov("scene", {
            "image": product, "seed": det_seed(ctx.seed, "scene", c["style"]),
            "prompt": f"Place this exact product in: {c['style']}. Keep the product, label and shape unchanged. "
                      "Professional advertising photograph, leave clean space at the bottom."})))
        image = Ref("scene")
        if s.get("upscale", True):
            stages.append(Stage("upscale", workflow="upscale_2x", inputs=ctx.ov("upscale", {"image": Ref("scene")})))
            image = Ref("upscale")
        stages.append(compose_static_stage(ctx, image, c["headline"], c["cta"]))
        return stages
