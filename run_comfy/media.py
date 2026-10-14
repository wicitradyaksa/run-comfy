"""Small ffmpeg / ffprobe / Pillow helpers shared by the demo backend, assembly and QA."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class MediaError(RuntimeError):
    pass


def require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise MediaError("ffmpeg/ffprobe not found - install ffmpeg")


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise MediaError(f"{cmd[0]} failed: {r.stderr.strip()[-1500:]}")
    return r


def ffmpeg(*args: str) -> None:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args])


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    return json.loads(r.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"].get("duration") or 0)


def has_stream(path: Path, kind: str) -> bool:
    return any(s.get("codec_type") == kind for s in probe(path).get("streams", []))


def video_size(path: Path) -> tuple[int, int]:
    for s in probe(path)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise MediaError(f"{path} has no video stream")


def loudness(path: Path) -> float | None:
    """Integrated loudness in LUFS (None if no audio)."""
    if not has_stream(path, "audio"):
        return None
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None


def extract_frames(path: Path, out_dir: Path, n: int = 4) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = max(duration(path), 0.1)
    frames = []
    for i in range(n):
        t = d * (i + 0.5) / n
        f = out_dir / f"frame_{i}.jpg"
        ffmpeg("-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "3", str(f))
        frames.append(f)
    return frames


def poster(video: Path, at: float = 1.0) -> Path:
    """Save <video>.jpg next to the video: instant gallery thumbnails, and a fallback where H.264 can't play."""
    out = video.with_suffix(".jpg")
    t = min(at, max(duration(video) - 0.1, 0))
    ffmpeg("-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out))
    return out


def font_path() -> str | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def _font(size: int):
    from PIL import ImageFont

    fp = font_path()
    return ImageFont.truetype(fp, size) if fp else ImageFont.load_default()


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (20, 20, 20)


def text_card(out: Path, w: int, h: int, title: str, subtitle: str = "", bg: str = "#111111",
              fg: str = "#ffffff", logo: Path | None = None, gradient_to: str | None = None) -> Path:
    """Solid/gradient card with centred text (+ optional logo). Used for demo images and end cards."""
    from PIL import Image, ImageDraw

    a, b = hex_rgb(bg), hex_rgb(gradient_to or bg)
    img = Image.new("RGB", (w, h), a)
    if gradient_to:
        px = img.load()
        for y in range(h):
            t = y / max(h - 1, 1)
            c = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
            for x in range(w):
                px[x, y] = c
    d = ImageDraw.Draw(img)
    y = h * 0.38
    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lg.thumbnail((int(w * 0.4), int(h * 0.15)))
        img.paste(lg, ((w - lg.width) // 2, int(h * 0.18)), lg)
    for text, size in ((title, max(28, w // 14)), (subtitle, max(18, w // 26))):
        if not text:
            continue
        f = _font(size)
        chars = max(10, int(w / (size * 0.55)))
        for line in textwrap.wrap(text, chars)[:6]:
            tw = d.textlength(line, font=f)
            d.text(((w - tw) / 2, y), line, font=f, fill=hex_rgb(fg))
            y += size * 1.25
        y += size * 0.6
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def cover_filter(w: int, h: int) -> str:
    """Scale + centre-crop to exactly w x h (like CSS object-fit: cover)."""
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def ken_burns(image: Path, out: Path, seconds: float, w: int, h: int, fps: int = 30, audio: Path | None = None):
    """Slow zoom over a still image -> mp4 (optionally with an audio track for the same length)."""
    frames = max(1, int(seconds * fps))
    vf = (f"{cover_filter(int(w * 1.25) // 2 * 2, int(h * 1.25) // 2 * 2)},zoompan=z='min(zoom+0.0008,1.12)':d={frames}"
          f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},format=yuv420p")
    args = ["-loop", "1", "-i", str(image)]
    if audio:
        args += ["-i", str(audio)]
    args += ["-vf", vf, "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", os.environ.get("RUN_COMFY_X264_PRESET", "veryfast"), "-crf", "20"]
    args += (["-c:a", "aac", "-b:a", "192k", "-shortest"] if audio else ["-an"])
    ffmpeg(*args, str(out))
    return out
