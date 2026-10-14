"""Demo backend: fabricates plausible media for each workflow kind with ffmpeg/Pillow.

Lets the whole factory, dashboard and tests run end to end with no GPU and no API keys. The outputs
are obviously placeholders (cards with the prompt text, Ken Burns motion, tones for speech) but they
have the real shapes: right durations, sizes, audio tracks, word timings.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import struct
import wave
from pathlib import Path

from .. import media
from ..registry import Workflow
from .base import Backend, Progress, StageFailed

PALETTE = ["#1f2937", "#7c3aed", "#0ea5e9", "#059669", "#d97706", "#db2777", "#4f46e5", "#0f766e"]


def _colour(seed: str, k: int = 0) -> str:
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    return PALETTE[(h + k) % len(PALETTE)]


def estimate_words(text: str, total: float, start: float = 0.15) -> list[dict]:
    """Spread words over `total` seconds proportionally to their length (fallback timing)."""
    words = text.split()
    if not words:
        return []
    weights = [len(w) + 2 for w in words]
    span = max(total - start - 0.2, 0.3)
    t, out = start, []
    for w, wt in zip(words, weights):
        d = span * wt / sum(weights)
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


def synth_speech(text: str, out: Path, sr: int = 24000) -> tuple[Path, list[dict]]:
    """A syllable-ish tone per word so captions, ducking and loudness have something real to work on."""
    words = text.split() or ["..."]
    secs = 0.3 + sum(0.12 + 0.055 * len(w) for w in words) + 0.3
    timings = estimate_words(text, secs)
    frames = bytearray()
    n = int(secs * sr)
    spans = [(int(w["start"] * sr), int(w["end"] * sr), 140 + (len(w["word"]) * 23) % 120) for w in timings]
    j = 0
    for i in range(n):
        while j < len(spans) and i >= spans[j][1]:
            j += 1
        v = 0.0
        if j < len(spans) and spans[j][0] <= i < spans[j][1]:
            a, b, f = spans[j]
            env = math.sin(math.pi * (i - a) / max(b - a, 1))
            v = 0.45 * env * (math.sin(2 * math.pi * f * i / sr) + 0.3 * math.sin(4 * math.pi * f * i / sr))
        frames += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return out, timings


class DemoBackend(Backend):
    name = "demo"
    rate_per_s = 0.0

    def __init__(self, delay_s: float = 0.0):
        self.delay_s = delay_s
        self.runs = 0

    async def run(self, wf: Workflow, values: dict, out_dir: Path, progress: Progress | None = None) -> list[Path]:
        media.require_ffmpeg()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.runs += 1
        if self.delay_s:
            for i in range(4):
                if progress:
                    progress(i / 4, "rendering")
                await asyncio.sleep(self.delay_s / 4)
        if values.get("fail") == "always":
            raise StageFailed(f"demo failure requested for {wf.name}")
        fn = getattr(self, f"_{wf.kind}", None)
        if fn is None:
            raise StageFailed(f"demo backend can't fake workflow kind {wf.kind!r}")
        result = await asyncio.to_thread(fn, wf, values, out_dir)
        if progress:
            progress(1.0, "done")
        return result

    # ---- per kind ----
    def _size(self, v, dw=768, dh=1344):
        return int(v.get("width") or dw), int(v.get("height") or dh)

    def _image(self, wf, v, out):
        w, h = self._size(v)
        prompt = str(v.get("prompt", wf.name))
        seed = f"{prompt}{v.get('seed', '')}"
        return [media.text_card(out / "image.png", w, h, prompt[:140], f"{wf.name} · seed {v.get('seed', '-')}",
                                bg=_colour(seed), gradient_to=_colour(seed, 3))]

    def _avatar(self, wf, v, out):
        return self._image(wf, v, out)

    def _edit(self, wf, v, out):
        from PIL import Image, ImageDraw, ImageEnhance

        src = Image.open(v["image"]).convert("RGB")
        w, h = self._size(v, src.width, src.height)
        img = src.resize((w, h))
        tint = Image.new("RGB", img.size, media.hex_rgb(_colour(str(v.get("prompt", "")))))
        img = Image.blend(ImageEnhance.Contrast(img).enhance(1.1), tint, 0.25)
        ImageDraw.Draw(img).text((20, 20), str(v.get("prompt", ""))[:80], fill=(255, 255, 255))
        img.save(out / "image.png")
        return [out / "image.png"]

    def _bg_remove(self, wf, v, out):
        from PIL import Image

        Image.open(v["image"]).convert("RGBA").save(out / "cutout.png")
        return [out / "cutout.png"]

    def _upscale(self, wf, v, out):
        from PIL import Image

        img = Image.open(v["image"])
        img.resize((img.width * 2, img.height * 2)).save(out / "upscaled.png")
        return [out / "upscaled.png"]

    def _tts(self, wf, v, out):
        wav, words = synth_speech(str(v["text"]), out / "voice.wav")
        (out / "voice.words.json").write_text(json.dumps(words))
        return [wav, out / "voice.words.json"]

    def _talking(self, wf, v, out):
        w, h = self._size(v, 720, 1280)
        secs = media.duration(Path(v["audio"]))
        return [media.ken_burns(Path(v["image"]), out / "talking.mp4", secs, w, h, audio=Path(v["audio"]))]

    def _lipsync(self, wf, v, out):
        media.ffmpeg("-i", str(v["video"]), "-i", str(v["audio"]), "-map", "0:v", "-map", "1:a",
                     "-c:v", "copy", "-c:a", "aac", "-shortest", str(out / "lipsync.mp4"))
        return [out / "lipsync.mp4"]

    def _i2v(self, wf, v, out):
        w, h = self._size(v, 720, 1280)
        secs = float(v.get("seconds") or 4)
        return [media.ken_burns(Path(v["image"]), out / "clip.mp4", secs, w, h)]

    def _t2v(self, wf, v, out):
        img = self._image(wf, v, out)[0]
        w, h = self._size(v, 720, 1280)
        return [media.ken_burns(img, out / "clip.mp4", float(v.get("seconds") or 4), w, h)]
