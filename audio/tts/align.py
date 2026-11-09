"""Forced alignment: word timings for backends that return only audio (Phase 3.9-A).

The text is known, so this is matching, not transcription: faster-whisper (MIT, CTranslate2; the
lightest reliable local option with word timestamps, CPU-friendly with the ``base.en`` model) gives
timed words, and ``match`` maps them onto the script's words with a sequence alignment that tolerates
inserted, missing and misheard words. Script words the recogniser missed get times interpolated
between their matched neighbours. ``validate`` checks the result: monotonic, coverage ≥ 98 % of the
script's words, and (on a fixture with known times) drift ≤ 80 ms.
"""
from __future__ import annotations

import difflib
import re

import config

MIN_COVERAGE = 0.98
MAX_DRIFT_MS = 80
_model = {}


class AlignerUnavailable(RuntimeError):
    """faster-whisper is not installed (pip install faster-whisper)."""


def normalise(word: str) -> str:
    word = str(word).lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"[^a-z0-9']+", "", word).strip("'")


def script_words(text: str) -> list[str]:
    """The words of ``text`` as captions count them (punctuation kept on the word)."""
    return [token for token in str(text).split() if normalise(token)]


def match(words: list[str], recognised: list[dict]) -> tuple[list[dict], float]:
    """Map recognised words ({"text", "start_ms", "end_ms"}) onto the script ``words``.

    Returns (events for every script word, coverage = matched / len(words)). Unmatched script words
    are interpolated between the surrounding matched words (or spread evenly when none match)."""
    target = [normalise(word) for word in words]
    heard = [normalise(item["text"]) for item in recognised]
    timed: list[dict | None] = [None] * len(words)
    matcher = difflib.SequenceMatcher(a=target, b=heard, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            source = recognised[block.b + offset]
            timed[block.a + offset] = {"start_ms": float(source["start_ms"]), "end_ms": float(source["end_ms"])}
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():   # a misheard word in place of a script word
        if tag == "replace" and a1 - a0 == b1 - b0:
            for offset in range(a1 - a0):
                source = recognised[b0 + offset]
                timed[a0 + offset] = {"start_ms": float(source["start_ms"]), "end_ms": float(source["end_ms"])}
    matched = sum(1 for item in timed if item is not None)
    coverage = matched / len(words) if words else 1.0
    total_end = max((float(item["end_ms"]) for item in recognised), default=0.0)
    index = 0
    while index < len(words):                            # interpolate runs of unmatched words
        if timed[index] is not None:
            index += 1
            continue
        run_end = index
        while run_end < len(words) and timed[run_end] is None:
            run_end += 1
        start = timed[index - 1]["end_ms"] if index > 0 else 0.0
        end = timed[run_end]["start_ms"] if run_end < len(words) else max(total_end, start)
        step = (end - start) / (run_end - index) if run_end > index else 0
        for position in range(index, run_end):
            timed[position] = {"start_ms": start + step * (position - index),
                               "end_ms": start + step * (position - index + 1), "interpolated": True}
        index = run_end
    events = [{"text": word, **item} for word, item in zip(words, timed)]
    return events, coverage


def validate(events: list[dict], coverage: float, truth: list[dict] | None = None) -> dict:
    """{"monotonic", "coverage", "drift_ms" (vs ``truth`` when given), "ok"}."""
    starts = [item["start_ms"] for item in events]
    monotonic = all(b >= a for a, b in zip(starts, starts[1:])) and all(
        item["end_ms"] >= item["start_ms"] for item in events)
    drift = None
    if truth:
        drift = max((abs(a["start_ms"] - b["start_ms"]) for a, b in zip(events, truth)), default=0.0)
    ok = monotonic and coverage >= MIN_COVERAGE and (drift is None or drift <= MAX_DRIFT_MS)
    return {"monotonic": monotonic, "coverage": round(coverage, 4),
            "drift_ms": None if drift is None else round(drift, 1), "ok": ok}


def _whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AlignerUnavailable("forced alignment needs faster-whisper: pip install faster-whisper") from exc
    name = str(getattr(config, "ALIGNER_MODEL", "base.en"))
    if name not in _model:
        _model[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _model[name]


def decode(audio_path: str):
    """16 kHz mono float32 samples via the project's ffmpeg (faster-whisper's own PyAV decoder breaks
    with newer ``av`` releases)."""
    import subprocess

    import numpy as np
    from video.tape import ffmpeg_exe
    raw = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1",
                          "-ar", "16000", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


def transcribe(audio_path: str) -> list[dict]:
    """Recognised words with timings (ms) from faster-whisper."""
    _whisper()                                           # says so plainly when the aligner is missing
    return transcribe_samples(decode(audio_path))


def transcribe_samples(samples) -> list[dict]:
    """Recognised words with timings (ms) from 16 kHz mono float32 samples."""
    segments, _ = _whisper().transcribe(samples, word_timestamps=True, language="en", vad_filter=False, beam_size=1)
    return [{"text": word.word.strip(), "start_ms": word.start * 1000, "end_ms": word.end * 1000}
            for segment in segments for word in (segment.words or [])]


def align(audio_path: str, text: str) -> tuple[list[dict], dict]:
    """Word events for ``text`` spoken in ``audio_path`` and the validation report."""
    events, coverage = match(script_words(text), transcribe(audio_path))
    return events, validate(events, coverage)
