"""Local ffmpeg post-processing."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("run_comfy")
VIDEO = {".mp4", ".webm", ".mov", ".mkv"}


def _has_audio(p: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def concat(files: list[Path], out: Path, loudnorm: bool = True) -> Path | None:
    clips = [f for f in files if f.suffix.lower() in VIDEO]
    if not clips:
        log.info("post: no video clips to concatenate")
        return None
    if not shutil.which("ffmpeg"):
        log.error("post: ffmpeg not installed, skipping concat")
        return None
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
    if all(_has_audio(c) for c in clips):
        cmd += (["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if loudnorm else []) + ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    subprocess.run(cmd + [str(out)], check=True)
    lst.unlink()
    log.info("post: stitched %d clips -> %s", len(clips), out)
    return out
