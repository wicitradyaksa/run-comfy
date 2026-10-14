"""Final ad assembly with ffmpeg: cover-crop to each aspect, b-roll cutaways, animated captions,
music ducked under the voice, loudness-normalised mix, branded end card. Plus static ad compositing."""
from __future__ import annotations

import math
import os
import shutil
import textwrap
from pathlib import Path

from .. import media
from .brand import ASPECTS, Brand
from .captions import build_ass, load_words

FPS = 30
# RUN_COMFY_X264_PRESET=ultrafast for previews/tests; "medium" gives smaller, cleaner final files
VENC = ["-c:v", "libx264", "-preset", os.environ.get("RUN_COMFY_X264_PRESET", "medium"), "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-movflags", "+faststart"]
AENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def endcard_image(brand: Brand, w: int, h: int, cta: str, out: Path, logo: Path | None = None) -> Path:
    return media.text_card(out, w, h, cta, brand.name, bg=brand.bg, fg=brand.fg, logo=logo,
                           gradient_to=brand.accent)


def _endcard_clip(img: Path, w: int, h: int, secs: float, out: Path) -> Path:
    media.ffmpeg("-loop", "1", "-t", f"{secs}", "-i", str(img), "-f", "lavfi", "-t", f"{secs}",
                 "-i", "anullsrc=r=48000:cl=stereo",
                 "-vf", f"{media.cover_filter(w, h)},fade=t=in:st=0:d=0.25,format=yuv420p",
                 *VENC, *AENC, "-shortest", str(out))
    return out


def render_video_ad(*, main: Path, voice: Path, words: list[dict], out: Path, aspect: str, brand: Brand,
                    broll: list[tuple[Path, float, float]] = (), music: Path | None = None,
                    caption_style: str = "bold_pop", endcard: Path | None = None, cta: str = "",
                    logo: Path | None = None, lufs: float = -14.0, endcard_secs: float = 1.8) -> Path:
    """One finished ad for one aspect ratio. `broll` = [(clip, start_s, dur_s)] cutaways over `main`."""
    w, h = ASPECTS[aspect]
    work = out.parent / f".work_{out.stem}"
    work.mkdir(parents=True, exist_ok=True)
    vo_len = media.duration(voice)
    total = vo_len + 0.25

    inputs = ["-i", str(main), "-i", str(voice)]
    filt = [f"[0:v]{media.cover_filter(w, h)},fps={FPS},format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration={total:.2f}[base]"]
    last = "base"
    for i, (clip, start, dur) in enumerate(broll):
        inputs += ["-i", str(clip)]
        idx = 2 + i
        filt.append(f"[{idx}:v]{media.cover_filter(w, h)},fps={FPS},trim=0:{dur:.2f},"
                    f"setpts=PTS-STARTPTS+{start:.2f}/TB[b{i}]")
        filt.append(f"[{last}][b{i}]overlay=eof_action=pass:enable='between(t,{start:.2f},{start + dur:.2f})'[v{i}]")
        last = f"v{i}"
    if caption_style and caption_style != "none" and words:
        ass = build_ass(words, work / "captions.ass", w, h, caption_style, accent=brand.accent, fg="#ffffff")
        filt.append(f"[{last}]ass=captions.ass[vc]")
        last = "vc"
    filt.append("[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad=pad_dur=0.25[vo]")
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        m = 2 + len(broll)
        filt += ["[vo]asplit=2[vo1][vo2]",
                 f"[{m}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.30[mus]",
                 "[mus][vo1]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck]",
                 "[vo2][duck]amix=inputs=2:duration=first:normalize=0[mix]"]
        amix = "mix"
    else:
        amix = "vo"
    filt.append(f"[{amix}]loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[a]")
    body = work / "body.mp4"
    # run inside `work` so the ass filter can use a bare relative filename (no escaping issues)
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *_abs_inputs(inputs),
               "-filter_complex", ";".join(filt), "-map", f"[{last}]", "-map", "[a]", "-t", f"{total:.3f}",
               *VENC, *AENC, str(body.resolve())], cwd=work)
    parts = [body]
    if endcard_secs > 0:
        img = endcard if endcard else endcard_image(brand, w, h, cta or brand.cta_default, work / "endcard.png", logo)
        parts.append(_endcard_clip(Path(img), w, h, endcard_secs, work / "endcard.mp4"))
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    media.ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out))
    shutil.rmtree(work, ignore_errors=True)
    return out


def _abs_inputs(args: list[str]) -> list[str]:
    out = []
    for i, a in enumerate(args):
        out.append(str(Path(a).resolve()) if i > 0 and args[i - 1] == "-i" else a)
    return out


def concat_scenes(clips: list[Path], durations: list[float], out: Path, w: int = 720, h: int = 1280) -> Path:
    """Normalise scene clips and cut each to its share of the voice-over, then join them."""
    inputs, filt = [], []
    for i, (c, d) in enumerate(zip(clips, durations)):
        inputs += ["-i", str(c)]
        filt.append(f"[{i}:v]{media.cover_filter(w, h)},fps={FPS},tpad=stop_mode=clone:stop_duration={d:.2f},"
                    f"trim=0:{d:.2f},setpts=PTS-STARTPTS[s{i}]")
    filt.append("".join(f"[s{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]")
    media.ffmpeg(*inputs, "-filter_complex", ";".join(filt), "-map", "[v]", *VENC, "-an", str(out))
    return out


def scene_durations(lines: list[str], total: float, min_s: float = 1.2) -> list[float]:
    weights = [max(len(x.split()), 1) for x in lines]
    raw = [total * wt / sum(weights) for wt in weights]
    return [max(min_s, r) for r in raw]


def compose_static(image: Path, out: Path, aspect: str, brand: Brand, headline: str, cta: str,
                   logo: Path | None = None) -> Path:
    """Static ad: cover-crop, bottom gradient, headline, CTA pill, logo."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = ASPECTS[aspect]
    src = Image.open(image).convert("RGB")
    scale = max(w / src.width, h / src.height)
    src = src.resize((math.ceil(src.width * scale), math.ceil(src.height * scale)), Image.LANCZOS)
    left, top = (src.width - w) // 2, (src.height - h) // 2
    img = src.crop((left, top, left + w, top + h)).convert("RGBA")
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    start = int(h * 0.55)
    for y in range(start, h):
        a = int(200 * (y - start) / (h - start))
        gd.line([(0, y), (w, y)], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img, grad)
    d = ImageDraw.Draw(img)
    fp = media.font_path()
    hsize = int(w * (0.085 if h >= w else 0.06))
    font = ImageFont.truetype(fp, hsize) if fp else ImageFont.load_default()
    lines = textwrap.wrap(headline, max(8, int(w / (hsize * 0.58))))[:3]
    y = h - int(h * 0.1) - hsize * 2 - len(lines) * hsize * 1.15
    for line in lines:
        d.text((int(w * 0.07), y), line, font=font, fill=media.hex_rgb("#ffffff"))
        y += hsize * 1.15
    if cta:
        csize = int(hsize * 0.5)
        cfont = ImageFont.truetype(fp, csize) if fp else ImageFont.load_default()
        tw = d.textlength(cta, font=cfont)
        x0, y0 = int(w * 0.07), int(y + hsize * 0.4)
        d.rounded_rectangle([x0, y0, x0 + tw + csize * 1.6, y0 + csize * 2], radius=csize,
                            fill=media.hex_rgb(brand.accent))
        d.text((x0 + csize * 0.8, y0 + csize * 0.45), cta, font=cfont, fill=media.hex_rgb(brand.bg))
    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lg.thumbnail((int(w * 0.22), int(h * 0.08)))
        img.alpha_composite(lg, (int(w * 0.06), int(h * 0.04)))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=92)
    return out


def words_for(voice: Path, words_json: Path | None, text: str) -> list[dict]:
    return load_words(voice, words_json, text)


def hook_end(words: list[dict], hook: str) -> float:
    n = len(hook.split())
    if words and n and len(words) >= n:
        return float(words[n - 1]["end"])
    return 2.0
