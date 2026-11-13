"""Discovery settings edited in the GUI (Phase 4.0): translation languages, publishing slots, playlists, the
packaging-swap rule. ``data/discovery_settings.json`` overrides the defaults in config.py; nothing here is secret.
"""
from __future__ import annotations

import re
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json

NARRATIVE_TYPES = ("realistic_psychological_fiction", "philosophical_thought_experiment", "dark_human_parable")
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
SLOT = re.compile(r"^(MON|TUE|WED|THU|FRI|SAT|SUN) ([01]\d|2[0-3]):([0-5]\d)$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
PLAYLIST = re.compile(r"^[A-Za-z0-9_-]{10,64}$")


def path() -> Path:
    return Path(getattr(config, "DISCOVERY_SETTINGS_FILE", Path(config.PUBLICATIONS_FILE).parent / "discovery_settings.json"))


def defaults() -> dict:
    return {"localize_languages": list(getattr(config, "LOCALIZE_LANGUAGES", [])),
            "publish_slots": dict(getattr(config, "PUBLISH_SLOTS", {})),
            "publish_timezone": str(getattr(config, "PUBLISH_TIMEZONE", "America/New_York")),
            "playlists": dict(getattr(config, "TYPE_PLAYLISTS", {})),
            "swap_rule": dict(getattr(config, "PACKAGING_SWAP_RULE", {}))}


def load() -> dict:
    saved = load_json(path()) or {}
    merged = defaults()
    for key, value in saved.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        elif key in merged:
            merged[key] = value
    return merged


def get(key: str):
    return load()[key]


def problems(updates: dict) -> list[str]:
    found = []
    for code in updates.get("localize_languages", []) or []:
        if not LANGUAGE.match(str(code)):
            found.append(f"idioma inválido: {code}")
    for kind, slot in (updates.get("publish_slots") or {}).items():
        if kind not in NARRATIVE_TYPES:
            found.append(f"tipo desconhecido: {kind}")
        elif not SLOT.match(str(slot)):
            found.append(f"horário inválido para {kind}: {slot} (use \"TUE 18:00\")")
    if "publish_timezone" in updates:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(str(updates["publish_timezone"]))
        except Exception:                                # noqa: BLE001
            found.append(f"fuso horário desconhecido: {updates['publish_timezone']}")
    for kind, playlist in (updates.get("playlists") or {}).items():
        if kind not in NARRATIVE_TYPES:
            found.append(f"tipo desconhecido: {kind}")
        elif playlist and not PLAYLIST.match(str(playlist)):
            found.append(f"ID de playlist inválido para {kind}: {playlist}")
    for key, value in (updates.get("swap_rule") or {}).items():
        try:
            float(value)
        except (TypeError, ValueError):
            found.append(f"regra de troca: {key} precisa ser um número")
    return found


def save(updates: dict) -> dict:
    """Merge ``updates`` into the saved file after validating them; ValueError lists the problems."""
    found = problems(updates)
    if found:
        raise ValueError("; ".join(found))
    saved = load_json(path()) or {}
    for key in ("localize_languages", "publish_slots", "publish_timezone", "playlists", "swap_rule"):
        if key not in updates:
            continue
        value = updates[key]
        if key == "localize_languages":
            value = list(dict.fromkeys(str(code).strip() for code in value if str(code).strip()))
        elif key == "swap_rule":
            value = {name: float(number) for name, number in value.items()}
        elif isinstance(value, dict):
            value = {**(saved.get(key) or {}), **{name: str(item).strip() for name, item in value.items()}}
        saved[key] = value
    atomic_write_json(path(), saved)
    return load()
