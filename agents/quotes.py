"""Quoted spans in narration (Phase 3.9.2): speech in double quotes, heard thoughts in single quotes.

An apostrophe inside a word ("don't", "Wickham's") is not a quote: a single quote opens only after a
non-word character and before a word character, and closes only after a non-space and before a
non-word character. Single quotes inside double-quoted speech are not thoughts. No project imports:
the captions and the audio post-process use this too.
"""
from __future__ import annotations

import re

_DOUBLE = re.compile(r'"[^"]*"|“[^”]*”')
_OPENERS = "'‘"
_CLOSERS = "'’"


def double_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the double-quoted speech (quotes included)."""
    return [match.span() for match in _DOUBLE.finditer(str(text or ""))]


def thought_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the single-quoted spans (quotes included), outside double quotes."""
    text = str(text or "")
    masked = list(text)
    for start, end in double_spans(text):
        masked[start:end] = " " * (end - start)
    found, opening = [], None
    for index, char in enumerate(masked):
        before = masked[index - 1] if index else " "
        after = masked[index + 1] if index + 1 < len(masked) else " "
        if opening is None:
            if char in _OPENERS and not before.isalnum() and after.isalnum():
                opening = index
        elif char in _CLOSERS and not before.isspace() and not after.isalnum():
            found.append((opening, index + 1))
            opening = None
    return found


def thought_lines(text: str) -> list[str]:
    """The heard thoughts in ``text``, without their quotes."""
    text = str(text or "")
    return [text[start + 1:end - 1] for start, end in thought_spans(text)]


def thought_token_flags(text: str) -> list[bool]:
    """For each whitespace token of ``text`` (``text.split()``): is it inside a heard thought?"""
    text = str(text or "")
    spans = thought_spans(text)
    flags = []
    for match in re.finditer(r"\S+", text):
        flags.append(any(match.start() < end and match.end() > start for start, end in spans))
    return flags


def without_speech(text: str) -> str:
    """``text`` with double-quoted speech blanked out (the narrator's own words and thoughts remain)."""
    return _DOUBLE.sub(" ", str(text or ""))
