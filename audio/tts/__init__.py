"""Pluggable text-to-speech (Phase 3.9-A).

Every backend implements ``TTSBackend.synthesize(text, out_path, *, voice, rate, pitch, style)`` and
returns ``{"audio": path, "words": [{"text", "start_ms", "end_ms"}], "aligned": bool}``. Word timings
are mandatory (captions, shot sync, Shorts cuts): a backend that does not return word boundaries goes
through forced alignment against the known text (``audio.tts.align``).

Backends:

* ``edge`` — Microsoft Edge TTS (the default until the user picks a channel voice);
* ``local`` — an offline model with a commercial-use licence (``TTS_LOCAL_ENGINE``: ``kokoro`` or
  ``chatterbox``), run in this process after freeing ComfyUI's VRAM, unloaded afterwards;
* ``elevenlabs`` — paid API, only used when ``ELEVENLABS_API_KEY`` is set (never printed).

The channel voice is ``TTS_BACKEND`` + ``TTS_VOICE``. Voice identity rules: a reference clip for
cloning must be the user's own voice or one licensed for this use, never a real public person; it
lives in ``assets/voice/`` with a provenance note, and is refused without one (``audio.tts.voices``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import config

BACKENDS = ("edge", "local", "elevenlabs")


class TTSUnavailable(RuntimeError):
    """The backend cannot run here (package, model or key missing)."""


class TTSBackend(Protocol):
    name: str
    audio_ext: str                     # file extension the backend writes
    supports_style: bool               # per-line delivery hints (dialogue / narration / quiet)

    def available(self) -> tuple[bool, str]: ...

    def cache_tag(self) -> str: ...

    def synthesize(self, text: str, out_path: str, *, voice: str, rate: str, pitch: str,
                   style: str | None = None) -> dict: ...


def backend_name(value: str | None = None) -> str:
    name = str(value or getattr(config, "TTS_BACKEND", "edge") or "edge").strip().lower()
    return name if name in BACKENDS else "edge"


def channel_voice(backend: str | None = None) -> str:
    """The configured voice of ``backend`` (default: the channel's backend)."""
    backend = backend_name(backend)
    voice = str(getattr(config, "TTS_VOICE", "") or "")
    if backend_name() == backend and voice:
        return voice
    return {"edge": getattr(config, "VOICE_ID", "en-US-AndrewNeural"),
            "local": getattr(config, "TTS_LOCAL_VOICE", "af_heart"),
            "elevenlabs": getattr(config, "ELEVENLABS_VOICE_ID", "")}[backend]


def get(name: str | None = None) -> TTSBackend:
    name = backend_name(name)
    if name == "local":
        from audio.tts.local import LocalBackend
        return LocalBackend()
    if name == "elevenlabs":
        from audio.tts.elevenlabs import ElevenLabsBackend
        return ElevenLabsBackend()
    from audio.tts.edge import EdgeBackend
    return EdgeBackend()


def identity(backend: str | None = None, voice: str | None = None) -> dict:
    """What a narration was made with (stored next to it: a change marks it stale)."""
    backend = backend_name(backend)
    found = {"backend": backend, "voice": voice or channel_voice(backend),
             "rate": getattr(config, "VOICE_RATE", "+0%"), "pitch": getattr(config, "VOICE_PITCH", "+0Hz")}
    reference = getattr(config, "TTS_REFERENCE_CLIP", "")
    if backend == "local" and reference and str(getattr(config, "TTS_LOCAL_ENGINE", "")).lower() == "chatterbox":
        found["reference"] = Path(reference).name        # a cloned voice: the disclosure rule checks it
    return found
