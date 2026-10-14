"""Animated burned-in captions (TikTok / CapCut style) as ASS subtitles.

Word timings come from the TTS provider when it has them, else faster-whisper word alignment,
else an even estimate. Styles:
- bold_pop:  2-3 word chunks, uppercase, heavy outline, the spoken word pops in the accent colour
- karaoke:   one line per chunk, fill sweeps across words as they are spoken
- minimal:   clean sentence-case subtitles
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pysubs2

from ..backends.demo import estimate_words
from ..media import duration, hex_rgb

log = logging.getLogger("run_comfy")


def whisper_words(audio: Path, model_size: str = "small") -> list[dict] | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        model = WhisperModel(model_size, device="auto", compute_type="int8")
        segments, _ = model.transcribe(str(audio), word_timestamps=True, vad_filter=True)
        return [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
                for seg in segments for w in (seg.words or []) if w.word.strip()]
    except Exception as e:  # model download blocked, no CPU support...
        log.warning("whisper alignment unavailable (%s) - estimating word timings", e)
        return None


def load_words(audio: Path, words_json: Path | None, text: str, use_whisper: bool = True) -> list[dict]:
    if words_json and Path(words_json).exists():
        w = json.loads(Path(words_json).read_text())
        if w:
            return w
    if use_whisper:
        w = whisper_words(audio)
        if w:
            return w
    return estimate_words(text, duration(audio))


def chunk(words: list[dict], max_words: int = 3, max_secs: float = 1.4, gap: float = 0.35) -> list[list[dict]]:
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["end"] - cur[0]["start"] > max_secs
                    or w["start"] - cur[-1]["end"] > gap or cur[-1]["word"][-1:] in ".!?,;:"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _ass_colour(hex_colour: str, alpha: int = 0) -> pysubs2.Color:
    r, g, b = hex_rgb(hex_colour)
    return pysubs2.Color(r, g, b, alpha)


def _tag_colour(hex_colour: str) -> str:
    r, g, b = hex_rgb(hex_colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def build_ass(words: list[dict], out: Path, w: int, h: int, style: str = "bold_pop", accent: str = "#facc15",
              fg: str = "#ffffff", font: str = "Inter", offset: float = 0.0) -> Path:
    subs = pysubs2.SSAFile()
    subs.info.update({"PlayResX": str(w), "PlayResY": str(h), "ScaledBorderAndShadow": "yes", "WrapStyle": "2"})
    shape = "vertical" if h > w * 1.1 else "square" if h >= w * 0.9 else "landscape"
    big = {"vertical": 0.078, "square": 0.066, "landscape": 0.045}[shape]
    small = {"vertical": 0.05, "square": 0.045, "landscape": 0.032}[shape]
    size = int(w * (small if style == "minimal" else big))
    margin_v = int(h * {"vertical": 0.24, "square": 0.12, "landscape": 0.10}[shape])
    subs.styles["Default"] = pysubs2.SSAStyle(
        fontname=font, fontsize=size, bold=True, primarycolor=_ass_colour(fg), secondarycolor=_ass_colour("#888888"),
        outlinecolor=_ass_colour("#000000"), backcolor=_ass_colour("#000000", 120),
        outline=max(2, size // 9) if style != "minimal" else 2, shadow=2 if style != "minimal" else 0,
        alignment=pysubs2.Alignment.BOTTOM_CENTER, marginv=margin_v, marginl=int(w * 0.08), marginr=int(w * 0.08))
    ms = lambda t: int(round((t + offset) * 1000))  # noqa: E731
    for group in chunk(words, max_words=3 if style != "minimal" else 7, max_secs=1.4 if style != "minimal" else 3.0):
        g_end = group[-1]["end"] + 0.12
        texts = [x["word"].upper() if style == "bold_pop" else x["word"] for x in group]
        if style == "karaoke":
            parts = []
            for i, x in enumerate(group):
                nxt = group[i + 1]["start"] if i + 1 < len(group) else g_end
                parts.append(f"{{\\kf{max(1, int((nxt - x['start']) * 100))}}}{texts[i]} ")
            subs.events.append(pysubs2.SSAEvent(start=ms(group[0]["start"]), end=ms(g_end),
                                                text="{\\1c" + _tag_colour(accent) + "\\2c" + _tag_colour(fg) + "}"
                                                + "".join(parts).strip()))
        elif style == "bold_pop":
            for i, x in enumerate(group):
                end = group[i + 1]["start"] if i + 1 < len(group) else g_end
                line = " ".join(
                    (f"{{\\c{_tag_colour(accent)}\\fscx112\\fscy112}}{t}{{\\r}}" if j == i else t)
                    for j, t in enumerate(texts))
                pop = "{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
                subs.events.append(pysubs2.SSAEvent(start=ms(x["start"]), end=ms(end), text=pop + line))
        else:
            subs.events.append(pysubs2.SSAEvent(start=ms(group[0]["start"]), end=ms(g_end), text=" ".join(texts)))
    out.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out))
    return out
