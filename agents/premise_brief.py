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
checks what is literal: no must_avoid, the beats' quoted lines by their kind
(``classify_quote``), provided names and ages unchanged, no text pasted from the
brief — violations are ``major`` findings the targeted revision may fix. Phase
3.9.2: everything that is not literal (must_include items, ending details, beat
coverage) only becomes a ``candidates`` entry on a lexical miss; the editorial
call confirms it (``resolve_candidates``) with a quote from the screenplay.

A single sentence is not a brief: it keeps working exactly as before (the exact
premise, ``topic_override``).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from agents.narrative import ABRUPT_ENDING, atomic_write_json, load_json, stable_hash
from agents.quotes import thought_lines

MAX_CHARS = 4000
MARKDOWN_FILE = "premise_brief.md"
JSON_FILE = "premise_brief.json"
NARRATIVE_IDS = {"a": "realistic_psychological_fiction", "b": "philosophical_thought_experiment",
                 "c": "dark_human_parable"}
BEAT_KEYS = ("cold_open", "hook", "strategy_works", "hidden_cost", "reversal", "philosophical_layer", "ending")
BEAT_MOVEMENTS = {"cold_open": ("cold_open",), "hook": ("hook",), "strategy_works": ("mechanism", "escalation_success"),
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
BEAT_ALIASES = {"cold open": "cold_open", "coldopen": "cold_open", "teaser": "cold_open", "omen": "cold_open",
                "hook": "hook", "strategy works": "strategy_works", "strategy": "strategy_works",
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
- Cold open: Walter, alone at the piano, hears Ada agree with something he knows is untrue, and stops playing. Don't say what it was.
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
    text = normalise_text(text)
    brief = parse_markdown(text)
    brief["raw"] = text
    return _check(brief)


def normalise_text(text) -> str:
    """LF line ends, no trailing spaces, Unicode NFC: a Windows textarea (CRLF) gives the same brief."""
    text = unicodedata.normalize("NFC", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _canonical(value):
    if isinstance(value, str):
        return normalise_text(value)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    return value


def _check(brief: dict) -> dict:
    if len(brief.get("raw", "")) > MAX_CHARS + 2000:
        raise BriefError(f"the brief has {len(brief['raw'])} characters (keep it under ~{MAX_CHARS})")
    if not any(key in brief for key in FIELDS + ("notes",)):
        raise BriefError("the brief is empty")
    return brief


def brief_hash(brief: dict) -> str:
    """Hash of the parsed fields (never the raw text or a stored ``hash``), on normalised text."""
    return stable_hash(_canonical({key: value for key, value in brief.items() if key not in ("raw", "hash")}))


def matches(story_brief: dict, author: dict) -> bool:
    """The story brief was made from this premise brief. Story briefs written by Phase 3.7 hashed the
    saved record including its own ``hash`` key; that value is still accepted."""
    requested = story_brief.get("requested_brief_hash")
    return requested in (brief_hash(author), stable_hash({key: value for key, value in author.items()
                                                          if key != "raw"}))


def save(output_dir, brief: dict) -> dict:
    """premise_brief.md (normalised, LF) + premise_brief.json (parsed, with its hash).
    Nothing is rewritten when the normalised brief is unchanged."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    text = normalise_text(brief.get("raw", "")) + "\n"
    current = load(output)
    markdown = output / MARKDOWN_FILE
    same_text = markdown.is_file() and normalise_text(markdown.read_text(encoding="utf-8")) + "\n" == text
    if current and brief_hash(current) == brief_hash(brief) and same_text:
        return current
    markdown.write_text(text, encoding="utf-8", newline="\n")
    record = {**{key: value for key, value in brief.items() if key != "raw"}, "hash": brief_hash(brief)}
    atomic_write_json(output / JSON_FILE, record)
    return record


def load(output_dir) -> dict | None:
    record = load_json(Path(output_dir) / JSON_FILE)
    return record if record and any(key in record for key in FIELDS + ("notes",)) else None


def target_minutes(brief: dict | None, requested=None) -> float | None:
    """The brief's target duration wins over the GUI / CLI value (episodes are 12-20 minutes)."""
    value = (brief or {}).get("target_minutes")
    if value and 12 <= float(value) <= 20:
        return float(value)
    return float(requested) if requested else None


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
    if isinstance(brief.get("beats"), dict) and brief["beats"].get("cold_open"):
        result["cold_open"] = "the outline's cold_open scene (authoritative)"
    return result


_QUOTE = re.compile(r'"([^"]+)"|“([^”]+)”')
_SUFFIXES = ("ing", "ed", "es", "s")


def spoken_lines(text: str) -> list[str]:
    """Direct speech in ``text``: the lines between double quotes (straight or curly)."""
    return [first or second for first, second in _QUOTE.findall(str(text or ""))]


_NARRATED = re.compile(r"\bnarrated line\b|\bnarrator\b|\bfinal line\b|turns the question to the viewer", re.I)
_THOUGHT = re.compile(r"\bthoughts?\b|\bthinks?\b|\bthinking\b|\bheard\b|\bhears?\b|\bescapes him\b|\binner\b", re.I)
_ATTRIBUTION = re.compile(r"\b(?:says?|said|asks?|asked|answers?|answered|tells?|told|repl(?:y|ies|ied)|speaks? aloud|"
                          r"whispers?|whispered|thanks|thanked|mutters?|shouts?|murmurs?|admits?)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?…][\"”]?\s")
QUOTE_KINDS = ("dialogue", "thought", "narrated", "term")


def classify_quote(beat_text: str, span: str) -> str:
    """dialogue / thought / narrated / term, from the words around a quoted span in a beat.
    Unknown is ``term``, the lenient kind."""
    text = str(beat_text or "")
    position = text.find(span)
    if position < 0:
        return "term"
    before = text[:position].rstrip(' "“')
    cut = max((match.end() for match in _SENTENCE_END.finditer(before + " ")), default=0)
    previous_quote = max(before.rfind('"'), before.rfind("”"))
    before = before[max(cut, previous_quote + 1):]
    after = text[position + len(span):].lstrip(' "”')
    tail = str(span).rstrip()
    closes = tail.endswith((".", "!", "?", "…")) or text[position + len(span):position + len(span) + 2].strip(' "”')[:1] in (".", "!", "?")
    after = "" if closes else re.split(r"[.!?…]", after, maxsplit=1)[0]
    context = before + " " + after
    if _NARRATED.search(context):
        return "narrated"

    def last(pattern, text):
        return max((match.end() for match in pattern.finditer(text)), default=-1)
    thought, attribution = last(_THOUGHT, before), last(_ATTRIBUTION, before)   # the nearest verb decides
    if thought >= 0 or attribution >= 0:
        return "thought" if thought > attribution else "dialogue"
    return "thought" if _THOUGHT.search(after) else "dialogue" if _ATTRIBUTION.search(after) else "term"


def beat_quotes(brief: dict) -> list[tuple[str, str]]:
    """(beat, line) for every span the brief's beats quote."""
    return [(beat, line.strip()) for beat, text in (brief.get("beats") or {}).items()
            for line in spoken_lines(text) if _words(line)]


def required_lines(brief: dict) -> list[dict]:
    """The quoted lines the author requires, with their kind."""
    return [{"beat": beat, "line": line, "kind": classify_quote(brief["beats"][beat], line)}
            for beat, line in beat_quotes(brief)]


def ending_items(brief: dict) -> list[str]:
    """The ending beat's details, one per sentence (quoted lines are checked by their kind)."""
    text = _QUOTE.sub(". ", str((brief.get("beats") or {}).get("ending", "")))
    items = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = re.sub(r"^\s*(?:open question|question)\s*:\s*", "", sentence.strip(), flags=re.I)
        sentence = sentence.strip(" :;,.")
        if len(_words(sentence)) >= 2:
            items.append(sentence)
    return items


_STAGE_DIRECTION = re.compile(r"\bcut\b|\bcuts\b|\ba long silence\b|\bopens? (?:his|her|their) mouth\b|\bthe screen\b|"
                              r"\bfade\b|\bto black\b|\bbefore (?:he|she|they) (?:answers?|speaks?|replies?)\b", re.I)
_STYLE_ITEM = re.compile(r"\bas quoted\b|\bquieter than\b|\bin silence\b|\bnever shown\b|\bwritten as\b|"
                         r"\bspoken as\b|\bvoiced\b|\bin (?:single|double) quotes\b|\bin voice-?over\b|\boff-?screen\b", re.I)
_THOUGHT_RULE = re.compile(r"\bthoughts?\b|\btelepath|\bhears? (?:your|their|his|her|each other's)\b|\bvoices?\b", re.I)


def is_stage_direction(item: str) -> bool:
    """Intent, not narration: satisfied when the scene ends at that point, never written on the page."""
    return bool(_STAGE_DIRECTION.search(str(item)))


def is_style_constraint(item: str) -> bool:
    """A must_include item that says how something is written or voiced, not what happens."""
    return bool(_STYLE_ITEM.search(str(item)))


def style_constraints(brief: dict) -> list[str]:
    return [item for item in brief.get("must_include") or [] if is_style_constraint(item)]


def heard_thoughts_on(brief: dict | None, narrative_type: str | None = None) -> bool:
    """The heard-thought convention (thoughts in single quotes, delivered quieter): a must_include item
    asks for it, or a Type B brief whose rule involves thoughts, voices or telepathy. Off for Type A."""
    if not brief:
        return False
    if any("thought" in item.lower() for item in style_constraints(brief)):
        return True
    return narrative_type == NARRATIVE_IDS["b"] and bool(_THOUGHT_RULE.search(str(brief.get("setting", ""))))


def abrupt_ending(brief: dict | None) -> bool:
    """The author wants the story to stop short ("cut before he answers", "one final line")."""
    return bool(ABRUPT_ENDING.search(str(((brief or {}).get("beats") or {}).get("ending", ""))))


def _stem(word: str) -> str:
    word = word.removesuffix("'s")
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)]
            break
    return word[:5]


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
    stems = {_stem(word) for word in found}
    hits = sum(1 for word in words if word in found or _stem(word) in stems)
    return hits / len(words) >= ratio


def said(line: str, text: str, ratio: float = 0.8) -> bool:
    """``line`` appears as direct speech (inside quotes) in ``text``."""
    return any(mentioned(line, spoken, ratio) for spoken in spoken_lines(text))


_COLLECTIVE = re.compile(r"\b(?:townsfolk|townspeople|villagers|customers|crowd|extras|neighbours|neighbors|passers-?by|"
                         r"never named)\b", re.I)


def _people(brief: dict) -> list[dict]:
    """Names and ages the author gave: "Walter Hale, 68, …" / "June, his daughter, 41, …". A character
    bullet starts with one or two capitalised words followed by a comma or an age; collective or
    background entries ("Townsfolk: never named") are not characters."""
    people = []
    for text in [brief.get("protagonist", "")] + list(brief.get("other_characters") or []):
        name = re.match(r"\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:,|\(?\d)", str(text))
        age = re.search(r"\b(\d{1,2})\b", str(text))
        if name and not _COLLECTIVE.match(name.group(1)) and not _COLLECTIVE.search(str(text)[:60]):
            people.append({"name": name.group(1), "age": age.group(1) if age else None, "text": text})
    return people


def _quote_present(kind: str, line: str, story: str) -> bool:
    if kind == "dialogue":
        return said(line, story)
    if kind == "thought":
        return said(line, story) or any(mentioned(line, spoken, 0.8) for spoken in thought_lines(story))
    if kind == "narrated":
        return mentioned(line, story, 0.8)
    return mentioned(line, story)


_QUOTE_CLAIMS = {"dialogue": "the brief's line is not spoken as direct dialogue: \"{}\"",
                 "thought": "the brief's heard thought is not on the page in quotes: \"{}\"",
                 "narrated": "the brief's narrated line is missing: \"{}\"",
                 "term": "the brief's term does not appear: \"{}\""}
_QUOTE_FIXES = {"dialogue": "Have the character say it aloud, in double quotes, in the {} scene.",
                "thought": "Let the character think it, in single quotes, in the {} scene.",
                "narrated": "Let the narrator say it, word for word, in the {} scene.",
                "term": "Use the term in the {} scene."}


def _beat_scenes(beat: str, outline: dict | None, sections: list[dict]) -> list[int]:
    """Screenplay scene ids that deliver ``beat`` (via the outline's arc_movements)."""
    planned = {scene.get("scene_id") for scene in (outline or {}).get("scenes") or []
               if set(BEAT_MOVEMENTS.get(beat, ())) & set(scene.get("arc_movements") or [])}
    return [int(scene["id"]) for scene in sections if scene.get("outline_scene_id") in planned]


def faithfulness(brief: dict, *, bible: dict | None, outline: dict | None, script: dict | None) -> list[dict]:
    """Deterministic findings (major, confirmed) for what is literal: must_avoid, quoted lines, names."""
    findings = []

    def finding(claim, evidence, recommendation, scene_ids=()):
        findings.append({"severity": "major", "issue_type": "brief_faithfulness", "scene_ids": list(scene_ids),
                         "claim": claim, "evidence": list(evidence), "why_it_matters":
                         "The author's brief is authoritative.", "recommendation": recommendation,
                         "certainty": "confirmed"})
    sections = (script or {}).get("sections") or []
    story_text = " ".join(str(scene.get("narration", "")) for scene in sections)
    for item in brief.get("must_avoid") or []:
        scenes = [int(scene["id"]) for scene in sections if mentioned(item, scene.get("narration", ""), 0.8)]
        if scenes:
            finding(f"must_avoid item present: {item!r}", [item], f"Remove {item!r}.", scenes)
    if sections:
        for item in required_lines(brief):
            if not _quote_present(item["kind"], item["line"], story_text):
                beat = item["beat"].replace("_", " ")
                finding(_QUOTE_CLAIMS[item["kind"]].format(item["line"]), [brief["beats"][item["beat"]]],
                        _QUOTE_FIXES[item["kind"]].format(beat), _beat_scenes(item["beat"], outline, sections))
        if any("thought" in item.lower() for item in style_constraints(brief)) and not thought_lines(story_text):
            finding("the brief asks for heard thoughts in quotes, but no thought is in single quotes",
                    style_constraints(brief), "Write each heard thought in single quotes, 'like this'.")
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


def candidates(brief: dict, *, outline: dict | None, script: dict | None) -> list[dict]:
    """Lexical misses: not findings yet. The editorial call confirms each one (``resolve_candidates``)."""
    found = []

    def candidate(kind, item, claim, recommendation, scene_ids=(), question=""):
        found.append({"kind": kind, "item": item, "claim": claim, "recommendation": recommendation,
                      "scene_ids": list(scene_ids), "question": question or f"Is this in the story: {item}"})
    sections = (script or {}).get("sections") or []
    story_text = " ".join(str(scene.get("narration", "")) for scene in sections)
    outline_text = " ".join(" ".join(str(scene.get(key, "")) for key in ("summary", "dramatic_action", "setting"))
                            for scene in (outline or {}).get("scenes") or [])
    for item in brief.get("must_include") or []:
        if not is_style_constraint(item) and not mentioned(item, story_text + " " + outline_text):
            candidate("must_include", item, f"must_include item missing: {item!r}", f"Add {item!r} to a fitting scene.")
    if sections:
        ending = _beat_scenes("ending", outline, sections) or [int(scene["id"]) for scene in sections[-2:]]
        ending_text = " ".join(str(scene.get("narration", "")) for scene in sections if int(scene["id"]) in ending)
        for item in ending_items(brief):
            if is_stage_direction(item):
                candidate("stage_direction", item, f"the ending does not stop where the brief says: {item!r}",
                          "End the final scene at that point; never write the direction into the narration.", ending,
                          f"Does the final scene end at this point (it is an instruction, not text to find): {item}")
            elif not mentioned(item, ending_text, 0.6):
                candidate("ending", item, f"ending detail missing: {item!r}",
                          "Dramatise it in the final scene(s).", ending)
    if (outline or {}).get("scenes"):
        movements = {movement for scene in outline["scenes"] for movement in scene.get("arc_movements") or []}
        for beat, text in (brief.get("beats") or {}).items():
            if not set(BEAT_MOVEMENTS.get(beat, ())) & movements:
                candidate("beat", text, f"beat {beat!r} has no outline scene (arc_movements)",
                          f"Map the {beat.replace('_', ' ')} beat to a scene.")
    return found


def _normal(text: str) -> str:
    text = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).lower()


def _unverified(item: dict) -> dict:
    return {"severity": "minor", "issue_type": "brief_faithfulness", "scene_ids": item["scene_ids"],
            "claim": item["claim"] + " (unverified: lexical miss)", "evidence": [item["item"]],
            "recommendation": item["recommendation"], "candidate": item["item"]}


def resolve_candidates(review: dict, answers, script: dict) -> dict:
    """Apply the editorial call's ``brief_check``: ``present: false`` makes a major finding; ``present:
    true`` clears it only when ``evidence_quote`` exists verbatim in the screenplay; anything else
    stays an unverified minor."""
    pending = review.get("faithfulness_candidates") or []
    if not pending:
        return review
    story = _normal(" ".join(str(scene.get("narration", "")) for scene in (script or {}).get("sections") or []))
    by_item = {_normal(item.get("item")): item for item in answers or [] if isinstance(item, dict)}
    verified, kept = [], []
    review["minor_issues"] = [item for item in review.get("minor_issues", []) if "candidate" not in item]
    for item in pending:
        answer = by_item.get(_normal(item["item"]))
        if answer is not None and answer.get("present") is False:
            major = {"severity": "major", "issue_type": "brief_faithfulness", "scene_ids": item["scene_ids"],
                     "claim": item["claim"], "evidence": [str(answer.get("evidence_quote") or item["item"])],
                     "why_it_matters": "The author's brief is authoritative.",
                     "recommendation": item["recommendation"], "certainty": "confirmed"}
            review.setdefault("major_issues", []).append(major)
            review.setdefault("brief_faithfulness", []).append(major)
            if review.get("status") != "structurally_invalid":
                review["status"] = "revision_required"
            continue
        quote = _normal((answer or {}).get("evidence_quote"))
        if answer is not None and answer.get("present") is True and quote and quote in story:
            verified.append({"item": item["item"], "scene_id": answer.get("scene_id"),
                             "evidence_quote": answer.get("evidence_quote")})
        else:
            kept.append(item)
            review["minor_issues"].append(_unverified(item))
    review["faithfulness_candidates"] = kept
    review["faithfulness_verified"] = list(review.get("faithfulness_verified") or []) + verified
    return review


PASTE_MIN_WORDS = 9
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(str(text).lower().replace("’", "'"))


def paste_sources(brief: dict) -> list[str]:
    """The brief's prose (beats, logline, setting, tone) without its quoted lines, which are authorised."""
    texts = list((brief.get("beats") or {}).values()) + [brief.get(key, "") for key in ("logline", "setting", "tone")]
    return [part for text in texts for part in _QUOTE.sub("\0", str(text or "")).split("\0") if part.strip()]


def paste_spans(text: str, brief: dict, minimum: int = PASTE_MIN_WORDS) -> list[str]:
    """Runs of ``minimum`` or more words of ``text`` copied from the brief's prose."""
    grams = set()
    for source in paste_sources(brief):
        words = _tokens(source)
        grams.update(tuple(words[i:i + minimum]) for i in range(len(words) - minimum + 1))
    words = _tokens(text)
    spans, index = [], 0
    while index <= len(words) - minimum:
        if tuple(words[index:index + minimum]) in grams:
            end = index + minimum
            while end < len(words) and tuple(words[end - minimum + 1:end + 1]) in grams:
                end += 1
            spans.append(" ".join(words[index:end]))
            index = end
        else:
            index += 1
    return spans


def paste_findings(brief: dict, script: dict | None) -> list[dict]:
    """``brief_paste`` (major): a scene copied sentences of the brief instead of dramatising them."""
    findings = []
    for scene in (script or {}).get("sections") or []:
        for span in paste_spans(scene.get("narration", ""), brief):
            findings.append({"severity": "major", "issue_type": "brief_paste", "scene_ids": [int(scene["id"])],
                             "claim": f"Scene {scene['id']} copies the brief instead of dramatising it.",
                             "evidence": [span], "why_it_matters": "The brief's sentences are instructions, not prose.",
                             "recommendation": "Dramatise what must happen in your own words.", "certainty": "confirmed"})
    return findings


def intent_block(brief: dict) -> str:
    """The brief for a revision: what must happen, as intent; authorised lines passed separately."""
    beats = "".join(f"\n<intent beat=\"{beat}\">{_QUOTE.sub('(…)', str(text))}</intent>"
                    for beat, text in (brief.get("beats") or {}).items())
    lines = "".join(f"\n<required_line kind=\"{item['kind']}\">{item['line']}</required_line>"
                    for item in required_lines(brief))
    return ("Each <intent> says what must happen. It is an instruction, not text: dramatise it, never copy its "
            "sentences. Only a <required_line> may appear word for word." + beats + lines)


def without_beats(story_brief: dict) -> dict:
    """The story brief for a revision or expansion prompt: the author's beats go in ``intent_block``."""
    author = story_brief.get("author_brief")
    if not author:
        return story_brief
    return {**story_brief, "author_brief": {key: value for key, value in author.items() if key != "beats"}}


def add_findings(review: dict, story_brief: dict, *, bible, outline, script) -> dict:
    """Merge the faithfulness findings into a deterministic review (before the editorial merge)."""
    author = story_brief.get("author_brief") or {}
    if not author:
        return review
    found = faithfulness(author, bible=bible, outline=outline, script=script)
    pasted = paste_findings({**author, "logline": story_brief.get("logline", "")}, script)
    pending = candidates(author, outline=outline, script=script)
    review["brief_faithfulness"] = found
    review["brief_paste"] = pasted
    review["faithfulness_candidates"] = pending
    review["style_constraints"] = style_constraints(author)
    review.setdefault("minor_issues", []).extend(_unverified(item) for item in pending)
    if found or pasted:
        review.setdefault("major_issues", []).extend(found + pasted)
        if review.get("status") not in ("structurally_invalid",):
            review["status"] = "revision_required"
    return review
