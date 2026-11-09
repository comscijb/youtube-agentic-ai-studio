"""Caption cues from word timings.

Edge TTS WordBoundary events carry bare words ("dinner", "eight") while the
screenplay text carries the punctuation the viewer should read ("dinner—eight",
"father’s,"). ``align_words`` maps events back onto the narration tokens, and
``build_cues`` groups them into readable cues: at most 7 words / 42
characters, never crossing a sentence boundary, 0.8-4.5 s on screen, with a
short lead-in so the text appears just before it is spoken.
"""
from __future__ import annotations

import re

import config

_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")


def _norm(text: str) -> str:
    return "".join(char for char in str(text).lower() if char.isalnum())


def setting(name: str, default):
    return type(default)(getattr(config, name, default))


def align_words(text: str, events: list[dict]) -> list[dict]:
    """Attach event timings (ms) to the narration's own tokens.

    Returns ``[{"text", "start", "end", "sentence_end"}]`` in seconds. A token
    spanning several events ("dinner—eight") takes the first start and last
    end; punctuation-only tokens join the previous word; tokens the voice
    skipped inherit the neighbouring timing.
    """
    tokens = str(text).split()
    words: list[dict] = []
    index = 0
    for token in tokens:
        key = _norm(token)
        if not key:
            if words:
                words[-1]["text"] += " " + token
            continue
        start = end = None
        consumed = ""
        while index < len(events):
            event_key = _norm(events[index].get("text", ""))
            if not event_key:
                index += 1
                continue
            candidate = consumed + event_key
            if key.startswith(candidate) or (not consumed and event_key.startswith(key)):
                start = events[index]["start_ms"] if start is None else start
                end = events[index]["end_ms"]
                consumed = candidate
                index += 1
                if len(consumed) >= len(key):
                    break
            else:
                break
        if start is None:
            # Not spoken as a separate event: share the previous word's end.
            previous = words[-1]["end"] * 1000 if words else 0.0
            start = end = previous
        words.append({"text": token, "start": start / 1000, "end": end / 1000})
    for word in words:
        word["sentence_end"] = bool(_SENTENCE_END.search(word["text"]))
    return words


def mark_thoughts(text: str, words: list[dict]) -> list[dict]:
    """Flag the words inside heard thoughts (single quotes). ``words`` come from ``align_words`` (where a
    punctuation-only token joined the previous word) or ``proportional_words``."""
    from agents.quotes import thought_token_flags
    tokens, flags = str(text).split(), thought_token_flags(text)
    aligned = len(words) == len(tokens)
    position = 0
    for token, flag in zip(tokens, flags):
        if not aligned and not _norm(token):
            continue
        if position >= len(words):
            break
        if flag:
            words[position]["thought"] = True
        position += 1
    return words


def cue_text(group: list[dict]) -> str:
    """A cue's words; a heard thought is wrapped in <i>…</i> (SRT; ASS turns it into \\i1)."""
    pieces, inside = [], False
    for word in group:
        flagged = bool(word.get("thought"))
        if flagged and not inside:
            pieces.append("<i>" + word["text"])
        elif inside and not flagged:
            pieces[-1] += "</i>"
            pieces.append(word["text"])
        else:
            pieces.append(word["text"])
        inside = flagged
    if inside:
        pieces[-1] += "</i>"
    return " ".join(pieces)


def strip_tags(text: str) -> str:
    return re.sub(r"</?i>", "", str(text))


def proportional_words(text: str, total_seconds: float) -> list[dict]:
    """Character-weighted word timings when the voice sent no WordBoundary events."""
    tokens = str(text).split()
    if not tokens or total_seconds <= 0:
        return []
    gap = 1.5   # an inter-word pause counts as 1.5 characters
    per_char = total_seconds / (sum(len(token) for token in tokens) + gap * len(tokens))
    words, cursor = [], 0.0
    for token in tokens:
        end = cursor + len(token) * per_char
        words.append({"text": token, "start": cursor, "end": min(end, total_seconds),
                      "sentence_end": bool(_SENTENCE_END.search(token))})
        cursor = end + gap * per_char
    return words


def build_cues(words: list[dict], *, max_words: int | None = None, max_chars: int | None = None,
               min_seconds: float | None = None, max_seconds: float | None = None,
               lead_in: float | None = None, end_limit: float | None = None) -> list[tuple]:
    """Group timed words into ``(start_ms, end_ms, text)`` cues."""
    max_words = max_words or setting("CAPTION_MAX_WORDS", 7)
    max_chars = max_chars or setting("CAPTION_MAX_CHARS", 42)
    min_seconds = setting("CAPTION_MIN_SECONDS", 0.8) if min_seconds is None else min_seconds
    max_seconds = setting("CAPTION_MAX_SECONDS", 4.5) if max_seconds is None else max_seconds
    lead_in = setting("CAPTION_LEAD_IN_SECONDS", 0.08) if lead_in is None else lead_in

    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current:
            text = " ".join(item["text"] for item in current + [word])
            if (len(current) >= max_words or len(text) > max_chars
                    or word["end"] - current[0]["start"] > max_seconds):
                groups.append(current)
                current = []
        current.append(word)
        if word["sentence_end"]:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    cues = []
    for position, group in enumerate(groups):
        start = max(0.0, group[0]["start"] - lead_in)
        if cues:
            start = max(start, cues[-1][1])
        next_start = (max(0.0, groups[position + 1][0]["start"] - lead_in)
                      if position + 1 < len(groups) else None)
        end = max(group[-1]["end"], start + min_seconds)
        if next_start is not None:
            end = min(end, max(next_start, group[-1]["end"]))
        if end_limit is not None:
            end = min(end, end_limit)
        if end <= start:
            continue
        cues.append((start, end, cue_text(group)))
    return [(round(start * 1000), round(end * 1000), text) for start, end, text in cues]
