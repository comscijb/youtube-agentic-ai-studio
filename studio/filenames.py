"""File names built from IDs, titles or model text that are valid on Windows (and so everywhere).

Windows rejects ``<>:"/\\|?*`` and control characters, names ending in a space
or a dot, and the device names (``CON``, ``NUL``, ``COM1``…) even with an
extension. IDs such as ``s01_01`` or ``julian_vance`` pass through unchanged,
so existing paths keep working.
"""
from __future__ import annotations

import re

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
MAX_LENGTH = 120


def safe_filename(name, *, max_length: int = MAX_LENGTH, default: str = "file", replacement: str = "_") -> str:
    """``name`` as one path component: invalid characters replaced, no trailing dots or
    spaces, no reserved device name, at most ``max_length`` characters, never empty."""
    text = INVALID.sub(replacement, str(name if name is not None else "")).strip(" ")
    text = text[:max_length].rstrip(" .")
    if not text.strip("._ "):
        text = default
    if text.split(".")[0].upper() in RESERVED:
        text = (replacement + text)[:max_length]
    return text


def windows_problems(name: str) -> list[str]:
    """Why ``name`` is not a valid Windows file name (empty list when it is)."""
    problems = []
    if not name:
        problems.append("empty")
    if INVALID.search(name):
        problems.append("invalid character")
    if name != name.rstrip(" ."):
        problems.append("ends with a space or a dot")
    if name.split(".")[0].upper() in RESERVED:
        problems.append("reserved device name")
    if len(name) > 255:
        problems.append("longer than 255 characters")
    return problems
