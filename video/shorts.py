"""Shorts cut from the episode — the plan (Phase 3.7-B.1).

For a new channel Shorts are the main discovery engine, so every long episode
gets ``SHORTS_PER_EPISODE`` (2–3) Shorts cut from its own narration and shots,
reviewed in the same approval gate and uploaded with it.

The ``shorts_plan`` stage runs after narration (it needs real timings):

* ``word_timeline`` — every narrated word with its global time
  (``narration_words.json``, written by the narrator; rebuilt from the TTS cache
  and ``scene_timeline.json`` for older projects), grouped into ``sentences``;
* Gemini proposes segments as sentence ranges (contract-first JSON), each with
  a title (≤ 60 characters including ``#Shorts``, through the title validator),
  a hook text and an end text (≤ 6 words each) and a 1–2 line description;
* ``validate`` checks every rule by code: 45–75 s, sentence boundaries, no
  overlap, never past the twist (the start of the episode's reversal/climax
  scene), at most one Short ending in the ``APPROACH_SECONDS`` before it.

The user then adjusts in/out points (snapped to sentences), drops or adds a
segment and edits the texts in the GUI; ``save`` re-validates.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import config
from agents.narrative import (NarrativeValidationError, atomic_write_json, load_json, stable_hash, title_passes,
                              validate_title)

FOLDER = "shorts"
PLAN_FILE = "shorts_plan.json"
WORDS_FILE = "narration_words.json"
APPROACH_SECONDS = 90.0
TITLE_SUFFIX = " #Shorts"
TITLE_MAX = 60
TEXT_MAX_WORDS = 6
DURATION_SLACK = 1.5          # a sentence boundary rarely lands exactly on the limit
TWIST_TYPES = ("reversal", "climax")


def per_episode() -> int:
    return int(getattr(config, "SHORTS_PER_EPISODE", 3))


def limits() -> tuple[float, float]:
    return float(getattr(config, "SHORTS_MIN_SECONDS", 45)), float(getattr(config, "SHORTS_MAX_SECONDS", 75))


# ── Words and sentences ─────────────────────────────────────────────────────

def word_timeline(output_dir, script: dict | None = None) -> list[dict]:
    """[{text, start, end, sentence_end, scene_id}] in episode seconds."""
    output = Path(output_dir)
    saved = load_json(output / WORDS_FILE)
    if saved and saved.get("words"):
        return saved["words"]
    from video.captions import align_words, proportional_words
    from video.narrator import cache_key
    from video.timeline import load_timeline
    script = script or load_json(output / "script.json") or {}
    sections = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
    scenes = (load_timeline(output / "scene_timeline.json") or {}).get("scenes") or []
    if len(scenes) != len(sections):
        return []
    words = []
    for section, scene in zip(sections, scenes):
        text = section["narration"].strip()
        events = load_json(output / ".tts_cache" / f"{cache_key(text)}.json")
        timed = align_words(text, events) if isinstance(events, list) and events else \
            proportional_words(text, float(scene["duration"]))
        words += [{**word, "start": round(word["start"] + float(scene["start"]), 3),
                   "end": round(word["end"] + float(scene["start"]), 3), "scene_id": section.get("id")}
                  for word in timed]
    return words


def sentences(words: list[dict]) -> list[dict]:
    """[{index, start, end, text, scene_id, first_word, last_word}] — the only legal cut points."""
    found, current = [], []
    for position, word in enumerate(words):
        current.append(position)
        if word.get("sentence_end") or position == len(words) - 1:
            first, last = words[current[0]], words[current[-1]]
            found.append({"index": len(found), "start": first["start"], "end": last["end"],
                          "text": " ".join(words[item]["text"] for item in current), "scene_id": first.get("scene_id"),
                          "first_word": current[0], "last_word": current[-1]})
            current = []
    return found


def twist_start(script: dict, timeline: dict | None, total: float) -> float:
    """Where the episode's final twist begins: the first reversal/climax scene (else 70 %)."""
    sections = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
    scenes = (timeline or {}).get("scenes") or []
    for section, scene in zip(sections, scenes):
        if str(section.get("section_type", "")).lower() in TWIST_TYPES:
            return float(scene["start"])
    return round(total * 0.7, 3)


# ── Validation ──────────────────────────────────────────────────────────────

def short_title(title: str) -> str:
    title = re.sub(r"\s*#shorts\b", "", str(title or ""), flags=re.I).strip()
    return title + TITLE_SUFFIX if title else ""


def validate(segments: list[dict], sentence_list: list[dict], *, twist: float, brief: dict | None = None,
             count: tuple[int, int] | None = None) -> list[dict]:
    """Clean segments with their times; NarrativeValidationError listing every broken rule."""
    low, high = limits()
    count = count or (2, per_episode())
    problems, clean = [], []
    if not isinstance(segments, list) or not count[0] <= len(segments) <= count[1]:
        raise NarrativeValidationError(f"the plan needs {count[0]}-{count[1]} segments")
    for number, item in enumerate(segments, 1):
        label = f"segment {number}"
        try:
            first, last = int(item["start_sentence"]), int(item["end_sentence"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{label}: start_sentence and end_sentence must be sentence indexes")
            continue
        if not (0 <= first <= last < len(sentence_list)):
            problems.append(f"{label}: sentence indexes {first}-{last} outside 0-{len(sentence_list) - 1}")
            continue
        start, end = sentence_list[first]["start"], sentence_list[last]["end"]
        seconds = end - start
        if not low - DURATION_SLACK <= seconds <= high + DURATION_SLACK:
            problems.append(f"{label}: {seconds:.0f} s (must be {low:.0f}-{high:.0f} s)")
        if end > twist + 0.05:
            problems.append(f"{label}: ends at {end:.0f} s, after the twist starts ({twist:.0f} s) — never reveal it")
        title = short_title(item.get("title", ""))
        if not title or len(title) > TITLE_MAX:
            problems.append(f"{label}: title must be 1-{TITLE_MAX} characters including '#Shorts'")
        elif not title_passes(validate_title(title, brief or {})):
            problems.append(f"{label}: title {title!r} breaks the title policy")
        for key in ("hook_text", "end_text"):
            words = str(item.get(key, "")).split()
            if not 1 <= len(words) <= TEXT_MAX_WORDS:
                problems.append(f"{label}: {key} must be 1-{TEXT_MAX_WORDS} words")
        description = str(item.get("description", "")).strip()
        if not description or len(description) > 300:
            problems.append(f"{label}: description must be 1-2 lines (max 300 characters)")
        clean.append({"start_sentence": first, "end_sentence": last, "start": round(start, 3), "end": round(end, 3),
                      "seconds": round(seconds, 2), "title": title, "hook_text": str(item.get("hook_text", "")).strip(),
                      "end_text": str(item.get("end_text", "")).strip(), "description": description,
                      "approaches_reversal": end > twist - APPROACH_SECONDS,
                      "why": str(item.get("why", "")).strip()[:300],
                      "include": bool(item.get("include", True))})
    ordered = sorted(clean, key=lambda item: item["start"])
    for before, after in zip(ordered, ordered[1:]):
        if after["start"] < before["end"] - 0.01:
            problems.append(f"segments overlap ({before['start']:.0f}-{before['end']:.0f} s and "
                            f"{after['start']:.0f}-{after['end']:.0f} s)")
    if sum(1 for item in clean if item["approaches_reversal"]) > 1:
        problems.append("only one Short may approach the reversal")
    if problems:
        raise NarrativeValidationError("shorts plan: " + "; ".join(problems))
    for number, item in enumerate(ordered, 1):
        item["id"] = f"short_{number:02d}"
    return ordered


# ── Gemini plan ─────────────────────────────────────────────────────────────

def teaser_end(script: dict, sentence_list: list[dict], timeline: dict | None) -> int | None:
    """Index of the last sentence of the cold open (Phase 3.9.7), or None."""
    sections = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
    scenes = (timeline or {}).get("scenes") or []
    if not sections or sections[0].get("section_type") != "cold_open" or not scenes:
        return None
    end = float(scenes[0].get("start", 0)) + float(scenes[0].get("duration", 0))
    inside = [item["index"] for item in sentence_list if item["start"] < end]
    return inside[-1] if inside else None


def contract(count: int, twist_index: int, teaser: int | None = None) -> str:
    low, high = limits()
    opener = (f"\nSentences 0-{teaser} are the episode's COLD OPEN, a teaser that stops before the payoff: it makes an "
              "ideal Short opener, so one segment may start at sentence 0 (the twist rule above still holds).") if teaser is not None else ""
    return f"""You cut YouTube Shorts from one Private Gravity episode (cinematic psychological fiction). The
narration is given as numbered SENTENCES with their times. Choose {count} segments (or at least 2), each a
contiguous range of whole sentences lasting {low:.0f}-{high:.0f} seconds, not overlapping, chosen for:
- a HOOK in the first 2 seconds: the first sentence is a striking line or situation, never scene-setting;
- self-contained clarity: understandable without the episode;
- an ending on tension or a cliffhanger.
NEVER reveal the episode's final twist: every segment must end before sentence {twist_index} (where the
reversal begins). At most one segment may come close to it, and it must stop before the payoff.

Return ONLY JSON {{"segments": [{{"start_sentence": int, "end_sentence": int, "title": "curiosity title,
max 51 characters (#Shorts is added)", "hook_text": "max 6 words shown on screen for the first 2 s",
"end_text": "max 6 words, e.g. Full story on the channel", "description": "1-2 lines, no spoilers",
"why": "one sentence: why this cut works"}}]}}. Titles follow the channel title policy: present a person and
a situation, never explain the theme.""" + opener


def build_plan(output_dir, *, script: dict | None = None, brief: dict | None = None, generator=None,
               force: bool = False) -> dict:
    """Gemini proposes, ``validate`` decides; saved in shorts/shorts_plan.json (cached per narration)."""
    from agents.gemini_client import generate
    from agents.identity import TITLE_POLICY
    from agents.structured import compact_json, generate_json
    from video.timeline import load_timeline
    output = Path(output_dir)
    script = script or load_json(output / "script.json") or {}
    brief = brief or load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    words = word_timeline(output, script)
    sentence_list = sentences(words)
    if not sentence_list:
        raise FileNotFoundError("no narration timings: run the narration step first")
    total = float(words[-1]["end"])
    twist = twist_start(script, load_timeline(output / "scene_timeline.json"), total)
    twist_index = next((item["index"] for item in sentence_list if item["start"] >= twist - 0.05), len(sentence_list))
    source = stable_hash({"sentences": [(item["start"], item["text"]) for item in sentence_list], "twist": twist,
                          "count": per_episode(), "limits": limits()})
    path = output / FOLDER / PLAN_FILE
    saved = load_json(path) or {}
    if saved.get("source_hash") == source and not force:
        return saved
    listing = [{"i": item["index"], "t": f"{item['start']:.1f}-{item['end']:.1f}", "text": item["text"]}
               for item in sentence_list if item["start"] < twist]
    story = {key: brief.get(key, "") for key in ("logline", "psychological_theme", "video_title")}
    value = generate_json(
        contract(per_episode(), twist_index, teaser_end(script, sentence_list, load_timeline(output / "scene_timeline.json")))
        + "\n\n" + TITLE_POLICY, "shorts plan",
        lambda value: {"segments": validate(value.get("segments") if isinstance(value, dict) else None,
                                            sentence_list, twist=twist, brief=brief)},
        generator=generator or generate, context=f"STORY: {compact_json(story)}\nSENTENCES: {compact_json(listing)}",
        stage="shorts_plan", temperature_key="packaging", run_dir=str(output))
    plan = {"source_hash": source, "twist_start": twist, "twist_sentence": twist_index, "total_duration": total,
            "segments": value["segments"], "edited": False}
    atomic_write_json(path, plan)
    return plan


def save(output_dir, segments: list[dict], *, brief: dict | None = None) -> dict:
    """The user's edits (in/out points as sentence indexes, texts, include) re-validated by code."""
    output = Path(output_dir)
    plan = load_json(output / FOLDER / PLAN_FILE) or {}
    sentence_list = sentences(word_timeline(output))
    brief = brief or load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    twist = float(plan.get("twist_start") or (sentence_list[-1]["end"] if sentence_list else 0))
    plan.update(segments=validate(segments, sentence_list, twist=twist, brief=brief, count=(1, max(per_episode(), 5))),
                edited=True)
    atomic_write_json(output / FOLDER / PLAN_FILE, plan)
    return plan


def load(output_dir) -> dict | None:
    return load_json(Path(output_dir) / FOLDER / PLAN_FILE)


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
