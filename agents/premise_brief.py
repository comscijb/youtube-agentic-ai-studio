"""Detailed premise briefs (Phase 3.7-A).

Until now the exact premise was one sentence and Gemini invented everything
else. A **premise brief** lets the author steer the screenplay: free text (up to
``MAX_CHARS``), a Markdown file with optional headings, or JSON. Recognised
fields (all optional): working_title, narrative_type (A/B/C), logline,
protagonist, other_characters, setting, beats (hook, strategy_works,
hidden_cost, reversal, philosophical_layer, ending), tone, must_include,
must_avoid, target_minutes, title_ideas, thumbnail_idea. Anything else is kept
as ``notes``.

The brief is **authoritative**: the Story Development Agent preserves every
provided field and only fills what is missing (``brief_coverage`` records how
each field was used). The Character Bible, outline and scene prompts receive
only the relevant parts (``author_brief``), not the raw text. ``faithfulness``
checks the result deterministically: every must_include present, no must_avoid,
provided beats mapped to outline ``arc_movements``, provided names and ages
unchanged — violations are ``major`` findings the targeted revision may fix.

A single sentence is not a brief: it keeps working exactly as before (the exact
premise, ``topic_override``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.narrative import atomic_write_json, load_json, stable_hash

MAX_CHARS = 4000
MARKDOWN_FILE = "premise_brief.md"
JSON_FILE = "premise_brief.json"
NARRATIVE_IDS = {"a": "realistic_psychological_fiction", "b": "philosophical_thought_experiment",
                 "c": "dark_human_parable"}
BEAT_KEYS = ("hook", "strategy_works", "hidden_cost", "reversal", "philosophical_layer", "ending")
BEAT_MOVEMENTS = {"hook": ("hook",), "strategy_works": ("mechanism", "escalation_success"),
                  "hidden_cost": ("hidden_cost",), "reversal": ("reversal",),
                  "philosophical_layer": ("philosophical_layer",), "ending": ("ending",)}
LIST_FIELDS = ("other_characters", "must_include", "must_avoid", "title_ideas")
TEXT_FIELDS = ("working_title", "narrative_type", "logline", "protagonist", "setting", "tone", "thumbnail_idea")
FIELDS = TEXT_FIELDS + LIST_FIELDS + ("beats", "target_minutes")
ALIASES = {
    "title": "working_title", "working title": "working_title", "type": "narrative_type",
    "narrative type": "narrative_type", "premise": "logline", "logline": "logline", "protagonist": "protagonist",
    "main character": "protagonist", "other characters": "other_characters", "characters": "other_characters",
    "setting": "setting", "beats": "beats", "structure": "beats", "tone": "tone", "must include": "must_include",
    "must avoid": "must_avoid", "target minutes": "target_minutes", "duration": "target_minutes",
    "title ideas": "title_ideas", "thumbnail idea": "thumbnail_idea", "thumbnail": "thumbnail_idea",
}
BEAT_ALIASES = {"hook": "hook", "strategy works": "strategy_works", "strategy": "strategy_works",
                "hidden cost": "hidden_cost", "cost": "hidden_cost", "reversal": "reversal", "twist": "reversal",
                "philosophical layer": "philosophical_layer", "meaning": "philosophical_layer", "ending": "ending"}

TEMPLATE = """# Working title
The Only One Who Never Got Tired of Him

# Narrative type
A

# Logline
A lonely widower finds the perfect listener in an AI companion, until he notices it never needs anything from him.

# Protagonist
Walter Hale, 68, retired piano tuner, widowed two years; wound: he was never sure his wife enjoyed his company; desire: to be listened to without being a burden.

# Other characters
- June, his daughter, 41, calls every Sunday and always sounds in a hurry
- Ada, the AI companion (a voice on his phone), endlessly patient

# Setting
Walter's small terraced house in Leeds, present day, autumn into winter.

# Beats
- Hook: Walter thanks the AI for "the best conversation I've had in years".
- Strategy works: he stops calling June; Ada remembers everything he says.
- Hidden cost: his stories stop changing; nobody contradicts him any more.
- Reversal: he tells Ada a lie about his wife and Ada agrees with it.
- Philosophical layer: being listened to is not the same as being known.
- Ending: he calls June and lets her interrupt him.

# Tone
Quiet, tender, unsettling; no sci-fi spectacle.

# Must include
- the untuned piano in the front room
- Sunday phone calls

# Must avoid
- the AI becoming evil or sentient
- any lecture about technology

# Target minutes
12

# Title ideas
- The Only One Who Never Got Tired of Him

# Thumbnail idea
An old man laughing alone at his phone in a dark room.
"""


class BriefError(ValueError):
    """The brief cannot be used (too long, unreadable JSON, wrong field types)."""


def is_brief(text: str) -> bool:
    """Free text that is more than one sentence on one line is a brief; one sentence is a premise."""
    text = str(text or "").strip()
    if not text:
        return False
    if "\n" in text or re.search(r"^\s*#", text, re.M):
        return True
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return len(sentences) > 2 or len(text) > 300


def _norm(heading: str) -> str:
    return re.sub(r"[^a-z ]", "", heading.lower().replace("_", " ")).strip()


def _bullets(text: str) -> list[str]:
    items = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip() for line in text.splitlines()]
    items = [item for item in items if item]
    if len(items) == 1 and ";" in items[0]:
        items = [part.strip() for part in items[0].split(";") if part.strip()]
    return items


def _beats(text: str) -> dict:
    beats = {}
    for line in _bullets(text):
        key, sep, value = line.partition(":")
        beat = BEAT_ALIASES.get(_norm(key)) if sep else None
        if beat and value.strip():
            beats[beat] = value.strip()
    return beats


def normalise(fields: dict) -> dict:
    """Clean field values and types; unknown keys go to notes."""
    brief, notes = {}, []
    for key, value in (fields or {}).items():
        name = ALIASES.get(_norm(str(key)), str(key)) if str(key) not in FIELDS else str(key)
        if name not in FIELDS:
            if str(value).strip():
                notes.append(f"{key}: {value}" if key != "notes" else str(value))
            continue
        if name in LIST_FIELDS:
            items = value if isinstance(value, list) else _bullets(str(value))
            items = [str(item).strip() for item in items if str(item).strip()]
            if items:
                brief[name] = items
        elif name == "beats":
            beats = value if isinstance(value, dict) else _beats(str(value))
            beats = {BEAT_ALIASES.get(_norm(key), key): str(text).strip() for key, text in beats.items()
                     if str(text).strip()}
            beats = {key: text for key, text in beats.items() if key in BEAT_KEYS}
            if beats:
                brief["beats"] = beats
        elif name == "target_minutes":
            match = re.search(r"\d+(?:\.\d+)?", str(value))
            if match:
                brief["target_minutes"] = float(match.group())
        elif name == "narrative_type":
            text = str(value).strip()
            letter = re.match(r"(?:type\s*)?([abc])(?![a-z])", text.lower())
            brief["narrative_type"] = NARRATIVE_IDS[letter.group(1)] if letter else text
        elif str(value).strip():
            brief[name] = " ".join(str(value).split()) if name != "protagonist" else str(value).strip()
    if notes:
        brief["notes"] = "\n".join(notes)
    return brief


def parse_markdown(text: str) -> dict:
    """Markdown with optional headings (# / ## / ###) or "Field:" lines; free text goes to notes."""
    fields, current, buffer, preface = {}, None, [], []
    for line in str(text).splitlines():
        heading = re.match(r"^\s*#{1,4}\s*(.+?)\s*:?\s*$", line)
        if heading:
            if current is not None:
                fields[current] = "\n".join(buffer).strip()
            current, buffer = heading.group(1), []
        elif current is None:
            preface.append(line)
        else:
            buffer.append(line)
    if current is not None:
        fields[current] = "\n".join(buffer).strip()
    free = "\n".join(preface).strip()
    if free:
        fields["notes"] = free
    return normalise(fields)


def parse(text: str = "", *, path=None) -> dict:
    """A brief from text or a .md/.txt/.json file. Raises BriefError when unusable."""
    if path is not None:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise BriefError(f"{path.name} is not valid JSON: {exc}") from None
            if not isinstance(data, dict):
                raise BriefError(f"{path.name} must hold a JSON object")
            brief = normalise(data)
            brief["raw"] = json.dumps(data, ensure_ascii=False, indent=2)
            return _check(brief)
    text = str(text or "").strip()
    brief = parse_markdown(text)
    brief["raw"] = text
    return _check(brief)


def _check(brief: dict) -> dict:
    if len(brief.get("raw", "")) > MAX_CHARS + 2000:
        raise BriefError(f"the brief has {len(brief['raw'])} characters (keep it under ~{MAX_CHARS})")
    if not any(key in brief for key in FIELDS + ("notes",)):
        raise BriefError("the brief is empty")
    return brief


def brief_hash(brief: dict) -> str:
    return stable_hash({key: value for key, value in brief.items() if key != "raw"})


def save(output_dir, brief: dict) -> dict:
    """premise_brief.md (as written) + premise_brief.json (parsed, with its hash)."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / MARKDOWN_FILE).write_text(brief.get("raw", "").rstrip() + "\n", encoding="utf-8")
    record = {**{key: value for key, value in brief.items() if key != "raw"}, "hash": brief_hash(brief)}
    atomic_write_json(output / JSON_FILE, record)
    return record


def load(output_dir) -> dict | None:
    record = load_json(Path(output_dir) / JSON_FILE)
    return record if record and any(key in record for key in FIELDS + ("notes",)) else None


def exact_premise(brief: dict) -> str:
    """The one-line premise kept verbatim in the story brief (``topic``)."""
    for key in ("logline", "working_title"):
        if brief.get(key):
            return brief[key]
    notes = str(brief.get("notes", "")).strip()
    return re.split(r"(?<=[.!?])\s", notes, maxsplit=1)[0][:300] if notes else ""


def author_parts(brief: dict) -> dict:
    """What the bible, outline and scene prompts receive (``author_brief`` in the story brief)."""
    keys = ("protagonist", "other_characters", "setting", "beats", "tone", "must_include", "must_avoid", "notes")
    return {key: brief[key] for key in keys if brief.get(key)}


def contract_block(brief: dict) -> str:
    """The Story Development instruction: the brief is authoritative."""
    fields = {key: value for key, value in brief.items() if key not in ("raw", "hash")}
    return ("AUTHOR BRIEF (authoritative): the author has decided the following. Preserve every provided field "
            "and decision exactly — names, ages, relationships, setting, beats, tone, must_include and must_avoid. "
            "Only fill what is missing; you may improve wording but never replace a decision. If a provided "
            "logline exists, the logline field must keep its meaning. "
            "Record in brief_coverage how each provided field was used (preserved / expanded).\n"
            f"{json.dumps(fields, ensure_ascii=False)}")


def coverage(brief: dict, story: dict) -> dict:
    """field → how the generated story brief used it (deterministic)."""
    result = {}
    for key in FIELDS:
        if key not in brief:
            continue
        if key == "logline":
            result[key] = "preserved" if _similar(brief[key], story.get("logline", "")) else "rephrased"
        elif key == "narrative_type":
            result[key] = "preserved" if story.get("narrative_type") == brief[key] else "overridden"
        elif key == "working_title":
            titles = [str(item.get("title", item) if isinstance(item, dict) else item)
                      for item in story.get("title_candidates") or []] + [story.get("video_title", "")]
            result[key] = "kept as a title candidate" if brief[key] in titles else "inspiration"
        elif key == "target_minutes":
            result[key] = "preserved" if float(story.get("target_duration_minutes") or 0) == brief[key] else "overridden"
        else:
            result[key] = "passed to the bible, outline and scenes"
    return result


def _words(text: str) -> set[str]:
    stop = {"the", "a", "an", "of", "to", "and", "in", "on", "at", "his", "her", "he", "she", "it", "is", "any",
            "with", "for", "that", "this", "about", "from", "every", "their", "they", "who", "be", "by", "as"}
    return {word for word in re.findall(r"[a-z0-9']+", str(text).lower()) if word not in stop and len(word) > 2}


def _similar(first: str, second: str, ratio: float = 0.6) -> bool:
    words = _words(first)
    return bool(words) and len(words & _words(second)) / len(words) >= ratio


def mentioned(item: str, text: str, ratio: float = 0.7) -> bool:
    """A must_include / must_avoid item appears in ``text`` (most of its content words)."""
    words = _words(item)
    if not words:
        return False
    found = _words(text)
    stems = {word[:5] for word in found}
    hits = sum(1 for word in words if word in found or word[:5] in stems)
    return hits / len(words) >= ratio


def _people(brief: dict) -> list[dict]:
    """Names and ages the author gave: "Walter Hale, 68, …" / "June, his daughter, 41, …"."""
    people = []
    for text in [brief.get("protagonist", "")] + list(brief.get("other_characters") or []):
        name = re.match(r"\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", str(text))
        age = re.search(r"\b(\d{1,2})\b", str(text))
        if name:
            people.append({"name": name.group(1), "age": age.group(1) if age else None, "text": text})
    return people


def faithfulness(brief: dict, *, bible: dict | None, outline: dict | None, script: dict | None) -> list[dict]:
    """Deterministic findings (major, confirmed) for the editorial review."""
    findings = []

    def finding(claim, evidence, recommendation, scene_ids=()):
        findings.append({"severity": "major", "issue_type": "brief_faithfulness", "scene_ids": list(scene_ids),
                         "claim": claim, "evidence": list(evidence), "why_it_matters":
                         "The author's brief is authoritative.", "recommendation": recommendation,
                         "certainty": "confirmed"})
    sections = (script or {}).get("sections") or []
    story_text = " ".join(str(scene.get("narration", "")) for scene in sections)
    outline_text = " ".join(" ".join(str(scene.get(key, "")) for key in ("summary", "dramatic_action", "setting"))
                            for scene in (outline or {}).get("scenes") or [])
    for item in brief.get("must_include") or []:
        if not mentioned(item, story_text + " " + outline_text):
            finding(f"must_include item missing: {item!r}", [item], f"Add {item!r} to a fitting scene.")
    for item in brief.get("must_avoid") or []:
        scenes = [int(scene["id"]) for scene in sections if mentioned(item, scene.get("narration", ""), 0.8)]
        if scenes:
            finding(f"must_avoid item present: {item!r}", [item], f"Remove {item!r}.", scenes)
    movements = {movement for scene in (outline or {}).get("scenes") or [] for movement in scene.get("arc_movements") or []}
    if (outline or {}).get("scenes"):
        for beat in brief.get("beats") or {}:
            if not set(BEAT_MOVEMENTS.get(beat, ())) & movements:
                finding(f"beat {beat!r} has no outline scene (arc_movements)", [brief["beats"][beat]],
                        f"Map the {beat.replace('_', ' ')} beat to a scene.")
    characters = (bible or {}).get("characters") or []
    for person in _people(brief):
        match = next((item for item in characters if person["name"].split()[0].lower()
                      in str(item.get("name", "")).lower()), None)
        if match is None and characters:
            finding(f"character {person['name']!r} from the brief is not in the Character Bible", [person["text"]],
                    f"Keep {person['name']} as written in the brief.")
        elif match is not None and person["age"] and str(match.get("age", "")).strip() and \
                person["age"] not in str(match.get("age")):
            finding(f"{person['name']}'s age changed ({person['age']} → {match.get('age')})", [person["text"]],
                    f"Keep {person['name']} at {person['age']}.")
    return findings


def add_findings(review: dict, story_brief: dict, *, bible, outline, script) -> dict:
    """Merge the faithfulness findings into a deterministic review (before the editorial merge)."""
    author = story_brief.get("author_brief") or {}
    if not author:
        return review
    found = faithfulness(author, bible=bible, outline=outline, script=script)
    review["brief_faithfulness"] = found
    if found:
        review.setdefault("major_issues", []).extend(found)
        if review.get("status") not in ("structurally_invalid",):
            review["status"] = "revision_required"
    return review
