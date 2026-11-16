"""Settings → Shorts (Phase 4.3): the mode, the formats and their weights, how many per episode, the voice speed.

``data/shorts_settings.json`` keeps what the user changed in the GUI (or applied from the "Formatos de Shorts" panel);
``apply`` copies it onto ``config`` at start-up (GUI and CLI pipeline), like ``video.cut_settings``.
"""
from __future__ import annotations

from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json


def path() -> Path:
    return Path(getattr(config, "SHORTS_SETTINGS_FILE", Path(config.PUBLICATIONS_FILE).parent / "shorts_settings.json"))


def current() -> dict:
    from video import short_formats
    return {"mode": short_formats.mode(), "formats": {name: float(dict(getattr(config, "SHORTS_FORMATS", {}) or {}).get(name, 0))
                                                     for name in short_formats.ORDER},
            "per_episode": short_formats.per_episode(), "tts_speed": float(getattr(config, "SHORTS_TTS_SPEED", 0.95)),
            "labels": {name: short_formats.FORMATS[name]["label"] for name in short_formats.ORDER}}


def apply() -> dict:
    saved = load_json(path()) or {}
    if saved.get("mode") in ("formats", "cuts"):
        config.SHORTS_MODE = saved["mode"]
    if isinstance(saved.get("formats"), dict):
        config.SHORTS_FORMATS = {**dict(getattr(config, "SHORTS_FORMATS", {}) or {}), **saved["formats"]}
    if saved.get("per_episode"):
        config.SHORTS_PER_EPISODE = int(saved["per_episode"])
    if saved.get("tts_speed"):
        config.SHORTS_TTS_SPEED = float(saved["tts_speed"])
    return current()


def save(updates: dict) -> dict:
    """Validate and store; ValueError on a bad value."""
    from video import short_formats
    saved = load_json(path()) or {}
    if "mode" in updates:
        if updates["mode"] not in ("formats", "cuts"):
            raise ValueError("modo: 'formats' (roteiros próprios) ou 'cuts' (recortes do episódio)")
        saved["mode"] = updates["mode"]
    if "formats" in updates:
        weights = {name: float(value) for name, value in (updates["formats"] or {}).items() if name in short_formats.FORMATS}
        if any(value < 0 or value > 10 for value in weights.values()):
            raise ValueError("pesos dos formatos entre 0 e 10")
        merged = {**dict(getattr(config, "SHORTS_FORMATS", {}) or {}), **(saved.get("formats") or {}), **weights}
        if not any(float(value) > 0 for value in merged.values()):
            raise ValueError("ligue ao menos um formato")
        saved["formats"] = {name: float(merged.get(name, 0)) for name in short_formats.ORDER}
    if "per_episode" in updates:
        count = int(updates["per_episode"])
        if not 1 <= count <= short_formats.MAX_PER_EPISODE:
            raise ValueError(f"Shorts por episódio: 1 a {short_formats.MAX_PER_EPISODE}")
        saved["per_episode"] = count
    if "tts_speed" in updates:
        value = float(updates["tts_speed"])
        if not 0.7 <= value <= 1.2:
            raise ValueError("velocidade da voz dos Shorts entre 0,70 e 1,20")
        saved["tts_speed"] = value
    atomic_write_json(path(), saved)
    return apply()
