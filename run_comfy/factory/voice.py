"""Voice-over providers. Each returns (audio file, word timings) - timings drive captions and cuts.

- demo:        offline tone-speech with estimated timings
- elevenlabs:  ElevenLabs TTS with character-level timestamps (premium quality, voice cloning)
- comfy:       a registered ComfyUI TTS workflow (Chatterbox / F5-TTS on your GPU); timings come from
               whisper alignment in the captions stage
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx

from ..backends.demo import synth_speech

DEMO_VOICES = ["demo-warm", "demo-bright"]


def chars_to_words(chars: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    words, cur, s = [], "", None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": round(s, 3), "end": round(prev_end, 3)})
            cur, s = "", None
            continue
        if s is None:
            s = a
        cur += ch
        prev_end = b
    if cur:
        words.append({"word": cur, "start": round(s, 3), "end": round(prev_end, 3)})
    return words


def elevenlabs_tts(text: str, voice_id: str, out: Path, model: str = "eleven_multilingual_v2",
                   api_key: str | None = None, transport: httpx.BaseTransport | None = None) -> tuple[Path, list]:
    key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    with httpx.Client(timeout=120, transport=transport) as c:
        r = c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
                   params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": key},
                   json={"text": text, "model_id": model})
        r.raise_for_status()
        j = r.json()
    out.write_bytes(base64.b64decode(j["audio_base64"]))
    al = j.get("alignment") or j.get("normalized_alignment") or {}
    words = chars_to_words(al.get("characters", []), al.get("character_start_times_seconds", []),
                           al.get("character_end_times_seconds", []))
    return out, words


def voice_stage(provider: str, model: str = ""):
    """Local stage function for providers that run outside ComfyUI."""
    def run(ctx, inputs):
        text, voice = inputs["text"], inputs.get("voice") or ""
        if provider == "elevenlabs":
            audio, words = elevenlabs_tts(text, voice, ctx.out_dir / "voice.mp3", model or "eleven_multilingual_v2")
        else:
            audio, words = synth_speech(text, ctx.out_dir / "voice.wav")
        wj = ctx.out_dir / "voice.words.json"
        wj.write_text(json.dumps(words))
        return [audio, wj]

    run.__qualname__ = f"voice_stage.{provider}"
    return run
