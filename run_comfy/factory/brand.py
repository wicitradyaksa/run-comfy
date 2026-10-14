"""Brand kit: everything the writer, assembly and QA need to stay on-brand."""
from __future__ import annotations

from pydantic import BaseModel, Field

ASPECTS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}


class Brand(BaseModel):
    name: str = "My Brand"
    product: str = ""                      # one line: what it is
    audience: str = ""                     # who it's for
    facts: list[str] = Field(default_factory=list)    # true, approved claims the writer may use
    banned: list[str] = Field(default_factory=list)   # words/claims that must never appear
    tone: str = "friendly, direct, conversational UGC"
    language: str = "English"
    colors: list[str] = Field(default_factory=lambda: ["#111111", "#ffffff", "#7c3aed"])  # bg, fg, accent
    cta_default: str = "Shop now"
    logo_asset_id: int | None = None
    endcard_asset_id: int | None = None
    music_asset_id: int | None = None
    caption_style: str | None = None       # overrides [factory].caption_style
    aspects: list[str] = Field(default_factory=lambda: ["9:16", "1:1", "16:9"])

    @property
    def bg(self) -> str:
        return self.colors[0] if self.colors else "#111111"

    @property
    def fg(self) -> str:
        return self.colors[1] if len(self.colors) > 1 else "#ffffff"

    @property
    def accent(self) -> str:
        return self.colors[2] if len(self.colors) > 2 else "#facc15"

    def violations(self, text: str) -> list[str]:
        low = text.lower()
        return [b for b in self.banned if b and b.lower() in low]
