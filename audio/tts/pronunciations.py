"""One shared pronunciation list for every backend (Phase 3.9-A).

``data/pronunciations.json``::

    {"words": {"Leeds": {"respell": "Leeds"}, "Siobhan": {"respell": "Shiv-awn", "ipa": "ʃɪˈvɔːn"}}}

``apply(text, backend)`` replaces whole words (case-insensitive, keeping a capital first letter) with
the respelling; IPA is used only by backends that accept it (none of the current ones take SSML from
plain text, so it is stored for later). Captions and timings always use the original script words:
the respelling only changes what the engine reads.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import config

DEFAULT_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "pronunciations.json"
IPA_BACKENDS: tuple = ()


def path() -> Path:
    return Path(getattr(config, "PRONUNCIATIONS_FILE", None) or DEFAULT_FILE)


def load(file=None) -> dict:
    try:
        data = json.loads(Path(file or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    words = data.get("words") if isinstance(data, dict) else None
    return {str(key): value for key, value in (words or {}).items() if isinstance(value, dict)}


def apply(text: str, backend: str, entries: dict | None = None) -> str:
    entries = load() if entries is None else entries
    for word, entry in sorted(entries.items(), key=lambda item: -len(item[0])):
        replacement = entry.get("ipa") if backend in IPA_BACKENDS and entry.get("ipa") else entry.get("respell")
        if not replacement:
            continue

        def keep_case(found, replacement=replacement):
            text = found.group(0)
            return replacement[:1].upper() + replacement[1:] if text[:1].isupper() else replacement
        text = re.sub(rf"(?<![\w'’]){re.escape(word)}(?!\w)", keep_case, text, flags=re.I)   # "Siobhan's" too
    return text


def fingerprint(entries: dict | None = None) -> str:
    """Part of the TTS cache key: a changed pronunciation re-synthesises the affected scenes."""
    import hashlib
    entries = load() if entries is None else entries
    return hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
