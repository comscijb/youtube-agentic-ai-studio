"""Settings → Ritmo / Som / Música (Phase 4.2): every new feature behind a switch, on by default.

``data/cut_settings.json`` keeps what the user changed in the GUI; ``apply`` copies it onto ``config`` at start-up
(the GUI and the CLI pipeline), so the render reads the same values either way.
"""
from __future__ import annotations

from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json

SWITCHES = ("RHYTHM_ENABLED", "UPSCALE_CROPS", "KEY_LINES_ENABLED", "BEAT_GRADES_ENABLED", "SOUND_DESIGN_ENABLED",
            "MUSIC_PLAN_ENABLED", "VIDEO_ATTENTION_GATE")
RHYTHM_KEYS = ("cold_open", "tension", "development", "reflection")


def path() -> Path:
    return Path(getattr(config, "CUT_SETTINGS_FILE", Path(config.PUBLICATIONS_FILE).parent / "cut_settings.json"))


def current() -> dict:
    return {"switches": {name: bool(getattr(config, name, True)) for name in SWITCHES},
            "rhythm": dict(getattr(config, "RHYTHM", {})), "music_volume": float(getattr(config, "MUSIC_VOLUME", 0.12))}


def apply() -> dict:
    saved = load_json(path()) or {}
    for name, value in (saved.get("switches") or {}).items():
        if name in SWITCHES:
            setattr(config, name, bool(value))
    if saved.get("rhythm"):
        setattr(config, "RHYTHM", {**dict(getattr(config, "RHYTHM", {})), **saved["rhythm"]})
    return current()


def save(updates: dict) -> dict:
    """Validate and store; ValueError on a bad range (low > high, more than the 7 s hard maximum)."""
    saved = load_json(path()) or {}
    switches = {name: bool(value) for name, value in (updates.get("switches") or {}).items() if name in SWITCHES}
    rhythm = {}
    for key, value in (updates.get("rhythm") or {}).items():
        if key == "max_hold":
            if not 3 <= float(value) <= 12:
                raise ValueError("o máximo sem mudança visual fica entre 3 e 12 s")
            rhythm[key] = float(value)
        elif key in RHYTHM_KEYS:
            low, high = (float(item) for item in value)
            if not 1 <= low <= high <= 12:
                raise ValueError(f"ritmo {key}: use 1 ≤ mínimo ≤ máximo ≤ 12 s")
            rhythm[key] = [low, high]
    saved.setdefault("switches", {}).update(switches)
    saved.setdefault("rhythm", {}).update(rhythm)
    atomic_write_json(path(), saved)
    return apply()
