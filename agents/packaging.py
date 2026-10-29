"""Packaging for clicks (Phase 3.6): thumbnail concepts, title scoring, description parts.

Episode 1 (w-XazyuEUv8) got 220 impressions and 0 clicks in its first 12 hours:
a grey thumbnail with the text over busy faces and the protagonist's head
cropped, and a title that states the premise instead of the story's strongest
moment. Every stage here is contract-first Gemini JSON, validated by code.

* ``thumbnail_concepts`` — 3 distinct concepts, each from a different
  composition template (``COMPOSITIONS``), with ≤ 4 words of text that pass the
  thumbnail-text policy and do not repeat the title.
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
    characters = [item["character_id"] for item in vb.get("characters", [])]
    story = {name: brief.get(name, "") for name in (
        "logline", "main_character_concept", "central_contradiction", "potential_turning_point",
        "philosophical_question", "thumbnail_concept", "psychological_theme")}
    story["characters"] = [{"id": item["character_id"], "descriptor": item.get("descriptor", "")}
                           for item in vb.get("characters", [])]
    value = generate_json(
        concepts_contract(title, characters[1:]), "thumbnail concepts",
        lambda value: validate_concepts(value, brief, title, characters[1:]),
        generator=generator or generate, context=f"STORY: {compact_json(story)}",
        stage="thumbnail_concepts", temperature_key="packaging", run_dir=str(output_dir))
    record = {"key": key, **value, "style": identity.visual_style()}
    atomic_write_json(path, record)
    return record
