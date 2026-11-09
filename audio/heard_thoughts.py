"""Heard thoughts sound different (Phase 3.9.2-E).

When the brief's rule involves thoughts (``premise_brief.heard_thoughts_on``) the screenplay writes them
in single quotes. Here the spans inside single quotes are delivered "quieter than speech": -5 dB with
30 ms fades, a light high-pass at 120 Hz and a short, soft room reverb (send at most 12 %).

The effect is gain and filters on the finished narration, so it works with every TTS backend and never
stretches time: word timings, Shorts slicing and alignment are untouched. ``HEARD_THOUGHT_STYLE`` is
"soft" (used wherever the convention is on) or "off".
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np

import config

GAIN_DB = -5.0
FADE_SECONDS = 0.03
HIGHPASS_HZ = 120
REVERB_SEND = 0.12
REVERB_DELAY_MS = 35
RATE = 44100
CHANNELS = 2


def enabled(output_dir, script: dict | None = None) -> bool:
    """The convention is on for this project and the style is not "off"."""
    if str(getattr(config, "HEARD_THOUGHT_STYLE", "soft")).lower() == "off":
        return False
    from agents.narrative import load_json
    from agents.premise_brief import heard_thoughts_on
    story = load_json(Path(output_dir) / "story_brief.json") or {}
    return heard_thoughts_on(story.get("author_brief"), (script or {}).get("narrative_type") or story.get("narrative_type"))


def intervals(words: list[dict]) -> list[tuple[float, float]]:
    """Seconds covered by consecutive words flagged ``thought`` (``video.captions.mark_thoughts``)."""
    spans: list[tuple[float, float]] = []
    previous = False
    for word in words:
        flagged = bool(word.get("thought"))
        if flagged and previous:
            spans[-1] = (spans[-1][0], float(word["end"]))
        elif flagged:
            spans.append((float(word["start"]), float(word["end"])))
        previous = flagged
    return [span for span in spans if span[1] > span[0]]


def envelope(length: int, rate: int, spans: list[tuple[float, float]], fade: float = FADE_SECONDS) -> np.ndarray:
    """1 inside the spans, 0 outside, with linear fades inside the span boundaries."""
    shape = np.zeros(length, dtype=np.float32)
    for start, end in spans:
        first, last = max(0, int(start * rate)), min(length, int(end * rate))
        if last <= first:
            continue
        ramp = min(int(fade * rate), (last - first) // 2)
        shape[first:last] = 1.0
        if ramp > 0:
            rise = np.linspace(0, 1, ramp, endpoint=False, dtype=np.float32)
            shape[first:first + ramp] = np.maximum(shape[first:first + ramp] * 0, rise)
            shape[last - ramp:last] = rise[::-1]
    return shape


def mix(dry: np.ndarray, wet: np.ndarray, spans: list[tuple[float, float]], rate: int = RATE,
        gain_db: float = GAIN_DB) -> np.ndarray:
    """Interleaved samples: ``dry`` outside the spans, ``wet`` at ``gain_db`` inside, same length as ``dry``."""
    channels = dry.reshape(-1, CHANNELS)
    frames = len(channels)
    treated = np.zeros_like(channels)
    source = wet.reshape(-1, CHANNELS)[:frames]
    treated[:len(source)] = source
    shape = envelope(frames, rate, spans)[:, None]
    gain = 10 ** (gain_db / 20)
    return (channels * (1 - shape) + treated * gain * shape).reshape(-1)


def _decode(path, ffmpeg: str, filters: str = "") -> np.ndarray:
    command = [ffmpeg, "-loglevel", "error", "-i", str(path)] + (["-af", filters] if filters else []) + \
        ["-f", "f32le", "-ac", str(CHANNELS), "-ar", str(RATE), "-"]
    return np.frombuffer(subprocess.run(command, check=True, capture_output=True).stdout, dtype=np.float32)


def apply(audio_path, spans: list[tuple[float, float]]) -> int:
    """Rewrite ``audio_path`` (mp3 or wav) with the spans softened; returns how many spans were treated."""
    if not spans:
        return 0
    from video.tape import ffmpeg_exe
    ffmpeg = ffmpeg_exe()
    dry = _decode(audio_path, ffmpeg)
    wet = _decode(audio_path, ffmpeg, f"highpass=f={HIGHPASS_HZ},aecho=1.0:1.0:{REVERB_DELAY_MS}:{REVERB_SEND}")
    mixed = np.clip(mix(dry, wet, spans), -1.0, 1.0)
    path = Path(audio_path)
    temporary = path.with_name(path.stem + ".thoughts" + path.suffix)
    codec = ["-c:a", "libmp3lame", "-q:a", "2"] if path.suffix.lower() == ".mp3" else []
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", str(CHANNELS), "-i", "-",
                    *codec, str(temporary)], input=mixed.astype(np.float32).tobytes(), check=True, capture_output=True)
    os.replace(temporary, path)
    return len(spans)
