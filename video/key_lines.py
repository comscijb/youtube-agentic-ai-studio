"""Key lines on screen (Phase 4.2-B).

From the 4.1 attention map, 1-3 lines per minute (questions, reveals, the hook line, the final narrated question)
appear as large text while they are spoken: ivory on a soft dark gradient, revealed word by word on the real word
timings, never over a face, never with the burned captions at the same time (the captions are hidden while a key
line is up; the line goes to the upper third unless a face is there). Only words already spoken are shown, so a key
line can never spoil; the cold open's key line is its question, not the answer. ``KEY_LINES_ENABLED`` turns it off.
"""
from __future__ import annotations

import re

import config

IVORY = (238, 230, 212)
GRADIENT_ALPHA = 0.62
MAX_WORDS = 14
MIN_GAP = 15.0                 # seconds between two key lines
PER_MINUTE = (1, 3)
HOLD_AFTER = 0.9               # seconds the full line stays after its last word
PRIORITY = {"question": 0, "reveal": 1, "open_loop": 2, "dialogue_turn": 3}


def enabled() -> bool:
    return bool(getattr(config, "KEY_LINES_ENABLED", True))


def _norm(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower().replace("’", "'"))


def find_span(words: list[dict], text: str, start_index: int = 0) -> tuple[int, int] | None:
    """The word indexes [first, last] in narration_words.json that speak ``text`` (first match from start_index)."""
    wanted = _norm(text)
    if not wanted:
        return None
    spoken = [(_norm(word["text"]) or [""])[0] for word in words]
    for first in range(start_index, len(spoken) - len(wanted) + 1):
        if spoken[first:first + len(wanted)] == wanted:
            return first, first + len(wanted) - 1
    return None


def choose(attention_map: dict, words: list[dict], *, duration: float | None = None) -> list[dict]:
    """[{"text", "kind", "start", "end", "words": [{"text", "start"}], "cold_open"}], 1-3 per minute, spaced."""
    sentences = attention_map.get("sentences", []) if attention_map else []
    if not sentences or not words:
        return []
    total = float(duration or words[-1]["end"])
    candidates = []
    last = len(sentences) - 1
    for position, item in enumerate(sentences):
        kinds = {tag["tag"].split(":")[0] for tag in item.get("tags", [])}
        cold = item.get("section_type") == "cold_open"
        if cold and "question" not in kinds and "?" not in item["text"]:
            continue                                   # the teaser's line is its question, never the answer
        rank = min((PRIORITY[kind] for kind in kinds if kind in PRIORITY), default=None)
        if position == last and "?" in item["text"]:
            rank = -1                                  # the final narrated question
        if rank is None or len(_norm(item["text"])) > MAX_WORDS:
            continue
        candidates.append((rank, position, item, cold))
    found, cursor = [], 0
    for rank, position, item, cold in sorted(candidates, key=lambda entry: (entry[0], entry[1])):
        span = find_span(words, item["text"])
        if span is None:
            continue
        first, final = span
        line = {"text": item["text"], "kind": "final_question" if rank == -1 else min(
                    (kind for kind in PRIORITY if kind in {tag["tag"].split(":")[0] for tag in item["tags"]}), key=PRIORITY.get, default="question"),
                "start": float(words[first]["start"]), "end": float(words[final]["end"]) + HOLD_AFTER, "cold_open": cold,
                "words": [{"text": words[index]["text"], "start": float(words[index]["start"])} for index in range(first, final + 1)]}
        if any(abs(line["start"] - other["start"]) < MIN_GAP for other in found):
            continue
        minute = int(line["start"] // 60)
        if sum(1 for other in found if int(other["start"] // 60) == minute) >= PER_MINUTE[1]:
            continue
        found.append(line)
    found.sort(key=lambda line: line["start"])
    return found[:max(1, int(total / 60 * PER_MINUTE[1]) + 1)]


def visible_text(line: dict, t: float) -> str:
    """The words already spoken at global time ``t`` (word-by-word reveal; nothing ahead of the narration)."""
    if t < line["start"] or t > line["end"]:
        return ""
    return " ".join(word["text"] for word in line["words"] if word["start"] <= t)


def placement(faces: list, *, captions_on: bool) -> dict:
    """{"band": "upper" | "lower", "hide_captions": bool}: the upper third unless a face is there."""
    upper = [0.08, 0.06, 0.92, 0.36]
    face_up = any(not (face[2] <= upper[0] or face[0] >= upper[2] or face[3] <= upper[1] or face[1] >= upper[3]) for face in faces)
    if not face_up:
        return {"band": "upper", "hide_captions": False}
    return {"band": "lower", "hide_captions": captions_on}


def contrast_ratio(fore=IVORY, back=(0, 0, 0), alpha: float = GRADIENT_ALPHA, under=(90, 90, 90)) -> float:
    """WCAG contrast of the text over the gradient laid on a mid-grey frame (the worst common case)."""
    def luminance(rgb):
        channels = []
        for value in rgb:
            value = value / 255
            channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    mixed = tuple(round(b * alpha + u * (1 - alpha)) for b, u in zip(back, under))
    high, low = sorted((luminance(fore), luminance(mixed)), reverse=True)
    return round((high + 0.05) / (low + 0.05), 2)


def hidden_caption_windows(lines: list[dict]) -> list[tuple[float, float]]:
    return [(line["start"], line["end"]) for line in lines if line.get("hide_captions")]


def without_hidden(captions: list[dict], windows: list[tuple[float, float]]) -> list[dict]:
    """Caption cues with the key-line windows cut out (the burned captions never sit under a key line)."""
    found = []
    for cue in captions:
        pieces = [(cue["start"], cue["end"])]
        for start, end in windows:
            pieces = [part for piece in pieces for part in ((piece[0], min(piece[1], start)), (max(piece[0], end), piece[1]))
                      if part[1] - part[0] > 0.25]
        found += [{**cue, "start": start, "end": end} for start, end in pieces]
    return found


def draw(frame, line: dict, t: float, band: str, font_loader) -> object:
    """Draw the visible part of ``line`` on a PIL frame (soft dark gradient, ivory, large)."""
    from PIL import Image, ImageDraw
    text = visible_text(line, t)
    if not text:
        return frame
    width, height = frame.size
    top = int(height * (0.08 if band == "upper" else 0.56))
    band_height = int(height * 0.28)
    gradient = Image.new("L", (1, band_height))
    for y in range(band_height):
        gradient.putpixel((0, y), int(255 * GRADIENT_ALPHA * (1 - abs(y - band_height / 2) / (band_height / 2)) ** 0.6))
    shade = Image.new("RGB", (width, band_height), (0, 0, 0))
    region = frame.crop((0, top, width, top + band_height))
    frame.paste(Image.composite(shade, region, gradient.resize((width, band_height))), (0, top))
    canvas = ImageDraw.Draw(frame)
    font = font_loader("bold", int(height * 0.062))
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if canvas.textlength(trial, font=font) > width * 0.78 and current:
            lines.append(current)
            current = word
        else:
            current = trial
    lines.append(current)
    step = int(height * 0.075)
    y = top + (band_height - step * len(lines)) // 2
    for row in lines[:3]:
        x = (width - canvas.textlength(row, font=font)) / 2
        canvas.text((x + 2, y + 2), row, font=font, fill=(0, 0, 0))
        canvas.text((x, y), row, font=font, fill=IVORY)
        y += step
    return frame
