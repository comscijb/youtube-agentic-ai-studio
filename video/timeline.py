"""Exact scene timing derived from rendered narration audio."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.narrative import atomic_write_json


def build_scene_timeline(sections: list[dict], durations: list[float]) -> dict:
    if len(sections) != len(durations) or not sections:
        raise ValueError("sections and durations must be non-empty and have equal length")
    cursor = 0.0
    scenes = []
    for section, raw_duration in zip(sections, durations):
        duration = round(float(raw_duration), 3)
        if duration <= 0:
            raise ValueError("every scene audio duration must be positive")
        start = round(cursor, 3)
        end = round(start + duration, 3)
        scenes.append({
            "scene_id": section.get("id"),
            "outline_scene_id": section.get("outline_scene_id", ""),
            "title": section.get("title", ""),
            "start": start,
            "end": end,
            "duration": duration,
        })
        cursor = end
    return {
        "schema_version": 2,
        "timing_source": "per_scene_audio",
        "transition_policy": "duration_preserving",
        "total_duration": round(cursor, 3),
        "scenes": scenes,
    }


def validate_timeline(timeline: dict, audio_duration: float | None = None,
                      tolerance: float = 0.5) -> dict:
    scenes = timeline.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("timeline requires scenes")
    cursor = 0.0
    for scene in scenes:
        start, end, duration = (float(scene[key]) for key in ("start", "end", "duration"))
        if abs(start - cursor) > 0.01 or end <= start or abs((end - start) - duration) > 0.01:
            raise ValueError("timeline scenes must be contiguous and internally consistent")
        cursor = end
    total = float(timeline.get("total_duration", cursor))
    if abs(total - cursor) > 0.01:
        raise ValueError("timeline total does not match its scenes")
    if audio_duration is not None and abs(total - float(audio_duration)) > tolerance:
        raise ValueError(
            f"timeline differs from narration by {abs(total - float(audio_duration)):.3f}s"
        )
    return timeline


def write_timeline(path, sections: list[dict], durations: list[float]) -> dict:
    timeline = validate_timeline(build_scene_timeline(sections, durations))
    atomic_write_json(path, timeline)
    return timeline


def load_timeline(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return validate_timeline(data)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def parse_srt(path, *, keep_tags: bool = False) -> list[tuple[float, float, str]]:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    cues = []
    for block in re.split(r"\r?\n\r?\n", raw):
        lines = block.strip().splitlines()
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        try:
            start, end = (_timestamp_to_ms(value) for value in lines[1].split(" --> ", 1))
            text = " ".join(lines[2:])
            cues.append((start, end, text if keep_tags else re.sub(r"</?i>", "", text)))
        except ValueError:
            continue
    return cues


def write_shifted_srt(destination, scene_srts: list[tuple[str, float]]) -> int:
    shifted = []
    for path, offset_seconds in scene_srts:
        offset_ms = float(offset_seconds) * 1000
        for start, end, text in parse_srt(path, keep_tags=True):
            shifted.append((start + offset_ms, end + offset_ms, text))
    with open(destination, "w", encoding="utf-8") as handle:
        for index, (start, end, text) in enumerate(shifted, 1):
            handle.write(f"{index}\n{_format_ms(start)} --> {_format_ms(end)}\n{text}\n\n")
    return len(shifted)


def proportional_scene_durations(sections: list[dict], total_duration: float) -> list[float]:
    """Safe legacy fallback when no per-scene narration artifact exists."""
    weights = [max(1, len(section.get("narration", "").split())) for section in sections]
    total_weight = sum(weights)
    durations = [float(total_duration) * weight / total_weight for weight in weights]
    durations[-1] += float(total_duration) - sum(durations)
    return durations


def _timestamp_to_ms(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d+):(\d+),(\d+)", value.strip())
    if not match:
        raise ValueError("invalid SRT timestamp")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3_600_000 + minutes * 60_000 + seconds * 1000 + millis


def _format_ms(value: float) -> str:
    millis = max(0, int(round(value)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
