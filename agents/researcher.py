"""Story Development Agent for Private Gravity."""
from __future__ import annotations

import json
import os
import random
import time

import config
from agents.gemini_client import generate
from agents.narrative import (
    NARRATIVE_TYPES,
    PSYCHOLOGICAL_THEMES,
    NarrativeValidationError,
    atomic_write_json,
    load_json,
    select_title,
    title_passes,
    stable_hash,
    validate_story_brief,
)
from agents.identity import TITLE_POLICY, identity_block
from agents.structured import compact_json, generate_json

_BANNED_TOPICS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "banned_topics.txt"
)
_BUILTIN_BANNED: list[str] = []

# Kept as a public compatibility alias for the existing GUI.
FOCUS_ANGLES = list(PSYCHOLOGICAL_THEMES)


def load_banned_topics() -> list[str]:
    topics = list(_BUILTIN_BANNED)
    try:
        if os.path.exists(_BANNED_TOPICS_FILE):
            with open(_BANNED_TOPICS_FILE, encoding="utf-8") as handle:
                for line in handle:
                    topic = line.strip()
                    if topic and not topic.startswith("#") and topic not in topics:
                        topics.append(topic)
    except OSError as exc:
        print(f"   WARNING: Could not read banned_topics.txt: {exc}")
    return topics


def save_banned_topics(topics: list[str]) -> None:
    with open(_BANNED_TOPICS_FILE, "w", encoding="utf-8") as handle:
        handle.write("# One previously used story premise per line.\n")
        handle.write("# These are avoided only during automatic premise generation.\n\n")
        for topic in topics:
            if str(topic).strip():
                handle.write(str(topic).strip() + "\n")


def research_topic(
    channel_description: str,
    topic_override: str = "",
    focus_angle: str = "",
    narrative_type: str = "auto",
    target_duration_minutes: float | None = None,
    reuse_cached: bool = False,
) -> dict:
    """Develop several premises and select one production-ready story brief.

    ``topic_override`` is authoritative: the returned brief always preserves it
    verbatim in ``topic`` and ``requested_premise`` even if the model paraphrases.
    With ``reuse_cached`` a valid ``story_brief.json`` produced by the same
    request in ``config.OUTPUT_DIR`` is returned without an API call.
    """
    if narrative_type != "auto" and narrative_type not in NARRATIVE_TYPES:
        raise ValueError(f"Unsupported narrative type: {narrative_type}")
    exact = topic_override.strip()
    duration = float(target_duration_minutes or getattr(config, "TARGET_DURATION_MINUTES", 15))
    request_hash = stable_hash({
        "premise": exact, "theme": focus_angle.strip(),
        "narrative_type": narrative_type, "duration": duration,
    })
    brief_path = os.path.join(config.OUTPUT_DIR, "story_brief.json")
    if reuse_cached:
        cached = _cached_brief(brief_path, request_hash)
        if cached:
            print("   -> Reusing cached story brief")
            return cached

    theme = focus_angle.strip() or random.choice(PSYCHOLOGICAL_THEMES)
    banned = [] if exact else load_banned_topics()
    mode_instruction = (
        f'The user premise is authoritative: "{exact}". Develop this exact premise; '
        "do not replace it with a related idea."
        if exact else
        "Invent four substantially different premises, score them, and select the strongest."
    )
    type_instruction = (
        "Choose the narrative type that best serves the story."
        if narrative_type == "auto" else f'Use exactly this narrative type: "{narrative_type}".'
    )
    print(f"   -> Developing {'exact premise' if exact else 'original premises'}: {exact or theme}")
    contract = f"""You are the Story Development Agent for Private Gravity, an anthology of
cinematic psychological fiction. Ideas from psychology and philosophy must emerge through
character choice, consequence, image and subtext—never through a lecture or explainer.
The channel description, theme, duration and previously used premises are supplied after this contract.

{identity_block(None if narrative_type == "auto" else narrative_type)}

{TITLE_POLICY}

{mode_instruction}
{type_instruction}

Create four premise_candidates unless an exact premise was supplied (then create at least one
candidate faithful to it). Each candidate needs premise, narrative_type, originality_score,
emotional_power_score, dramatic_potential_score, visual_feasibility_score, audience_access_score,
weighted_total, strengths, and risks. Weights: dramatic 30%, emotional 25%, originality 20%,
audience access 15%, visual feasibility 10%. Select the best candidate without claiming that
scores predict audience performance.

Return ONLY one complete JSON object with these keys:
premise_candidates, selected_candidate_index, video_title, narrative_type,
psychological_theme, logline, main_character_concept, central_dramatic_question,
protagonist_desire, underlying_emotional_need, principal_conflict, central_contradiction,
stakes, potential_turning_point, intended_emotional_experience, philosophical_question,
ending_direction, intended_audience, thumbnail_concept, originality_assessment,
production_feasibility, tags, title_candidates, thumbnail_text_candidates.
title_candidates: exactly 5 objects {{"title": "...", "rationale": "one sentence on the curiosity it
creates"}} that follow the title policy; video_title must be the strongest of them.
thumbnail_text_candidates: exactly 3 strings that follow the thumbnail text policy.

Use one of these narrative_type values: {json.dumps(sorted(NARRATIVE_TYPES))}.
The story must be original, causally coherent, emotionally plausible, filmable with cinematic
stock imagery, and must not use educational segments, factual listicles, or generic science hooks."""
    context = f"""Channel identity:
{channel_description}

Primary theme: {theme}
Planned duration: {duration:g} minutes.
Previously used premises to avoid during automatic generation: {json.dumps(banned)}
Uniqueness nonce: {time.time_ns()}"""

    def finalize(value: dict) -> dict:
        brief = dict(value)
        if exact:
            brief["topic"] = exact
            brief["requested_premise"] = exact
            brief["exact_premise_override"] = True
        else:
            brief.setdefault("topic", brief.get("logline", ""))
            brief["exact_premise_override"] = False
        if narrative_type != "auto":
            brief["narrative_type"] = narrative_type
            for candidate in brief.get("premise_candidates", []):
                if isinstance(candidate, dict):
                    candidate["narrative_type"] = narrative_type
        brief["target_duration_minutes"] = duration
        brief["request_hash"] = request_hash
        if not exact and len(brief.get("premise_candidates", [])) < 3:
            raise NarrativeValidationError(
                "automatic story development requires at least three premise_candidates"
            )
        return validate_story_brief(brief)

    brief = generate_json(
        contract, "Story Development Agent", finalize, generator=generate,
        context=context, stage="premise", temperature_key="premise",
        run_dir=config.OUTPUT_DIR,
    )
    _apply_title_review(brief, _review_titles(brief))
    atomic_write_json(brief_path, brief)
    return brief


def _review_titles(brief: dict) -> dict:
    """Pick the first policy-compliant title; otherwise make one bounded repair call."""
    review = select_title(brief)
    if review["status"] == "passed":
        return review
    failures = [
        {"title": item["title"], "problems": [finding["claim"] for finding in item["findings"]]}
        for item in review["title_candidates"] if not item["passes"]
    ] + [
        {"thumbnail_text": item["text"],
         "problems": [finding["claim"] for finding in item["findings"]]}
        for item in review["thumbnail_text_candidates"] if not item["passes"]
    ]
    print("   -> No title/thumbnail candidate passed the title policy; requesting new candidates")
    contract = f"""You write packaging for one Private Gravity episode. Return ONLY JSON
{{"title_candidates": [5 objects {{"title": "...", "rationale": "..."}}],
"thumbnail_text_candidates": [3 strings]}}.

{TITLE_POLICY}

The previous candidates failed for these reasons; do not repeat them:
{compact_json(failures)}"""
    story = {key: brief.get(key, "") for key in (
        "topic", "logline", "main_character_concept", "protagonist_desire",
        "central_contradiction", "psychological_theme", "potential_turning_point",
    )}

    def require_candidates(value: dict) -> dict:
        titles = value.get("title_candidates")
        thumbnails = value.get("thumbnail_text_candidates")
        if not isinstance(titles, list) or not titles or not isinstance(thumbnails, list)                 or not thumbnails:
            raise NarrativeValidationError(
                "title repair requires nonempty title_candidates and thumbnail_text_candidates arrays"
            )
        return value

    try:
        repaired = generate_json(
            contract, "title candidates", require_candidates, generator=generate,
            context=f"STORY: {compact_json(story)}", stage="title_repair",
            temperature_key="premise", run_dir=config.OUTPUT_DIR,
        )
    except NarrativeValidationError as exc:
        review["repair_error"] = str(exc)
        review["repair_attempted"] = True
        return review
    retried = select_title({
        "psychological_theme": brief.get("psychological_theme", ""),
        "title_candidates": repaired["title_candidates"],
        "thumbnail_text_candidates": repaired["thumbnail_text_candidates"],
    })
    if retried["status"] != "passed":
        # Keep the best of both rounds: a passing title or thumbnail from either.
        for key, selected, candidates in (
                ("title", "selected_title", "title_candidates"),
                ("text", "selected_thumbnail_text", "thumbnail_text_candidates")):
            if not any(item["passes"] for item in retried[candidates]):
                passing = next((item for item in review[candidates] if item["passes"]), None)
                if passing:
                    retried[selected] = passing[key]
                    if key == "title":
                        retried["selected_title_findings"] = passing["findings"]
        if (title_passes(retried["selected_title_findings"]) and any(
                item["passes"] and item["text"] == retried["selected_thumbnail_text"]
                for item in retried["thumbnail_text_candidates"] + review["thumbnail_text_candidates"])):
            retried["status"] = "passed"
    retried["repair_attempted"] = True
    retried["initial_candidates"] = {
        "title_candidates": review["title_candidates"],
        "thumbnail_text_candidates": review["thumbnail_text_candidates"],
    }
    return retried


def _apply_title_review(brief: dict, review: dict) -> None:
    if review.get("selected_title"):
        brief["video_title"] = review["selected_title"]
    brief["thumbnail_text"] = review.get("selected_thumbnail_text", "")
    brief["title_review"] = review


def _cached_brief(path: str, request_hash: str) -> dict | None:
    value = load_json(path)
    if not value or value.get("request_hash") != request_hash:
        return None
    try:
        return validate_story_brief(value)
    except (NarrativeValidationError, KeyError, TypeError):
        return None
