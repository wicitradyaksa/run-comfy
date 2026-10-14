"""Creative writer: brief / product URL -> hooks, scripts, CTAs, presenter + scene prompts.

Uses Claude with structured outputs so the plan is always valid JSON matching CreativePlan.
Without an ANTHROPIC_API_KEY it falls back to a deterministic template writer (demo mode).
"""
from __future__ import annotations

import html
import logging
import os
import re

import httpx
from pydantic import BaseModel, Field

from .brand import Brand

log = logging.getLogger("run_comfy")


class Scene(BaseModel):
    visual: str = Field(description="Keyframe image prompt: subject, setting, lighting, lens, vertical framing")
    motion: str = Field(description="Camera/subject motion for image-to-video, one short phrase")
    line: str = Field(description="Voice-over line spoken during this scene")


class CreativePlan(BaseModel):
    angle: str = Field(description="The core marketing angle in one sentence")
    hooks: list[str] = Field(description="Scroll-stopping first lines, max 12 words each, all different angles")
    bodies: list[str] = Field(description="Spoken script bodies, 25-45 words, natural UGC voice, no hook/CTA")
    ctas: list[str] = Field(description="Closing calls to action, max 8 words")
    headlines: list[str] = Field(description="Short on-image headlines for static ads, max 6 words")
    presenter_prompts: list[str] = Field(description="Photoreal presenter descriptions for a selfie-style "
                                                     "talking-head video (age, look, wardrobe, setting)")
    scene_styles: list[str] = Field(description="Settings / art direction for product shots and b-roll")
    broll_motion: str = Field(description="Image-to-video motion prompt for product b-roll")
    scenes: list[Scene] = Field(description="3-5 scenes for a short-form explainer clip")


SYSTEM = """You are a senior direct-response creative strategist who writes short-form video ads \
(TikTok, Reels, Shorts) and static social ads that convert.

Rules:
- Only state product facts that appear in the brief or the approved facts list. Never invent prices, \
statistics, awards, medical or financial claims.
- Never use any banned word or claim, in any form.
- Hooks must stop the scroll in under 2 seconds: a pattern interrupt, a bold claim backed by the facts, \
a relatable problem, or curiosity. Make every hook a genuinely different angle, not a rephrasing.
- Bodies are spoken aloud by a real-looking person filming themselves: contractions, short sentences, \
no jargon, no emojis, no hashtags, no stage directions.
- Image prompts are concrete and photographic (subject, setting, light, lens), vertical 9:16 framing, \
no text in the image, no logos.
- Write in {language}."""


def fetch_page(url: str, max_chars: int = 12000, transport: httpx.BaseTransport | None = None) -> dict:
    """Pull title, description and visible text from a product page (best effort)."""
    with httpx.Client(timeout=20, follow_redirects=True, transport=transport,
                      headers={"User-Agent": "Mozilla/5.0 (run-comfy brief fetcher)"}) as c:
        r = c.get(url)
        r.raise_for_status()
        raw = r.text

    def meta(name):
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']+)', raw, re.I)
        return html.unescape(m.group(1)) if m else ""

    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    body = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    body = re.sub(r"\s+", " ", body).strip()
    return {"url": url, "title": html.unescape(title.group(1).strip()) if title else "",
            "description": meta("og:description") or meta("description"),
            "image": meta("og:image"), "text": body[:max_chars]}


def _prompt(brief: str, brand: Brand, page: dict | None, counts: dict) -> str:
    parts = [f"Brand: {brand.name}", f"Product: {brand.product}", f"Audience: {brand.audience}",
             f"Tone: {brand.tone}"]
    if brand.facts:
        parts.append("Approved facts:\n" + "\n".join(f"- {f}" for f in brand.facts))
    if brand.banned:
        parts.append("Banned words/claims (never use): " + ", ".join(brand.banned))
    if page:
        parts.append(f"Product page ({page['url']}):\nTitle: {page['title']}\nDescription: {page['description']}\n"
                     f"Page text:\n{page['text']}")
    parts.append(f"Brief:\n{brief or '(none - infer from the product)'}")
    parts.append("Write: " + ", ".join(f"{v} {k}" for k, v in counts.items()) +
                 f". Default CTA if unsure: {brand.cta_default!r}.")
    return "\n\n".join(parts)


def _clean(plan: CreativePlan, brand: Brand, counts: dict) -> CreativePlan:
    """Guardrails after generation: drop lines with banned terms, enforce counts/lengths."""
    def keep(items, max_words):
        out = []
        for s in items:
            s = re.sub(r"\s+", " ", s).strip().strip('"')
            if s and not brand.violations(s) and len(s.split()) <= max_words and s not in out:
                out.append(s)
        return out

    plan.hooks = keep(plan.hooks, 16)[: counts["hooks"]]
    plan.bodies = keep(plan.bodies, 70)[: counts["bodies"]]
    plan.ctas = keep(plan.ctas, 10)[: counts["ctas"]] or [brand.cta_default]
    plan.headlines = keep(plan.headlines, 8)[: counts["headlines"]]
    plan.presenter_prompts = plan.presenter_prompts[: counts["presenter_prompts"]]
    plan.scene_styles = plan.scene_styles[: counts["scene_styles"]]
    plan.scenes = [s for s in plan.scenes if not brand.violations(s.line)][:5]
    if not plan.hooks or not plan.bodies:
        raise ValueError("writer produced no usable hooks/bodies after brand guardrails")
    return plan


DEFAULT_COUNTS = {"hooks": 4, "bodies": 2, "ctas": 2, "headlines": 4, "presenter_prompts": 2,
                  "scene_styles": 3}


async def write_plan(brief: str, brand: Brand, *, url: str | None = None, counts: dict | None = None,
                     model: str = "claude-opus-5-5", client=None, page: dict | None = None) -> CreativePlan:
    counts = {**DEFAULT_COUNTS, **(counts or {})}
    if url and page is None:
        try:
            page = fetch_page(url)
        except httpx.HTTPError as e:
            log.warning("could not fetch %s: %s", url, e)
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("no ANTHROPIC_API_KEY - using the demo writer")
        return _clean(demo_plan(brief, brand, page, counts), brand, counts)
    if client is None:
        import anthropic

        client = anthropic.AsyncAnthropic()
    resp = await client.beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM.format(language=brand.language),
        messages=[{"role": "user", "content": _prompt(brief, brand, page, counts)}],
        output_format=CreativePlan,
        output_config={"effort": "medium"},
        # if the primary model declines, the API re-runs the request on a fallback model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise ValueError(f"writer declined or returned nothing (stop_reason={resp.stop_reason})")
    return _clean(resp.parsed_output, brand, counts)


def demo_plan(brief: str, brand: Brand, page: dict | None, counts: dict) -> CreativePlan:
    """Deterministic, keyless plan so the whole pipeline is demoable offline."""
    p = brand.product or (page or {}).get("title") or brand.name
    fact = brand.facts[0] if brand.facts else f"{p} makes it easy"
    hooks = [f"Stop scrolling if you've ever struggled with {brand.audience or 'this'}",
             f"I didn't believe the hype about {p} until this",
             f"Three reasons {p} lives in my bag now",
             f"Nobody talks about this {p} trick",
             f"POV: you finally found {p}", f"This changed my morning routine"]
    bodies = [f"So I've been using {p} for a few weeks. {fact}. It's honestly the first thing that "
              f"actually fit into my day without any extra effort, and I've already told two friends.",
              f"Here's the thing. Most options are complicated. {p} is not. {fact}. "
              f"I just use it and get on with my day, which is exactly what I wanted."]
    return CreativePlan(
        angle=f"{p}: effortless results for {brand.audience or 'busy people'}",
        hooks=hooks, bodies=bodies,
        ctas=[brand.cta_default, f"Try {brand.name} today"],
        headlines=[f"Meet {p}", "Made for real life", "Less effort. More results.", "Your new daily essential"],
        presenter_prompts=["woman in her late 20s, natural makeup, oversized beige sweater, bright apartment, "
                           "holding phone at arm's length, soft window light",
                           "man in his 30s, short beard, navy t-shirt, modern kitchen, morning light, selfie angle"],
        scene_styles=["sunlit marble countertop, soft morning shadows, minimal props",
                      "moody studio, deep teal backdrop, rim light, glossy reflections",
                      "outdoor picnic blanket, golden hour, shallow depth of field"],
        broll_motion="slow cinematic push-in with gentle parallax",
        scenes=[Scene(visual=f"close-up of {p} on a clean desk, morning light, 35mm", motion="slow push-in",
                      line=f"This is {p}."),
                Scene(visual="person smiling while using the product in a bright kitchen, candid",
                      motion="handheld follow", line=fact + "."),
                Scene(visual=f"{p} in a bag next to keys and sunglasses, top-down", motion="slow rotate",
                      line="It goes everywhere with me.")],
    )
