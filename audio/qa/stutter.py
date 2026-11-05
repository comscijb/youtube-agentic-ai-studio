"""Stutter check for synthesized speech (Phase 3.9.5-B).

Compares what faster-whisper heard with the sentence that was sent, and looks at the waveform itself, because the
recogniser often normalizes a stutter away:

* **repetition:** a word, word sequence or syllable heard twice in a row where the text has it once;
* **insertion or omission** of a content word;
* **duration:** a word lasting over 2.5x the median of words of its length;
* **acoustic:** inside a word, a short window that repeats right after itself. A sustained vowel is also self-similar,
  at any multiple of its pitch period, so a window that is periodic at pitch lags does not count.

``repair`` re-synthesizes a flagged sentence (speed, then a comma, then a split) and keeps the best attempt.
"""
from __future__ import annotations

import difflib
import re
from typing import Callable

import numpy as np

from audio.tts import align

MAX_TRIES = 3
SPEED_STEP = 0.02
DURATION_RATIO = 2.5
DURATION_FLOOR = 0.55
VOICED_DB = -40.0
# Median spoken seconds by letters, at speed 1.0 (scaled by 1/speed); measured on bm_lewis, see the 3.9.5 report.
MEDIAN_BASE = 0.10
MEDIAN_PER_LETTER = 0.045
ACOUSTIC_RATE = 12000
WINDOW = 0.06
HOP = 0.01
MAX_LAG = 0.25
WORD_MARGIN = 0.02
# The prompt's 0.90 is what natural speech reaches: it fires about as often with the batch joins as without them (3.9.5 report).
# A literal repeat is near 1.0.
ACOUSTIC_NCC = 0.97
PITCH_LAGS = (0.0025, 0.0125)
PERIODIC_NCC = 0.70
MIN_LEVEL_DB = -38.0
STOPWORDS = frozenset("""a an and are as at be been but by can could did do does for from had has have he her him his how i if in
into is it its me my no nor not of on or our she so than that the their them then there these they this those to too up us was
we were what when where which while who why will with would yet you your s t d ll re ve m""".split())
NUMBER_WORDS = frozenset("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen
seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million""".split())


def tokens(words: list[str]) -> list[tuple[str, int]]:
    """(normalized token, index of the source word); a hyphen separates tokens ("fla-flame", "flour-dusted")."""
    found = []
    for index, word in enumerate(words):
        for part in re.split(r"[-‐-―]+", str(word)):
            value = align.normalise(part)
            if value:
                found.append((value, index))
    return found


def _numeric(token: str) -> bool:
    return token in NUMBER_WORDS or any(char.isdigit() for char in token)


def _doubled(sequence: list[str], gram: tuple[str, ...]) -> int:
    size = len(gram)
    return sum(1 for start in range(len(sequence) - 2 * size + 1)
               if tuple(sequence[start:start + size]) == gram and tuple(sequence[start + size:start + 2 * size]) == gram)


def repetitions(expected: list[str], heard: list[dict]) -> list[dict]:
    """Words or sequences doubled in the transcript but not in the text; a syllable heard before its word."""
    wanted = [value for value, _ in tokens(expected)]
    parts = tokens([item["text"] for item in heard])
    said = [value for value, _ in parts]
    hits, used = [], set()
    for size in (3, 2, 1):
        for start in range(len(said) - 2 * size + 1):
            gram = tuple(said[start:start + size])
            if tuple(said[start + size:start + 2 * size]) != gram or start in used:
                continue
            if _doubled(said, gram) <= _doubled(wanted, gram):
                continue
            used.update(range(start, start + 2 * size))
            hits.append({"kind": "repetition", "word": " ".join(gram), "at": heard[parts[start][1]]["start_ms"] / 1000})
    known = set(wanted)
    for index, (value, source) in enumerate(parts[:-1]):
        following = parts[index + 1][0]
        if len(value) >= 2 and value not in known and following != value and following.startswith(value) and following in known:
            hits.append({"kind": "repetition", "word": f"{value}-{following}", "at": heard[source]["start_ms"] / 1000})
    return hits


def _content(token: str) -> bool:
    return len(token) >= 3 and token not in STOPWORDS and not _numeric(token)


def _same_letters(left: list[str], right: list[str]) -> bool:
    """"flour board" heard as "flourboard" is the same speech."""
    return "".join(left) == "".join(right)


def word_changes(expected: list[str], heard: list[dict]) -> list[dict]:
    """A content word inserted or left out. A misheard word (a replacement) is the recogniser's error, not a hit, and
    so is hearing nothing at all (a one-word sentence can be too short for it)."""
    if not heard:
        return []
    wanted_parts = tokens(expected)
    said_parts = tokens([item["text"] for item in heard])
    wanted = [value for value, _ in wanted_parts]
    said = [value for value, _ in said_parts]
    hits = []
    matcher = difflib.SequenceMatcher(a=wanted, b=said, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "delete":
            for token, source in wanted_parts[a0:a1]:
                if _content(token):
                    at = heard[said_parts[min(b0, len(said_parts) - 1)][1]]["start_ms"] / 1000 if said_parts else 0.0
                    hits.append({"kind": "omission", "word": token, "index": source, "at": at})
        elif tag == "insert":
            for token, source in said_parts[b0:b1]:
                if _content(token) and token not in wanted[max(0, a0 - 2):a0 + 2]:
                    hits.append({"kind": "insertion", "word": token, "at": heard[source]["start_ms"] / 1000})
    return hits


def median_for(letters: int, speed: float = 1.0) -> float:
    return (MEDIAN_BASE + MEDIAN_PER_LETTER * letters) / max(speed, 0.1)


def voiced_seconds(samples: np.ndarray, begin: float, end: float, rate: int) -> float:
    """Time above -40 dB in [begin, end]."""
    size = max(1, int(0.01 * rate))
    piece = samples[max(0, int(begin * rate)):max(0, int(end * rate))]
    count = len(piece) // size
    if not count:
        return 0.0
    power = np.mean(np.square(piece[:count * size].reshape(count, size)), axis=1)
    return float(np.sum(10 * np.log10(power + 1e-12) > VOICED_DB)) * 0.01


def long_words(events: list[dict], speed: float = 1.0, samples: np.ndarray | None = None, rate: int = 24000) -> list[dict]:
    """A word far longer than its length explains. Words before a pause are skipped: the recogniser stretches them. With
    ``samples``, only the voiced time counts: the recogniser also gives a word the natural silence before it ("and" spanning
    0.74 s held 0.22 s of voice), and every hit on this project's 129 sentences was that."""
    hits = []
    for index, event in enumerate(events):
        # the first word absorbs the clip's leading silence, the last one and those before punctuation the pause
        if event.get("interpolated") or re.search(r"[^\w'’]+$", str(event.get("text", ""))) or index in (0, len(events) - 1):
            continue
        letters = len(align.normalise(event.get("text", "")))
        seconds = (float(event["end_ms"]) - float(event["start_ms"])) / 1000
        if samples is not None:
            seconds = voiced_seconds(samples, float(event["start_ms"]) / 1000, float(event["end_ms"]) / 1000, rate)
        if letters and seconds > DURATION_FLOOR and seconds > DURATION_RATIO * median_for(letters, speed):
            hits.append({"kind": "duration", "word": str(event["text"]), "index": index, "at": float(event["start_ms"]) / 1000,
                         "seconds": round(seconds, 3)})
    return hits


def _resample(samples: np.ndarray, rate: int) -> np.ndarray:
    """To ACOUSTIC_RATE from any rate: every time below is computed at that rate."""
    if rate == ACOUSTIC_RATE:
        return samples
    if rate % ACOUSTIC_RATE == 0:
        step = rate // ACOUSTIC_RATE
        usable = len(samples) // step * step
        return samples[:usable].reshape(-1, step).mean(axis=1)
    count = int(len(samples) * ACOUSTIC_RATE / rate)
    return np.interp(np.arange(count) * rate / ACOUSTIC_RATE, np.arange(len(samples)), samples).astype(np.float32)


def _best_ncc(window: np.ndarray, region: np.ndarray) -> tuple[float, int]:
    """Highest normalized cross-correlation of ``window`` against every position of ``region``."""
    size = len(window)
    if len(region) < size:
        return 0.0, 0
    length = len(region) + size
    numerator = np.fft.irfft(np.fft.rfft(region, length) * np.fft.rfft(window[::-1], length), length)[size - 1:len(region)]
    squares = np.concatenate(([0.0], np.cumsum(np.square(region, dtype=np.float64))))
    energy = squares[size:] - squares[:-size]
    denominator = np.sqrt(np.maximum(energy, 0.0) * float(np.dot(window, window))) + 1e-9
    values = numerator / denominator
    values[energy < size * 10 ** (MIN_LEVEL_DB / 10)] = 0.0      # near silence: the ratio is only rounding noise
    position = int(np.argmax(values))
    return float(values[position]), position


def repeated_segment(samples: np.ndarray, begin: float, end: float, rate: int = ACOUSTIC_RATE) -> dict | None:
    """The strongest window inside [begin, end] (seconds) that repeats right after itself, or None."""
    size, hop = int(WINDOW * rate), max(1, int(HOP * rate))
    first_lag, last_lag = size, int(MAX_LAG * rate)
    short = int(PITCH_LAGS[0] * rate), int(PITCH_LAGS[1] * rate)
    stop = min(int(end * rate), len(samples))
    best = None
    for start in range(max(0, int(begin * rate)), stop - 2 * size + 1, hop):
        window = samples[start:start + size]
        if 10 * np.log10(np.mean(np.square(window)) + 1e-12) < MIN_LEVEL_DB:
            continue
        reach = min(start + last_lag + size, stop)
        score, position = _best_ncc(window, samples[start + first_lag:reach])
        if score < ACOUSTIC_NCC or (best and score <= best["ncc"]):
            continue
        pitch, _ = _best_ncc(window[:size - short[1]], samples[start + short[0]:start + size])
        if pitch >= PERIODIC_NCC:                       # a steady vowel: self-similar by nature
            continue
        best = {"ncc": round(score, 3), "lag": round((first_lag + position) / rate, 3), "at": round(start / rate, 3)}
    return best


def acoustic(samples: np.ndarray, events: list[dict], rate: int = 24000) -> list[dict]:
    reduced = _resample(np.asarray(samples, dtype=np.float32), rate)
    hits = []
    for index, event in enumerate(events):
        if event.get("interpolated"):
            continue
        found = repeated_segment(reduced, float(event["start_ms"]) / 1000 - WORD_MARGIN, float(event["end_ms"]) / 1000 + WORD_MARGIN)
        if found:
            hits.append({"kind": "acoustic", "word": str(event.get("text", "")), "index": index, **found})
    return hits


def check(samples: np.ndarray, text: str, heard: list[dict], events: list[dict], *, speed: float = 1.0, rate: int = 24000) -> list[dict]:
    """Every hit in one sentence clip, by time ({"kind", "word", "at"}, times relative to the clip)."""
    expected = align.script_words(text)
    hits = repetitions(expected, heard) + word_changes(expected, heard) + long_words(events, speed, samples, rate) + acoustic(samples, events, rate)
    return sorted(hits, key=lambda item: item.get("at", 0.0))


def comma_variant(text: str, hit: dict | None) -> str | None:
    """Move the breath near the hit: drop a comma within three words, else add one before the word."""
    tokens_ = str(text).split()
    if len(tokens_) < 3:
        return None
    index = (hit or {}).get("index")
    index = len(tokens_) // 2 if index is None else min(int(index), len(tokens_) - 1)
    for distance in range(4):
        for position in (index - distance, index + distance):
            if 0 <= position < len(tokens_) - 1 and tokens_[position].endswith(","):
                tokens_[position] = tokens_[position][:-1]
                return " ".join(tokens_)
    before = max(index - 1, 0)
    if re.search(r"[^\w'’]$", tokens_[before]):
        return None
    tokens_[before] += ","
    return " ".join(tokens_)


def repair(text: str, speed: float, render: Callable[[str, float], dict], split: Callable[[str, float], dict | None]) -> dict:
    """``render`` returns an attempt with "hits". On a hit, at most 3 more tries in order: speed -0.02, a comma near the
    hit, a split at a clause boundary. Returns the attempt with the fewest hits (the earliest on a tie) and "tries"."""
    best = render(text, speed)
    tried = 0
    plans = [lambda hit: render(text, round(speed - SPEED_STEP, 3)),
             lambda hit: (lambda variant: render(variant, speed) if variant else None)(comma_variant(text, hit)),
             lambda hit: split(text, speed)]
    for plan in plans[:MAX_TRIES]:
        if not best.get("hits"):
            break
        attempt = plan(best["hits"][0])
        if attempt is None:
            continue
        tried += 1
        if len(attempt.get("hits", [])) < len(best["hits"]):
            best = attempt
    return {**best, "tries": tried}


def spoken_sentences(words: list[dict]) -> list[list[dict]]:
    """Words of a finished narration grouped by sentence."""
    groups, current = [], []
    for word in words:
        current.append(word)
        if word.get("sentence_end"):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def scan_narration(samples: np.ndarray, words: list[dict], *, speed: float = 1.0, rate: int = 16000, transcribe=None) -> dict:
    """The check over a finished file: each sentence cut out by its word timings, its hits in episode time."""
    transcribe = transcribe or align.transcribe_samples
    margin = int(0.08 * rate)
    found, groups = [], spoken_sentences(words)
    for group in groups:
        begin = max(0, int(float(group[0]["start"]) * rate) - margin)
        piece = samples[begin:int(float(group[-1]["end"]) * rate) + margin]
        text = " ".join(str(word["text"]) for word in group)
        heard = transcribe(piece)
        events, _ = align.match(align.script_words(text), heard)
        hits = check(piece, text, heard, events, speed=speed, rate=rate)
        for hit in hits:
            found.append({**hit, "at": round(begin / rate + float(hit.get("at", 0.0)), 2), "sentence": text})
    return {"sentences": len(groups), "hits": found}
