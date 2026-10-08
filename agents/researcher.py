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
    atomic_write_json,
    extract_json,
    validate_story_brief,
)

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
) -> dict:
    """Develop several premises and select one production-ready story brief.

    ``topic_override`` is authoritative: the returned brief always preserves it
    verbatim in ``topic`` and ``requested_premise`` even if the model paraphrases.
    """
    if narrative_type != "auto" and narrative_type not in NARRATIVE_TYPES:
        raise ValueError(f"Unsupported narrative type: {narrative_type}")
    exact = topic_override.strip()
    theme = focus_angle.strip() or random.choice(PSYCHOLOGICAL_THEMES)
    duration = float(target_duration_minutes or getattr(config, "TARGET_DURATION_MINUTES", 15))
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
    prompt = f"""You are the Story Development Agent for Private Gravity, an anthology of
cinematic psychological fiction. Ideas from psychology and philosophy must emerge through
character choice, consequence, image and subtext—never through a lecture or explainer.

Channel identity:
{channel_description}

{mode_instruction}
{type_instruction}
Primary theme: {theme}
Planned duration: {duration:g} minutes.
Previously used premises to avoid during automatic generation: {json.dumps(banned)}
Uniqueness nonce: {time.time_ns()}

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
production_feasibility, tags.

Use one of these narrative_type values: {json.dumps(sorted(NARRATIVE_TYPES))}.
The story must be original, causally coherent, emotionally plausible, filmable with cinematic
stock imagery, and must not use educational segments, factual listicles, or generic science hooks."""
    raw = generate(prompt)
    try:
        brief = extract_json(raw, "Story Development Agent")
        if exact:
            brief["topic"] = exact
            brief["requested_premise"] = exact
            brief["exact_premise_override"] = True
        if narrative_type != "auto":
            brief["narrative_type"] = narrative_type
        if not exact and len(brief.get("premise_candidates", [])) < 3:
            raise ValueError("automatic story development requires at least three candidates")
        brief = validate_story_brief(brief)
    except Exception as exc:
        repair_prompt = f"""Repair this Story Development Agent response into the complete JSON
contract below. Correct missing fields and score data while preserving the premise.
Return JSON only. Validation problem: {exc}
ORIGINAL CONTRACT:\n{prompt[:12000]}
RESPONSE: {raw[:14000]}"""
        brief = extract_json(generate(repair_prompt), "repaired Story Development Agent")
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
    if not exact and len(brief.get("premise_candidates", [])) < 3:
        raise ValueError("automatic story development requires at least three candidates")
    brief = validate_story_brief(brief)
    atomic_write_json(os.path.join(config.OUTPUT_DIR, "story_brief.json"), brief)
    return brief
