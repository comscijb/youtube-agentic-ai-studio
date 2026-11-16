"""The attention map (Phase 4.1-C): one low-temperature Gemini call per screenplay tags every sentence.

Tags: ``question``, ``open_loop:<id>``, ``close_loop:<id>``, ``reveal``, ``escalation``, ``decision``,
``dialogue_turn``, ``description``, ``reflection``. Each tag carries the exact quote it rests on; a quote that is
not in the sentence is discarded (the same rule as 3.9.2). ``attention_map.json`` keeps the sentences with their
timestamps, cached on the narration; ``blocks`` turns it into the GUI's 15-second timeline.
"""
from __future__ import annotations

import re
from pathlib import Path

from agents import attention
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash

FILE_NAME = "attention_map.json"
TAGS = ("question", "open_loop", "close_loop", "reveal", "escalation", "decision", "dialogue_turn", "description", "reflection")
LOOP_ID = re.compile(r"^[a-z0-9_]{2,40}$")
BLOCK_SECONDS = 15.0
VERSION = 1


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", str(text or "").lower().replace("’", "'"))).strip()


def contract() -> str:
    return f"""You map where a viewer's attention is held in one narrated screenplay. For EVERY numbered sentence
return zero or more tags from: {list(TAGS)}.
- question: the narration or a character asks something the viewer now wants answered
- open_loop:<id> / close_loop:<id>: a story question opens (who did it, what is in the box, will she leave)
  and later closes; <id> is a short snake_case name reused at the close
- reveal: new information that changes how the story is read (a partial reveal counts)
- escalation: the stakes or the pressure rise; decision: a character chooses
- dialogue_turn: a spoken line that changes the situation (not small talk)
- description: setting, weather, appearance, with nothing happening; reflection: inner musing, no event
Every tag carries "quote": the exact words of that sentence it rests on (copied, not paraphrased).
Return ONLY JSON {{"sentences": [{{"index": 0, "tags": [{{"tag": "open_loop:who_signed", "quote": "..."}}]}}, ...]}}."""


def validate(value: dict, sentences: list[dict]) -> dict:
    """Keep only tags with a known kind and a quote found in their sentence; the structure must be sound."""
    items = value.get("sentences") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise NarrativeValidationError("attention map needs a sentences array")
    by_index = {item["index"]: item for item in sentences}
    kept, discarded = [], 0
    for item in items:
        if not isinstance(item, dict) or item.get("index") not in by_index:
            continue
        sentence = by_index[item["index"]]
        tags = []
        for tag in item.get("tags") or []:
            if not isinstance(tag, dict):
                continue
            name = str(tag.get("tag", "")).strip()
            kind, _, loop = name.partition(":")
            quote = str(tag.get("quote", "")).strip()
            if kind not in TAGS or (kind in ("open_loop", "close_loop") and not LOOP_ID.match(loop)) or \
                    (kind not in ("open_loop", "close_loop") and loop):
                discarded += 1
                continue
            if not quote or _norm(quote) not in _norm(sentence["text"]):
                discarded += 1                         # an unverifiable quote is not evidence
                continue
            tags.append({"tag": name, "quote": quote})
        kept.append({"sentence": sentence["index"], "tags": tags})
    if len(kept) < max(1, len(sentences) // 2):
        raise NarrativeValidationError(f"attention map covers {len(kept)} of {len(sentences)} sentences")
    return {"sentences": kept, "discarded_tags": discarded}


def build(output_dir, sections: list[dict], *, generator=None, words_per_minute: float | None = None,
          force: bool = False) -> dict:
    """attention_map.json for these sections (cached on the narration and the rate)."""
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    sentences = attention.timing(sections, words_per_minute)
    key = stable_hash({"narration": [item["text"] for item in sentences], "version": VERSION,
                       "wpm": round(float(words_per_minute or attention.wpm()), 1)})
    path = Path(output_dir) / FILE_NAME
    saved = load_json(path) or {}
    if saved.get("key") == key and not force:
        return saved
    numbered = [{"index": item["index"], "scene": item["scene_id"], "text": item["text"]} for item in sentences]
    value = generate_json(contract(), "attention map", lambda found: validate(found, sentences),
                          generator=generator or generate, context=f"SENTENCES: {compact_json(numbered)}",
                          stage="attention_map", temperature_key="editorial_review", run_dir=str(output_dir))
    tags = {item["sentence"]: item["tags"] for item in value["sentences"]}
    record = {"key": key, "version": VERSION, "duration": attention.duration(sentences),
              "discarded_tags": value["discarded_tags"],
              "sentences": [{**{name: item[name] for name in ("index", "scene_id", "section", "section_type", "title", "text", "start", "end")},
                             "sentence": item["index"], "tags": tags.get(item["index"], [])} for item in sentences]}
    atomic_write_json(path, record)
    return record


def load(output_dir) -> dict | None:
    return load_json(Path(output_dir) / FILE_NAME)


def live_loops(attention_map: dict) -> list[dict]:
    """[{id, start, end}] for the horizontal bars (an unclosed loop runs to the end)."""
    opened, closed = {}, {}
    for item in attention_map.get("sentences", []):
        for tag in item["tags"]:
            kind, _, loop = tag["tag"].partition(":")
            if kind == "open_loop":
                opened.setdefault(loop, item["start"])
            elif kind == "close_loop":
                closed[loop] = item["end"]
    end = attention_map.get("duration", 0)
    return [{"id": loop, "start": start, "end": closed.get(loop, end), "closed": loop in closed}
            for loop, start in sorted(opened.items(), key=lambda pair: pair[1])]


def blocks(attention_map: dict, seconds: float = BLOCK_SECONDS) -> list[dict]:
    """The timeline in 15-second blocks: events, a colour from green (several events) to red (description only)."""
    sentences = attention_map.get("sentences", [])
    total = float(attention_map.get("duration") or 0)
    found = []
    start = 0.0
    while start < total:
        lines = [item for item in sentences if start <= item["start"] < start + seconds]
        kinds = [tag["tag"].split(":")[0] for item in lines for tag in item["tags"]]
        events = sum(1 for kind in kinds if kind in attention.EVENT_TAGS)
        flat = lines and not events and all(set(tag["tag"].split(":")[0] for tag in item["tags"]) <= {"description", "reflection"} for item in lines)
        colour = "red" if flat else "orange" if events == 0 else "yellow" if events == 1 else "green"
        found.append({"start": start, "end": min(total, start + seconds), "events": events, "colour": colour,
                      "sentences": [item["sentence"] for item in lines]})
        start += seconds
    return found
