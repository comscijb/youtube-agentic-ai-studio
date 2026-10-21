"""Measured narration speed per voice + rate, used for word-count planning.

Edge TTS voices speak at their own pace: en-US-AndrewNeural at -5% measured
about 152 wpm while planning assumed 125, so episodes ran ~17% short. Every
narration run records its measured delivery here; planning (outline word
targets, duration estimates) uses the calibrated value for the configured
voice and falls back to config.NARRATION_WPM.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import config

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "data" / "voice_calibration.json"
# Ignore implausible measurements (silence, failed synthesis, tiny samples).
MIN_WORDS = 50
WPM_RANGE = (80.0, 240.0)


def calibration_path() -> Path:
    return Path(getattr(config, "VOICE_CALIBRATION_FILE", None) or DEFAULT_FILE)


def voice_key(voice: str | None = None, rate: str | None = None) -> str:
    voice = voice if voice is not None else getattr(config, "VOICE_ID", "")
    rate = rate if rate is not None else getattr(config, "VOICE_RATE", "+0%")
    return f"{voice}|{rate}"


def load(path=None) -> dict:
    try:
        value = json.loads(Path(path or calibration_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"voices": {}}
    voices = value.get("voices") if isinstance(value, dict) else None
    return {"voices": voices if isinstance(voices, dict) else {}}


def record(words: int, seconds: float, *, voice: str | None = None, rate: str | None = None,
           path=None) -> dict | None:
    """Store one measurement; returns the entry, or None when it is implausible."""
    if words < MIN_WORDS or seconds <= 0:
        return None
    wpm = words / seconds * 60
    if not WPM_RANGE[0] <= wpm <= WPM_RANGE[1]:
        return None
    target = Path(path or calibration_path())
    data = load(target)
    entry = {
        "words": int(words), "seconds": round(float(seconds), 2), "wpm": round(wpm, 1),
        "date": datetime.now(timezone.utc).date().isoformat(),
    }
    data["voices"][voice_key(voice, rate)] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return entry


def calibrated_wpm(voice: str | None = None, rate: str | None = None, path=None) -> float | None:
    entry = load(path)["voices"].get(voice_key(voice, rate))
    try:
        wpm = float(entry["wpm"])
    except (TypeError, KeyError, ValueError):
        return None
    return wpm if WPM_RANGE[0] <= wpm <= WPM_RANGE[1] else None


def configured_wpm() -> float:
    return float(getattr(config, "NARRATION_WPM", 125))


def planning_wpm() -> float:
    """Words per minute used to plan episodes: calibrated if known, else configured."""
    if not getattr(config, "USE_CALIBRATED_WPM", True):
        return configured_wpm()
    return calibrated_wpm() or configured_wpm()
