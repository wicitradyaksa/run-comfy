"""Recipes turn one variant (a combination of creative choices) into a stage DAG ending in finished ads."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..brand import Brand
from ..writer import CreativePlan


@dataclass
class Option:
    label: str          # what the dashboard shows
    value: Any          # what the recipe uses (text, {"asset": path}, {"prompt": ...}, voice id ...)


@dataclass
class VariantCtx:
    brand: Brand
    plan: CreativePlan
    choice: dict[str, Any]                 # axis -> Option.value for this variant
    assets: dict[str, Path | None]         # product, music, logo, endcard, face
    settings: dict                         # aspects, caption_style, voice_provider, talk_workflow, ...
    overrides: dict[str, dict] = field(default_factory=dict)  # stage -> input overrides (seed bumps, tweaks)
    seed: int = 0

    def ov(self, stage: str, inputs: dict) -> dict:
        return {**inputs, **self.overrides.get(stage, {})}


class Recipe:
    name = ""
    title = ""
    description = ""
    output = "video"                        # video | image
    axes: list[tuple[str, str]] = []        # (axis key, label)
    requires: list[str] = []                # asset kinds that must be provided
    qa_stages: list[str] = []               # stages QA may blame and regenerate

    def options(self, plan: CreativePlan, assets: dict, settings: dict) -> dict[str, list[Option]]:
        raise NotImplementedError

    def stages(self, ctx: VariantCtx) -> list:
        raise NotImplementedError

    def public(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description, "output": self.output,
                "axes": [{"key": k, "label": lbl} for k, lbl in self.axes], "requires": self.requires,
                "qa_stages": self.qa_stages}


def get_recipes() -> dict[str, Recipe]:
    from .clip import ClipRecipe
    from .product import ProductRecipe
    from .ugc import UGCRecipe

    return {r.name: r for r in (UGCRecipe(), ProductRecipe(), ClipRecipe())}
