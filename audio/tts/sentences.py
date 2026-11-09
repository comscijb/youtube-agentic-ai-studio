"""One sentence per Kokoro call (Phase 3.9.5-A).

``Kokoro.create`` cuts anything over 510 phonemes into batches, synthesizes them with no context between them and
concatenates them, and a whole scene is around 1000 phonemes. Sending sentences keeps every request inside one batch
and leaves every join here, at a real pause. It also removes a second source of stutter: nothing has to cut a continuous
scene at word timings any more, and a recogniser's timing can be wrong by a whole phoneme.

A sentence over ~350 phonemes is split at a clause boundary (a ``clause`` pause). Clips are cached per sentence: editing
one sentence re-synthesizes only that sentence.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Callable

import numpy as np

from audio import assembly
from audio.qa import stutter
from audio.tts import align

SOFT_LIMIT = 350
CONJUNCTIONS = frozenset("and but or nor so yet for while though although because when as which who".split())
ABBREVIATION = re.compile(r"^(?:Mr|Mrs|Ms|Dr|St|Mt|Prof|Rev)\.$")
OPENING = ('"', "“", "'", "‘")
CLOSING = ('"', "”", "'", "’")
SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
MIN_WORDS = 3


def _inside_after(token: str, inside: bool) -> bool:
    """Whether a quotation is open after ``token``. An apostrophe inside a word is not a quote."""
    if not inside and token.lstrip("([—-")[:1] in OPENING:
        inside = True
        token = token.lstrip("([—-")[1:]
    core = token.rstrip(".,!?;:…)]—-")
    if inside and core[-1:] in CLOSING:
        inside = False
    return inside


def split_text(text: str) -> list[dict]:
    """[{"text", "after"}] by the captions' sentence rule; a quotation stays whole. "after": sentence, paragraph or scene."""
    found = []
    paragraphs = [part for part in re.split(r"\n\s*\n", str(text)) if part.strip()]
    for number, paragraph in enumerate(paragraphs):
        current, inside, count = [], False, len(found)
        for token in paragraph.split():
            current.append(token)
            inside = _inside_after(token, inside)
            if not inside and SENTENCE_END.search(token) and not ABBREVIATION.match(token):
                found.append({"text": " ".join(current), "after": "sentence"})
                current = []
        if current:
            found.append({"text": " ".join(current), "after": "sentence"})
        if len(found) > count:
            found[-1]["after"] = "scene" if number == len(paragraphs) - 1 else "paragraph"
    return found


def boundaries(text: str) -> list[dict]:
    """Every place ``text`` could be split: {"after" (word count), "rank"}. 0 a semicolon, 1 a comma before a conjunction,
    2 a comma; inside a quotation (only when nothing else is left) 3 a sentence end, 4 a comma; 5 anywhere."""
    words = text.split()
    found, inside = [], False
    for index, token in enumerate(words[:-1]):
        inside = _inside_after(token, inside)
        following = align.normalise(words[index + 1])
        if inside:
            rank = 3 if SENTENCE_END.search(token) else 4 if token.endswith((",", ";")) else 5
        elif token.endswith(";"):
            rank = 0
        elif token.endswith(","):
            rank = 1 if following in CONJUNCTIONS else 2
        else:
            rank = 5
        found.append({"after": index + 1, "rank": rank})
    return found


def best_boundary(text: str, max_rank: int = 5) -> int | None:
    """Words before the best split: the lowest rank, then nearest the middle, avoiding fragments of under 3 words."""
    words = text.split()
    middle = len(" ".join(words)) / 2

    def distance(item: dict) -> float:
        return abs(len(" ".join(words[:item["after"]])) - middle)

    choices = [item for item in boundaries(text) if item["rank"] <= max_rank]
    roomy = [item for item in choices if item["after"] >= MIN_WORDS and len(words) - item["after"] >= MIN_WORDS]
    pool = roomy or choices
    if not pool:
        return None
    return min(pool, key=lambda item: (item["rank"], distance(item)))["after"]


def split_long(text: str, count: Callable[[str], int], limit: int = SOFT_LIMIT) -> list[str]:
    """``text`` in pieces of at most ``limit`` phonemes."""
    words = text.split()
    if count(text) <= limit or len(words) < 2:
        return [text]
    cut = best_boundary(text)
    return split_long(" ".join(words[:cut]), count, limit) + split_long(" ".join(words[cut:]), count, limit)


def plan(text: str, count: Callable[[str], int], limit: int = SOFT_LIMIT) -> list[dict]:
    """The sentences of a scene, long ones split: the join inside a sentence is a ``clause``."""
    pieces = []
    for sentence in split_text(text):
        parts = split_long(sentence["text"], count, limit)
        pieces += [{"text": part, "after": "clause"} for part in parts[:-1]]
        pieces.append({"text": parts[-1], "after": sentence["after"]})
    return pieces


def cache_key(spoken: str, *, backend: str, voice: str, speed: float, model: str) -> str:
    """Backend + voice + speed + model version + the normalized sentence."""
    payload = json.dumps([backend, voice, round(float(speed), 3), model, " ".join(str(spoken).split())], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timed(samples: np.ndarray, text: str, heard: list[dict]) -> tuple[list[dict], dict]:
    if heard:
        events, coverage = align.match(align.script_words(text), heard)
    else:                                                # a word too short for the recogniser: spread over the clip
        words = align.script_words(text)
        step = len(samples) / assembly.RATE * 1000 / max(len(words), 1)
        events = [{"text": word, "start_ms": step * index, "end_ms": step * (index + 1), "interpolated": True}
                  for index, word in enumerate(words)]
        coverage = 0.0
    return events, align.validate(events, coverage)


class SceneSynthesis:
    """One scene through ``synth(text, voice, speed, path)``; ``count(text)`` is the model's phoneme length."""

    def __init__(self, *, voice: str, speed: float, synth: Callable, count: Callable[[str], int], cache_dir=None, backend: str = "local",
                 model: str = "", apply: Callable[[str], str] | None = None, check: bool = True, transcribe=None,
                 progress: Callable[[str], None] = print):
        self.voice, self.speed, self.synth, self.count = voice, float(speed), synth, count
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.backend, self.model = backend, model
        self.apply = apply or (lambda text: text)
        self.check, self.progress = check, progress
        self.transcribe = transcribe or align.transcribe

    def _render(self, text: str, speed: float, folder: str) -> dict:
        path = Path(folder) / f"{hashlib.sha256(f'{speed}{text}'.encode('utf-8')).hexdigest()[:16]}.wav"
        self.synth(text, self.voice, speed, str(path))
        samples = assembly.decode(path)
        heard = self.transcribe(str(path))
        events, report = _timed(samples, text, heard)
        hits = stutter.check(samples, text, heard, events, speed=speed) if self.check else []
        return {"samples": samples, "events": events, "report": report, "hits": hits, "text": text, "speed": speed}

    def _render_split(self, text: str, speed: float, folder: str) -> dict | None:
        """Escalation: two calls joined at a clause boundary, one clip again."""
        cut = best_boundary(text, max_rank=2)
        if cut is None:
            return None
        words = text.split()
        parts = [self._render(" ".join(words[:cut]), speed, folder), self._render(" ".join(words[cut:]), speed, folder)]
        gap = int(round(assembly.pauses()["clause"] * assembly.RATE))
        chunks, events, hits, cursor = [], [], [], 0
        for number, part in enumerate(parts):
            first, last = assembly.trim_bounds(part["samples"])
            body = assembly.with_fades(part["samples"][first:last])
            shift = (cursor - first) / assembly.RATE * 1000
            events += [{**event, "start_ms": max(event["start_ms"] + shift, cursor / assembly.RATE * 1000),
                        "end_ms": event["end_ms"] + shift} for event in part["events"]]
            hits += part["hits"]
            chunks.append(body)
            cursor += len(body)
            if number == 0:
                chunks.append(np.zeros(gap, dtype=np.float32))
                cursor += gap
        coverage = min(part["report"]["coverage"] for part in parts)
        return {"samples": np.concatenate(chunks), "events": events, "hits": hits, "text": text, "speed": speed, "split_after": cut,
                "report": align.validate(events, coverage)}

    def clip(self, spoken: str, folder: str) -> dict:
        """The cached clip, or the best attempt, cached. {"samples", "events", "hits", "report", "tries", "reused", "path"}."""
        key = cache_key(spoken, backend=self.backend, voice=self.voice, speed=self.speed, model=self.model)
        audio = self.cache_dir / f"{key}.wav" if self.cache_dir else None
        notes = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if audio and audio.is_file() and notes.is_file():
            try:
                saved = json.loads(notes.read_text(encoding="utf-8"))
                return {**saved, "samples": assembly.decode(audio), "reused": True, "path": str(audio)}
            except (OSError, ValueError):
                pass
        best = stutter.repair(spoken, self.speed, lambda text, speed: self._render(text, speed, folder),
                              lambda text, speed: self._render_split(text, speed, folder))
        saved = {"events": best["events"], "hits": best["hits"], "report": best["report"], "tries": best["tries"],
                 "text": best["text"], "speed": best["speed"]}
        if audio:
            audio.parent.mkdir(parents=True, exist_ok=True)
            assembly.encode(best["samples"], audio)
            notes.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        return {**saved, "samples": best["samples"], "reused": False, "path": str(audio) if audio else ""}

    def scene(self, text: str, label: str = "") -> dict:
        """{"samples" (clips one after the other), "events" (ms, in that timeline), "cuts", "stats"}."""
        started = time.monotonic()
        pieces = plan(text, lambda part: self.count(self.apply(part)))
        chunks, events, cuts, flagged = [], [], [], []
        cursor = reused = resynthesized = 0
        covered = []
        with tempfile.TemporaryDirectory(prefix="private_gravity_sentences_") as folder:
            for index, piece in enumerate(pieces):
                clip = self.clip(self.apply(piece["text"]), folder)
                shift = cursor / assembly.RATE * 1000
                events += [{**event, "start_ms": event["start_ms"] + shift, "end_ms": event["end_ms"] + shift} for event in clip["events"]]
                chunks.append(clip["samples"])
                cursor += len(clip["samples"])
                cuts.append({"end": cursor / assembly.RATE, "after": piece["after"]})
                covered.append(clip["report"].get("coverage", 1.0))
                reused += bool(clip["reused"])
                resynthesized += bool(clip.get("tries"))
                if clip["hits"]:
                    flagged.append({"index": index, "text": piece["text"], "hits": clip["hits"], "clip": clip["path"], "tries": clip.get("tries", 0)})
        return {"samples": np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32), "events": events, "cuts": cuts,
                "stats": {"sentences": len(pieces), "reused": reused, "resynthesized": resynthesized, "flagged": flagged,
                          "seconds": round(time.monotonic() - started, 2), "coverage": round(min(covered, default=1.0), 4)}}
