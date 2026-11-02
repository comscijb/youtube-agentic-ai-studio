"""Per-line delivery hints from the screenplay's own signals (Phase 3.9-A).

A scene's narration is split into narration and quoted dialogue (Phase 3.7 puts spoken lines in
double quotes). Narration in a scene whose ``emotional_tone`` is intimate or hushed is read quieter.
Nothing else is inferred: no emotional tag the script does not support. Only backends with
``supports_style`` use the hints; the others read the scene in one piece.
"""
from __future__ import annotations

import re

QUIET = re.compile(r"\b(?:quiet|intimate|hushed|whisper\w*|tender|soft|subdued|murmur\w*|hush)\b", re.I)
_QUOTED = re.compile(r'("[^"]+"|“[^”]+”)')


def lines(text: str, section: dict | None = None) -> list[tuple[str, str]]:
    """[(text, "narration" | "dialogue" | "quiet")], in order, covering all of ``text``."""
    quiet = bool(QUIET.search(str((section or {}).get("emotional_tone", ""))))
    pieces = []
    for part in _QUOTED.split(str(text)):
        part = part.strip()
        if not part:
            continue
        style = "dialogue" if _QUOTED.fullmatch(part) else ("quiet" if quiet else "narration")
        if pieces and pieces[-1][1] == style:
            pieces[-1] = (pieces[-1][0] + " " + part, style)
        else:
            pieces.append((part, style))
    return pieces or [(str(text), "narration")]
