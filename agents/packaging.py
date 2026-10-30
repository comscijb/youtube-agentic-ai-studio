"""Packaging for clicks (Phase 3.6): thumbnail concepts, title scoring, description parts.

Episode 1 (w-XazyuEUv8) got 220 impressions and 0 clicks in its first 12 hours:
a grey thumbnail with the text over busy faces and the protagonist's head
cropped, and a title that states the premise instead of the story's strongest
moment. Every stage here is contract-first Gemini JSON, validated by code.

* ``thumbnail_concepts`` — 3 distinct concepts, each from a different
  composition template (``COMPOSITIONS``), with ≤ 4 words of text that pass the
  thumbnail-text policy and do not repeat the title.
* ``title_scoring`` — the 5 title candidates scored (low temperature) on
  curiosity, concreteness, emotion and clarity, plus a deterministic length score
  (≤ 60 characters, the hook within ~45); the policy validator still decides
  what may be published (``titles.json`` keeps the list for later swaps).
* ``description_parts`` — the description's hook, spoiler-free chapter titles,
  an episode-specific comment question and a pinned-comment suggestion
  (assembled and validated by ``video.description``).
"""
from __future__ import annotations

import re
from pathlib import Path

from agents import identity
from agents.narrative import (NarrativeValidationError, atomic_write_json, load_json,
                              validate_thumbnail_text)
from agents.structured import compact_json, generate_json

CONCEPTS_FILE = "thumbnail_concepts.json"
CONCEPT_COUNT = 3
TEXT_MAX_WORDS = 4
TEXT_MAX_CHARS = 22          # very large text (cap height ≥ 18 % of the frame) must still fit
WORD_MAX_CHARS = 10
SIDES = ("left", "right")

# Composition templates: the image prompt fragment each concept starts from.
COMPOSITIONS = {
    "big_face": (
        "Big face, strong emotion",
        "extreme close-up of {protagonist}: the whole head inside the frame with space above the hair, eyes "
        "in the upper third, a readable {emotion} expression, one warm light on the face, the rest of the "
        "frame falling into clean darkness on the {text_side} side"),
    "small_vs_presence": (
        "Small figure vs. overwhelming presence",
        "{protagonist} tiny in the lower part of the frame, dwarfed by {focal_element}; the scale is the "
        "story; deep clean shadow on the {text_side} side"),
    "telling_object": (
        "The telling object",
        "{focal_element} large in the foreground, sharply lit, the single brightest thing in the frame; "
        "{protagonist} small and out of focus behind it; clean dark space on the {text_side} side"),
    "two_person": (
        "Two-person tension",
        "{protagonist} and {other} in the same frame, faces turned away from each other, clear conflict, "
        "{emotion} on the protagonist's face, both heads fully inside the frame; clean dark space on the "
        "{text_side} side"),
}


def _content_words(text: str) -> set[str]:
    stop = {"the", "a", "an", "of", "to", "and", "in", "on", "at", "he", "she", "his", "her", "they", "them",
            "who", "is", "was", "be", "for", "with", "it", "him", "everyone", "i", "you", "we", "my", "me"}
    return {word for word in re.findall(r"[a-z']+", str(text).lower()) if word not in stop and len(word) > 2}


def text_repeats_title(text: str, title: str) -> bool:
    """The thumbnail text and the title must complement each other, not say the same thing."""
    words, title_words = _content_words(text), _content_words(title)
    if not words:
        return False
    shared = words & title_words
    return len(shared) >= 2 or (len(shared) == 1 and len(words) == 1) or \
        str(text).strip().lower().rstrip("?!.") in str(title).lower()


def thumbnail_text_problems(text: str, brief: dict, title: str = "") -> list[str]:
    """Policy (Phase 2.2) + Phase 3.6 limits: ≤ 4 short words that fit very large, not the title."""
    problems = [item["claim"] for item in validate_thumbnail_text(text, brief) if item["severity"] == "blocking"]
    words = str(text or "").split()
    if len(words) > TEXT_MAX_WORDS:
        problems.append(f"{len(words)} words (max {TEXT_MAX_WORDS})")
    if len(str(text or "")) > TEXT_MAX_CHARS:
        problems.append(f"{len(str(text))} characters (max {TEXT_MAX_CHARS}, the text must stay very large)")
    long_words = [word for word in words if len(word.strip("?!.,'\"")) > WORD_MAX_CHARS]
    if long_words:
        problems.append(f"word(s) too long to set very large: {', '.join(long_words)}")
    if title and text_repeats_title(text, title):
        problems.append("the text repeats the title")
    return problems


def validate_concepts(value: dict, brief: dict, title: str = "", characters: list[str] | None = None) -> dict:
    concepts = value.get("concepts") if isinstance(value, dict) else None
    if not isinstance(concepts, list) or len(concepts) != CONCEPT_COUNT:
        raise NarrativeValidationError(f"thumbnail_concepts requires exactly {CONCEPT_COUNT} concepts")
    seen, problems = set(), []
    for index, concept in enumerate(concepts):
        if not isinstance(concept, dict):
            raise NarrativeValidationError(f"concept {index} must be an object")
        for key in ("composition", "emotion", "focal_element", "text", "text_side", "curiosity"):
            if not str(concept.get(key) or "").strip():
                problems.append(f"concept {index}: missing {key}")
        composition = concept.get("composition")
        if composition not in COMPOSITIONS:
            problems.append(f"concept {index}: composition must be one of {', '.join(COMPOSITIONS)}")
        elif composition in seen:
            problems.append(f"concept {index}: composition {composition} is used twice")
        seen.add(composition)
        if concept.get("text_side") not in SIDES:
            problems.append(f"concept {index}: text_side must be left or right")
        if composition == "two_person" and concept.get("other_character") not in (characters or []):
            problems.append(f"concept {index}: two_person needs other_character, one of {characters}")
        for problem in thumbnail_text_problems(concept.get("text", ""), brief, title):
            problems.append(f"concept {index} text {concept.get('text')!r}: {problem}")
    if problems:
        raise NarrativeValidationError("thumbnail concepts: " + "; ".join(problems))
    return {"concepts": [{key: str(concept.get(key, "")).strip() for key in (
        "composition", "emotion", "focal_element", "text", "text_side", "curiosity", "other_character")}
        for concept in concepts]}


def concepts_contract(title: str, characters: list[str]) -> str:
    templates = "\n".join(f'- "{key}": {label}' for key, (label, _) in COMPOSITIONS.items())
    return f"""You design YouTube thumbnails for Private Gravity, cinematic psychological fiction drawn as
dark illustration. The thumbnail must stop the scroll at 170 px wide on a phone and create curiosity
WITHOUT explaining the thesis or the ending.

Return ONLY JSON {{"concepts": [exactly {CONCEPT_COUNT} objects]}}. Each object:
- "composition": one of the templates below, each used at most once:
{templates}
- "emotion": the protagonist's readable expression (fear, contempt, forced smile, realisation…)
- "focal_element": the one thing the eye lands on (a face, a shadow, a specific object from the story)
- "text": at most {TEXT_MAX_WORDS} words, at most {TEXT_MAX_CHARS} characters, a line of inner voice or a
  question; never the theme word, never a hashtag, and it must NOT repeat the title "{title}"
- "text_side": "left" or "right" — the clean dark side where the text goes (never over a face)
- "curiosity": one sentence on why it makes a viewer click without explaining the thesis
- "other_character": for "two_person" only, one of {characters}
Favour the story's most shareable moment (the reversal) over the premise."""


def generate_thumbnail_concepts(output_dir, *, brief: dict, script: dict, vb: dict, title: str = "",
                                generator=None, force: bool = False) -> dict:
    """3 validated concepts, cached in thumbnail_concepts.json (keyed by title and logline)."""
    from agents.gemini_client import generate
    path = Path(output_dir) / CONCEPTS_FILE
    title = title or script.get("title") or brief.get("video_title", "")
    key = {"title": title, "logline": brief.get("logline", ""), "version": 1}
    saved = load_json(path) or {}
    if saved.get("key") == key and not force:
        return saved
    from agents import visual_bible as vbible
    characters = [item["character_id"] for item in vbible.on_screen(vb)]      # only people who can be drawn
    story = {name: brief.get(name, "") for name in (
        "logline", "main_character_concept", "central_contradiction", "potential_turning_point",
        "philosophical_question", "thumbnail_concept", "psychological_theme")}
    story["characters"] = [{"id": item["character_id"], "descriptor": item.get("descriptor", "")}
                           for item in vbible.on_screen(vb)]
    value = generate_json(
        concepts_contract(title, characters[1:]), "thumbnail concepts",
        lambda value: validate_concepts(value, brief, title, characters[1:]),
        generator=generator or generate, context=f"STORY: {compact_json(story)}",
        stage="thumbnail_concepts", temperature_key="packaging", run_dir=str(output_dir))
    record = {"key": key, **value, "style": identity.visual_style()}
    atomic_write_json(path, record)
    return record


# ── Titles (Phase 3.6-B) ─────────────────────────────────────────────────────

TITLES_FILE = "titles.json"
TITLE_WEIGHTS = {"curiosity": 0.30, "concreteness": 0.25, "emotion": 0.20, "clarity": 0.15, "length": 0.10}
TITLE_HOOK_CHARS = 45
TITLE_TARGET_CHARS = 60
SCORE_KEYS = ("curiosity", "concreteness", "emotion", "clarity")


def length_score(title: str) -> int:
    """≤ 45 characters: the whole hook survives truncation; ≤ 60 still fits; longer loses the end."""
    size = len(str(title or ""))
    return 5 if size <= TITLE_HOOK_CHARS else 4 if size <= TITLE_TARGET_CHARS else 2 if size <= 70 else 1


def title_scoring_contract(titles: list[str]) -> str:
    listing = "\n".join(f"{index}. {title}" for index, title in enumerate(titles))
    return f"""You score YouTube titles for one Private Gravity episode (cinematic psychological fiction).
Score each title from 1 (weak) to 5 (strong) on:
- curiosity: does it open a gap the viewer needs to close, without explaining the thesis or the ending?
- concreteness: a specific person, an act, a number, an object — not an abstraction
- emotion: emotional charge a viewer feels at a glance
- clarity: understood instantly at feed speed (no puzzle, no jargon)
Titles built on the story's most shareable moment (the reversal) usually beat titles that restate the
premise. Return ONLY JSON {{"scores": [one object per title, in order: {{"index": 0, "curiosity": 1-5,
"concreteness": 1-5, "emotion": 1-5, "clarity": 1-5, "note": "one short sentence"}}]}}.

TITLES:
{listing}"""


def validate_title_scores(value: dict, count: int) -> dict:
    scores = value.get("scores") if isinstance(value, dict) else None
    if not isinstance(scores, list) or len(scores) != count:
        raise NarrativeValidationError(f"title scoring requires exactly {count} scores")
    clean = []
    for position, item in enumerate(scores):
        if not isinstance(item, dict):
            raise NarrativeValidationError(f"score {position} must be an object")
        row = {"index": int(item.get("index", position)), "note": str(item.get("note", "")).strip()[:200]}
        for key in SCORE_KEYS:
            try:
                number = int(round(float(item.get(key))))
            except (TypeError, ValueError):
                raise NarrativeValidationError(f"score {position}: {key} must be a number 1-5") from None
            if not 1 <= number <= 5:
                raise NarrativeValidationError(f"score {position}: {key} must be 1-5")
            row[key] = number
        clean.append(row)
    if sorted(row["index"] for row in clean) != list(range(count)):
        raise NarrativeValidationError("title scores must cover every index once")
    return {"scores": sorted(clean, key=lambda row: row["index"])}


def total_score(scores: dict) -> int:
    """0-100: the weighted 1-5 scores (length is deterministic)."""
    return round(sum(TITLE_WEIGHTS[key] * scores[key] for key in TITLE_WEIGHTS) / 5 * 100)


def score_titles(brief: dict, candidates: list, *, generator) -> list[dict]:
    """Every candidate (title-policy findings kept) with Gemini's 1-5 scores, the length score and a
    0-100 total, best first; candidates that break the policy always rank after the passing ones."""
    from agents.narrative import title_passes, validate_title
    rows = []
    for item in candidates:
        title = item["title"] if isinstance(item, dict) else str(item)
        if title and all(row["title"] != title for row in rows):
            findings = item["findings"] if isinstance(item, dict) and "findings" in item else validate_title(title, brief)
            rows.append({"title": title, "rationale": item.get("rationale", "") if isinstance(item, dict) else "",
                         "findings": findings, "passes": title_passes(findings)})
    if not rows:
        return []
    story = {key: brief.get(key, "") for key in ("logline", "potential_turning_point", "central_contradiction",
                                                 "main_character_concept")}
    value = generate_json(title_scoring_contract([row["title"] for row in rows]), "title scores",
                          lambda value: validate_title_scores(value, len(rows)), generator=generator,
                          context=f"STORY: {compact_json(story)}", stage="title_scoring",
                          temperature_key="title_scoring")
    for row, scores in zip(rows, value["scores"]):
        row["scores"] = {key: scores[key] for key in SCORE_KEYS}
        row["scores"]["length"] = length_score(row["title"])
        row["note"] = scores["note"]
        row["total"] = total_score(row["scores"])
    return sorted(rows, key=lambda row: (not row["passes"], -row["total"]))


def thumbnail_text_for(output_dir, script: dict | None = None, brief: dict | None = None) -> str:
    """The text on the thumbnail that will be uploaded: the selected variant's, else the brief's."""
    index = load_json(Path(output_dir) / "thumbnails" / "thumbnails.json") or {}
    selected = next((item for item in index.get("variants", []) if item.get("number") == index.get("selected")), None)
    if selected and (selected.get("concept") or {}).get("text"):
        return selected["concept"]["text"]
    return str((script or {}).get("thumbnail_text") or (brief or {}).get("thumbnail_text") or "")


def save_titles(output_dir, scored: list[dict] | None, selected: str, *, reason: str = "scored") -> dict:
    """titles.json: the scored list, the chosen title and every earlier choice (for later swaps)."""
    path = Path(output_dir) / TITLES_FILE
    record = load_json(path) or {"history": []}
    record.setdefault("history", [])
    if scored:
        record["candidates"] = scored
    if selected and selected != record.get("selected"):
        if record.get("selected"):
            record["history"].append({"title": record["selected"], "replaced_by": selected, "reason": reason})
        record["selected"] = selected
        record["selected_reason"] = reason
    atomic_write_json(path, record)
    return record


# ── Description parts (Phase 3.6-C) ─────────────────────────────────────────

DESCRIPTION_PARTS_FILE = "description_parts.json"
CHAPTER_TITLE_MAX = 40


def description_parts_contract(scene_count: int) -> str:
    return f"""You write the YouTube description parts for one Private Gravity episode (cinematic
psychological fiction). Never spoil the ending or the reversal; never explain the thesis.
Return ONLY JSON with:
- "hook": 1-2 short lines (max 220 characters) from the logline that make a viewer press play
- "chapter_titles": exactly {scene_count} short evocative scene titles (max {CHAPTER_TITLE_MAX} characters
  each, 2-5 words, no spoilers, no numbering), in scene order
- "comment_question": one open question from the story's philosophical question that invites viewers
  to share their own experience (ends with "?"), e.g. "Have you ever performed happiness for someone
  else's eyes?"
- "pinned_comment": the comment the creator pins: the same question plus a one-line invitation
  (max 300 characters)"""


def validate_description_parts(value: dict, scene_count: int) -> dict:
    problems = []
    hook = str(value.get("hook", "")).strip() if isinstance(value, dict) else ""
    titles = value.get("chapter_titles") if isinstance(value, dict) else None
    question = str((value or {}).get("comment_question", "")).strip()
    pinned = str((value or {}).get("pinned_comment", "")).strip()
    if not hook or len(hook) > 260 or hook.count("\n") > 1:
        problems.append("hook must be 1-2 lines, at most 220 characters")
    if not isinstance(titles, list) or len(titles) != scene_count:
        problems.append(f"chapter_titles must have exactly {scene_count} items")
    else:
        for index, title in enumerate(titles):
            if not str(title).strip() or len(str(title)) > CHAPTER_TITLE_MAX:
                problems.append(f"chapter title {index} must be 1-{CHAPTER_TITLE_MAX} characters")
    if not question.endswith("?"):
        problems.append("comment_question must be a question ending with '?'")
    if not pinned or len(pinned) > 320:
        problems.append("pinned_comment must be 1-300 characters")
    if problems:
        raise NarrativeValidationError("description parts: " + "; ".join(problems))
    return {"hook": hook, "chapter_titles": [str(title).strip() for title in titles],
            "comment_question": question, "pinned_comment": pinned}


def generate_description_parts(output_dir, *, brief: dict, script: dict, timeline: dict | None,
                               generator=None, force: bool = False) -> dict:
    """Hook, chapter titles, comment question and pinned comment; cached per story and timeline."""
    from agents.gemini_client import generate
    path = Path(output_dir) / DESCRIPTION_PARTS_FILE
    scenes = (timeline or {}).get("scenes") or []
    key = {"logline": brief.get("logline", ""), "scenes": [scene.get("title", "") for scene in scenes], "version": 1}
    saved = load_json(path) or {}
    if saved.get("key") == key and not force:
        return saved
    story = {name: brief.get(name, "") for name in ("logline", "philosophical_question", "central_dramatic_question",
                                                    "main_character_concept", "psychological_theme")}
    story["title"] = script.get("title", "")
    story["scenes"] = [{"index": index, "title": scene.get("title", ""), "seconds": round(float(scene.get("duration", 0)))}
                       for index, scene in enumerate(scenes)]
    value = generate_json(description_parts_contract(len(scenes)), "description parts",
                          lambda value: validate_description_parts(value, len(scenes)),
                          generator=generator or generate, context=f"STORY: {compact_json(story)}",
                          stage="description_parts", temperature_key="packaging", run_dir=str(output_dir))
    record = {"key": key, **value}
    atomic_write_json(path, record)
    return record
