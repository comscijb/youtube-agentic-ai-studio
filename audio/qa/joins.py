"""Scan a finished narration for bad joins (Phase 3.9.4-G.2).

* **Bursts:** a short non-silent island (30-150 ms) between two near-silent regions, the moviepy
  end-of-file glitch: digital silence, ~80 ms at a flat -30 dB, silence again.
* **Short gaps:** less silence than the configured pause at a scene join (needs the word timings).
"""
from __future__ import annotations

import numpy as np

from audio import assembly

RATE = 16000
WINDOW = 0.01
SILENCE_DB = -60.0
ISLAND_DB = -45.0
ISLAND = (0.03, 0.15)
SIDE_SILENCE = 0.02
EDGE_JUMP_DB = 20.0
GAP_TOLERANCE = 0.1
WORD_MARGIN = 0.02


def _levels(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    return assembly._rms_db(samples, rate)


def bursts(samples: np.ndarray, rate: int = RATE) -> list[dict]:
    """[{"at", "seconds", "level_db"}] for every isolated island. Windows are 10 ms, so a second pass half a
    window later catches a burst whose edge straddles one."""
    shift = int(WINDOW * rate / 2)
    found = _scan(samples, rate)
    for item in _scan(samples[shift:], rate):
        at = round(item["at"] + shift / rate, 2)
        if not any(abs(at - other["at"]) < 0.05 for other in found):
            found.append({**item, "at": at})
    return sorted(found, key=lambda item: item["at"])


def _scan(samples: np.ndarray, rate: int) -> list[dict]:
    levels = _levels(samples, rate)
    loud = levels > SILENCE_DB
    found, index, count = [], 0, len(levels)
    side = max(1, int(SIDE_SILENCE / WINDOW))
    while index < count:
        if not loud[index]:
            index += 1
            continue
        end = index
        while end < count and loud[end]:
            end += 1
        seconds = (end - index) * WINDOW
        before = index >= side and not loud[index - side:index].any()
        after = end + side <= count and not loud[end:end + side].any()
        peak = float(levels[index:end].max())
        sudden = index > 0 and end < count and levels[index] - levels[index - 1] >= EDGE_JUMP_DB             and levels[end - 1] - levels[end] >= EDGE_JUMP_DB       # a word rises and decays; a stale buffer just starts and stops
        if before and after and sudden and ISLAND[0] <= seconds <= ISLAND[1] and peak > ISLAND_DB:
            found.append({"at": round(index * WINDOW, 2), "seconds": round(seconds, 2), "level_db": round(peak, 1)})
        index = end
    return found


def short_gaps(words: list[dict], sections: list[dict], settings: dict | None = None) -> list[dict]:
    """Scene joins with less silence than configured between the last word and the first word."""
    wanted = settings or assembly.pauses()
    found = []
    by_scene: dict = {}
    for word in words:
        by_scene.setdefault(word.get("scene_id"), []).append(word)
    for first, second in zip(sections, sections[1:]):
        before, after = by_scene.get(first.get("id")), by_scene.get(second.get("id"))
        if not before or not after:
            continue
        major = str(second.get("section_type", "")) in assembly.MAJOR_SCENES
        target = wanted["major_scene" if major else "scene"]
        gap = float(after[0]["start"]) - float(before[-1]["end"])
        if gap < target - GAP_TOLERANCE:
            found.append({"at": round(float(before[-1]["end"]), 2), "gap": round(gap, 2), "wanted": target,
                          "scenes": [first.get("id"), second.get("id")]})
    return found


def check(path, words: list[dict] | None = None, sections: list[dict] | None = None) -> dict:
    """{"ok", "bursts", "short_gaps"} of a finished narration file."""
    samples = assembly.decode(path, RATE)
    found = bursts(samples)
    if words:                                       # a short word alone between two pauses is speech, not a glitch
        found = [item for item in found if not any(
            float(word["start"]) - WORD_MARGIN <= item["at"] + item["seconds"] and float(word["end"]) + WORD_MARGIN >= item["at"]
            for word in words)]
    gaps = short_gaps(words, sections) if words and sections else []
    return {"ok": not found and not gaps, "bursts": found, "short_gaps": gaps}


def describe(report: dict) -> list[str]:
    """Stage warnings, with timestamps."""
    lines = [f"ruído isolado em {item['at']:.2f} s ({item['seconds'] * 1000:.0f} ms, {item['level_db']:.0f} dB)"
             for item in report.get("bursts", [])]
    lines += [f"junção curta em {item['at']:.2f} s: {item['gap']:.2f} s de pausa (configurado {item['wanted']:.1f} s)"
              for item in report.get("short_gaps", [])]
    return lines
