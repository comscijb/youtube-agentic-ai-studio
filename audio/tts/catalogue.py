"""The curated voice catalogue the GUI previews (Phase 3.9.1-B).

Edge voices are stock Microsoft voices (online). Kokoro voices are filtered against the installed
``voices-v1.0.bin`` (an npz archive whose keys are the voice ids), so the list only offers voices that
exist on this machine. ElevenLabs is listed only when it is configured (key + voice id).
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import config

EDGE = [
    ("en-US-AndrewNeural", "Andrew (US, warm)"),
    ("en-US-BrianNeural", "Brian (US, calm)"),
    ("en-US-ChristopherNeural", "Christopher (US, deep)"),
    ("en-US-GuyNeural", "Guy (US, neutral)"),
    ("en-US-EricNeural", "Eric (US, plain)"),
    ("en-GB-RyanNeural", "Ryan (UK, warm)"),
    ("en-GB-ThomasNeural", "Thomas (UK, measured)"),
]
KOKORO = [
    ("am_michael", "Michael (US)"),
    ("am_adam", "Adam (US)"),
    ("am_eric", "Eric (US)"),
    ("am_onyx", "Onyx (US, deep)"),
    ("am_liam", "Liam (US)"),
    ("bm_george", "George (UK)"),
    ("bm_fable", "Fable (UK)"),
    ("bm_lewis", "Lewis (UK)"),
    ("bm_daniel", "Daniel (UK)"),
    ("af_heart", "Heart (US, female)"),
    ("bf_emma", "Emma (UK, female)"),
]
LICENCES = {"edge": "Microsoft Edge read-aloud service (online; stock voice)",
            "local": "Kokoro-82M weights Apache-2.0 (commercial use allowed)",
            "elevenlabs": "ElevenLabs paid plan terms (commercial use on paid tiers)"}


def kokoro_installed(voices_file=None) -> set[str] | None:
    """Voice ids in the installed Kokoro voices file; None when there is no file."""
    if voices_file is None:
        from audio.tts import local
        voices_file = local.kokoro_files()[1]
    path = Path(voices_file)
    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            return {Path(name).stem for name in archive.namelist() if name.endswith(".npy")}
    except zipfile.BadZipFile:
        return None


def elevenlabs_configured() -> bool:
    return bool(os.getenv("ELEVENLABS_API_KEY")) and bool(getattr(config, "ELEVENLABS_VOICE_ID", ""))


def catalogue(voices_file=None) -> list[dict]:
    """``[{"backend", "voice", "label", "licence"}]`` — only voices that can run here."""
    entries = [{"backend": "edge", "voice": voice, "label": label} for voice, label in EDGE]
    installed = kokoro_installed(voices_file)
    entries += [{"backend": "local", "voice": voice, "label": label} for voice, label in KOKORO
                if installed is not None and voice in installed]
    if elevenlabs_configured():
        entries.append({"backend": "elevenlabs", "voice": config.ELEVENLABS_VOICE_ID, "label": "ElevenLabs voice"})
    return [dict(entry, licence=LICENCES[entry["backend"]]) for entry in entries]


def key(backend: str, voice: str) -> str:
    return f"{backend}:{voice}"
