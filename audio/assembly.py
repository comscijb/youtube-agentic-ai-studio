"""Narration assembly in PCM (Phase 3.9.4-F/G).

moviepy 1.0.3's ``concatenate_audioclips`` on MP3 files replays its last decoded buffer at each file's
end: an isolated 60-90 ms burst at every scene join. Here every scene is decoded to PCM once, split at its
sentence ends (at the quietest point between two words), trimmed to a consistent threshold and joined with
equal-power fades, so the configured pauses are the only silence between pieces and they are exact. Word
timings follow every cut. The file is encoded once, at the end. Delivery (rate, speed) stays with the TTS
engine: nothing here stretches time.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

import config

RATE = 24000
TRIM_DB = -50.0
KEEP_SECONDS = 0.04
FADE_SECONDS = 0.008
WINDOW_SECONDS = 0.01
DEFAULT_PAUSES = {"sentence": 0.35, "paragraph": 0.7, "scene": 1.5, "major_scene": 2.0, "final": 2.5}
MAJOR_SCENES = {"climax", "aftermath"}


def pauses() -> dict:
    """Seconds of silence between pieces (``config.NARRATION_PAUSES`` overrides the defaults)."""
    return {**DEFAULT_PAUSES, **(getattr(config, "NARRATION_PAUSES", None) or {})}


def decode(path, rate: int = RATE) -> np.ndarray:
    """Mono float32 samples via the project's ffmpeg."""
    from video.tape import ffmpeg_exe
    raw = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def encode(samples: np.ndarray, path, rate: int = RATE) -> None:
    """One encode: mp3 at 44.1 kHz stereo, or PCM WAV (by extension)."""
    from video.tape import ffmpeg_exe
    target = Path(path)
    codec = ["-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3"] if target.suffix.lower() == ".mp3" \
        else ["-c:a", "pcm_s16le", "-f", "wav"]
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "-", *codec, str(target)],
                   input=np.clip(samples, -1.0, 1.0).astype(np.float32).tobytes(), check=True, capture_output=True)


def _rms_db(samples: np.ndarray, rate: int) -> np.ndarray:
    """RMS in dBFS per 10 ms window."""
    size = max(1, int(WINDOW_SECONDS * rate))
    count = len(samples) // size
    if not count:
        return np.zeros(0)
    power = np.mean(np.square(samples[:count * size].reshape(count, size)), axis=1)
    return 10 * np.log10(power + 1e-12)


def trim_bounds(samples: np.ndarray, rate: int = RATE, threshold_db: float = TRIM_DB,
                keep: float = KEEP_SECONDS) -> tuple[int, int]:
    """First and one-past-last sample after dropping leading and trailing silence, keeping ``keep`` seconds."""
    levels = _rms_db(samples, rate)
    loud = np.flatnonzero(levels > threshold_db)
    if not len(loud):
        return 0, 0
    size = max(1, int(WINDOW_SECONDS * rate))
    pad = int(keep * rate)
    return max(0, int(loud[0]) * size - pad), min(len(samples), (int(loud[-1]) + 1) * size + pad)


def with_fades(samples: np.ndarray, rate: int = RATE, seconds: float = FADE_SECONDS) -> np.ndarray:
    """Equal-power fade in and out (5-10 ms): no click at any join."""
    count = min(int(seconds * rate), len(samples) // 2)
    if count <= 0:
        return samples
    faded = samples.copy()
    ramp = np.sin(np.linspace(0.0, np.pi / 2, count, dtype=np.float32))
    faded[:count] *= ramp
    faded[-count:] *= ramp[::-1]
    return faded


def join(pieces: list[np.ndarray], rate: int = RATE) -> np.ndarray:
    """Pieces of one line-level scene, faded at every join, no silence added."""
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate([with_fades(piece, rate) for piece in pieces])


def paragraph_ends(text: str, words: list[dict]) -> set[int]:
    """Indexes in ``words`` of the last word of each paragraph but the last. ``words`` come from
    ``align_words`` (punctuation-only tokens joined the previous word) or ``proportional_words``."""
    from video.captions import _norm
    paragraphs = [part for part in re.split(r"\n\s*\n", str(text)) if part.strip()]
    tokens = str(text).split()
    aligned = len(tokens) == len(words)
    ends, count = set(), 0
    for paragraph in paragraphs[:-1]:
        count += sum(1 for token in paragraph.split() if aligned or _norm(token))
        ends.add(count - 1)
    return ends


def _cut_between(samples: np.ndarray, rate: int, end: float, start: float) -> int:
    """The quietest sample position between the end of one word and the start of the next."""
    first, last = int(end * rate), max(int(start * rate), int(end * rate) + 1)
    first, last = max(0, first), min(len(samples), last)
    if last - first <= 1:
        return first
    size = max(1, int(WINDOW_SECONDS * rate))
    region = samples[first:last]
    if len(region) < size * 2:
        return (first + last) // 2
    levels = _rms_db(region, rate)
    return first + int(np.argmin(levels)) * size + size // 2


def _scene_pieces(scene: dict, rate: int) -> list[dict]:
    """A scene cut at its sentence ends: [{"samples", "offset", "words", "after"}]; "after" names the pause."""
    samples, words = scene["samples"], scene["words"]
    breaks = paragraph_ends(scene.get("text", ""), words)
    pieces, begin_sample, begin_word = [], 0, 0
    for index, word in enumerate(words[:-1]):
        if not word.get("sentence_end") and index not in breaks:
            continue
        cut = _cut_between(samples, rate, float(word["end"]), float(words[index + 1]["start"]))
        if cut <= begin_sample:
            continue
        pieces.append({"samples": samples[begin_sample:cut], "offset": begin_sample, "words": words[begin_word:index + 1],
                       "after": "paragraph" if index in breaks else "sentence"})
        begin_sample, begin_word = cut, index + 1
    pieces.append({"samples": samples[begin_sample:], "offset": begin_sample, "words": words[begin_word:], "after": "scene"})
    return pieces


def assemble(scenes: list[dict], rate: int = RATE, settings: dict | None = None) -> dict:
    """``scenes``: [{"samples", "words", "text", "section_type", "id"}], word times relative to the scene.

    Returns {"samples", "words" (global times, "pause_after" on the last word before a pause), "durations"
    (a scene ends in the middle of the pause after it, so a transition is centred in the silence),
    "pauses" ([{"at", "seconds", "kind"}])}."""
    wanted = settings or pauses()
    chunks, placed_words, pause_log, durations = [], [], [], []
    cursor, scene_start = 0, 0.0
    for number, scene in enumerate(scenes):
        pieces = _scene_pieces(scene, rate)
        last_scene = number == len(scenes) - 1
        for position, piece in enumerate(pieces):
            first, last = trim_bounds(piece["samples"], rate)
            body = with_fades(piece["samples"][first:last], rate)
            lead = (piece["offset"] + first) / rate
            length = len(body) / rate
            base = cursor / rate
            for word in piece["words"]:
                start = min(max(float(word["start"]) - lead, 0.0), length)
                end = min(max(float(word["end"]) - lead, start), length)
                placed_words.append({**word, "start": round(base + start, 3), "end": round(base + end, 3),
                                     "scene_id": scene.get("id")})
            chunks.append(body)
            cursor += len(body)
            final_piece = position == len(pieces) - 1
            if final_piece and last_scene:
                continue
            if final_piece:
                major = str(scenes[number + 1].get("section_type", "")) in MAJOR_SCENES
                kind, seconds = "scene", wanted["major_scene" if major else "scene"]
            elif last_scene and position == len(pieces) - 2 and piece["after"] == "paragraph":
                kind, seconds = "final", wanted["final"]           # the hold before the closing line
            else:
                kind, seconds = piece["after"], wanted[piece["after"]]
            gap = int(round(seconds * rate))
            pause_log.append({"at": round(cursor / rate, 3), "seconds": gap / rate, "kind": kind})
            if final_piece:
                boundary = (cursor + gap / 2) / rate
                durations.append(boundary - scene_start)
                scene_start = boundary
            chunks.append(np.zeros(gap, dtype=np.float32))
            cursor += gap
    durations.append(cursor / rate - scene_start)
    samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return {"samples": samples, "words": placed_words, "durations": durations, "pauses": pause_log}


def estimate(sections: list[dict], words: list[dict], speed_ratio: float = 1.0, settings: dict | None = None) -> dict:
    """Seconds this narration would take, from its word timings, without synthesising anything:
    spoken time scaled by ``speed_ratio`` (old speed / new speed), plus the configured pauses."""
    wanted = settings or pauses()
    by_scene: dict = {}
    for word in words:
        by_scene.setdefault(word.get("scene_id"), []).append(word)
    speech = pause_total = 0.0
    for number, section in enumerate(sections):
        items = by_scene.get(section.get("id")) or []
        if not items:
            continue
        breaks = paragraph_ends(section.get("narration", ""), items)
        begin = float(items[0]["start"])
        for index, word in enumerate(items[:-1]):
            if word.get("sentence_end") or index in breaks:
                speech += max(0.0, float(word["end"]) - begin) + 2 * KEEP_SECONDS
                begin = float(items[index + 1]["start"])
                pause_total += wanted["paragraph" if index in breaks else "sentence"]
        speech += max(0.0, float(items[-1]["end"]) - begin) + 2 * KEEP_SECONDS
        if number + 1 < len(sections):
            major = str(sections[number + 1].get("section_type", "")) in MAJOR_SCENES
            pause_total += wanted["major_scene" if major else "scene"]
    if breaks_last := paragraph_ends(sections[-1].get("narration", ""), by_scene.get(sections[-1].get("id")) or []) if sections else set():
        pause_total += wanted["final"] - wanted["paragraph"] if breaks_last else 0.0
    total = speech * speed_ratio + pause_total
    return {"seconds": round(total, 1), "minutes": round(total / 60, 2), "speech_seconds": round(speech * speed_ratio, 1),
            "pause_seconds": round(pause_total, 1)}
