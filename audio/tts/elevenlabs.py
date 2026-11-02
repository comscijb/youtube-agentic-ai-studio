"""ElevenLabs backend (paid, optional; Phase 3.9-A).

Only used when ``ELEVENLABS_API_KEY`` is set (from .env; it is sent in a request header and never
printed or logged). The ``with-timestamps`` endpoint returns character timings, turned into word
timings here, so no forced alignment is needed. ``TTS_VOICE`` is the ElevenLabs voice id; the voice
must be a stock/licensed voice or your own clone (never a real public person).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import config
from audio.tts import TTSUnavailable

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
# Delivery hints → voice settings (style 0 is neutral); only from the screenplay's own signals.
STYLE = {"narration": 0.0, "dialogue": 0.2, "quiet": 0.0}
STABILITY = {"narration": 0.5, "dialogue": 0.4, "quiet": 0.65}


def api_key() -> str:
    return os.environ.get("ELEVENLABS_API_KEY", "")


def words_from_characters(text_chars: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    """Character timings (s) → word events (ms), splitting on whitespace."""
    words, current, first, last = [], "", None, None
    for char, start, end in zip(text_chars, starts, ends):
        if char.isspace():
            if current:
                words.append({"text": current, "start_ms": first * 1000, "end_ms": last * 1000})
            current, first = "", None
            continue
        if first is None:
            first = start
        current += char
        last = end
    if current:
        words.append({"text": current, "start_ms": first * 1000, "end_ms": last * 1000})
    return words


class ElevenLabsBackend:
    name = "elevenlabs"
    audio_ext = "mp3"
    supports_style = True

    def available(self) -> tuple[bool, str]:
        return (True, "ElevenLabs API (paid)") if api_key() else (False, "ELEVENLABS_API_KEY is not set")

    def cache_tag(self) -> str:
        return f"elevenlabs {getattr(config, 'ELEVENLABS_MODEL', 'eleven_multilingual_v2')}"

    def synthesize(self, text: str, out_path: str, *, voice: str, rate: str, pitch: str,
                   style: str | None = None, label: str = "") -> dict:
        import requests
        if not api_key():
            raise TTSUnavailable("ELEVENLABS_API_KEY is not set")
        if not voice:
            raise TTSUnavailable("TTS_VOICE must be an ElevenLabs voice id")
        style = style or "narration"
        response = requests.post(API.format(voice=voice), headers={"xi-api-key": api_key()}, timeout=180, json={
            "text": text, "model_id": getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2"),
            "voice_settings": {"stability": STABILITY.get(style, 0.5), "similarity_boost": 0.75,
                               "style": STYLE.get(style, 0.0)}})
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs answered {response.status_code}")    # body may echo request data
        data = response.json()
        Path(out_path).write_bytes(base64.b64decode(data["audio_base64"]))
        alignment = data.get("alignment") or {}
        words = words_from_characters(alignment.get("characters", []),
                                      alignment.get("character_start_times_seconds", []),
                                      alignment.get("character_end_times_seconds", []))
        return {"audio": out_path, "words": words, "aligned": False}
