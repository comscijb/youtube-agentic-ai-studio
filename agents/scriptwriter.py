"""Private Gravity character, outline and scene-writing pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

import config
from agents.gemini_client import generate
from agents.narrative import (
    CONTENT_MODE,
    SCHEMA_VERSION,
    NarrativeValidationError,
    atomic_write_json,
    deterministic_review,
    extract_json,
    load_json,
    stable_hash,
    validate_character_bible,
    validate_outline,
    validate_script,
    validate_story_brief,
)


def _generate_json(prompt: str, label: str, validator=None) -> dict:
    """Generate structured data with one bounded semantic repair attempt."""
    raw = generate(prompt)
    try:
        value = extract_json(raw, label)
        return validator(value) if validator else value
    except (NarrativeValidationError, ValueError, KeyError, TypeError) as exc:
        repair = f"""Repair the following incomplete or malformed response into ONE valid,
complete JSON object. Preserve its story decisions. Do not add commentary or markdown.
Required artifact: {label}. Validation problem: {exc}

ORIGINAL REQUEST:
{prompt[:14000]}

RESPONSE TO REPAIR:
{raw[:10000]}"""
        value = extract_json(generate(repair), f"repaired {label}")
        return validator(value) if validator else value


def _cached(path: Path, source_hash: str, validator, *args):
    value = load_json(path)
    if not value or value.get("source_hash") != source_hash:
        return None
    try:
        return validator(value, *args)
    except (NarrativeValidationError, KeyError, TypeError):
        return None


def _character_bible(brief: dict, output: Path, force: bool) -> dict:
    source_hash = stable_hash(brief)
    path = output / "character_bible.json"
    if not force:
        cached = _cached(path, source_hash, validate_character_bible)
        if cached:
            return cached
    prompt = f"""You are the character architect for Private Gravity. Build a compact,
production-ready character bible for this story brief:
{json.dumps(brief, ensure_ascii=False)}

Return ONLY JSON: {{"characters": [...], "speculative_rules": [...]}}.
Every character needs character_id (lowercase snake_case), name, role, age, background,
personality, core_desire, underlying_need, central_fear, psychological_wound,
internal_contradiction, behavioral_patterns, relationships, initial_state, development_arc,
final_state, visual_identity, distinctive_details. Define exactly one protagonist. Keep the cast
small and each visual identity stable. If the premise is speculative, speculative_rules must be
clear and consistent; otherwise use an empty list."""
    bible = _generate_json(
        prompt, "character bible",
        lambda value: validate_character_bible(value, source_hash),
    )
    bible["source_hash"] = source_hash
    atomic_write_json(path, bible)
    return bible


def _outline(brief: dict, bible: dict, video_type: str, target_minutes: float,
             output: Path, force: bool) -> dict:
    source_hash = stable_hash({"brief": brief, "bible": bible, "video_type": video_type,
                               "target_minutes": target_minutes})
    path = output / "story_outline.json"
    if not force:
        cached = _cached(path, source_hash, validate_outline, bible)
        if cached:
            return cached
    wpm = int(getattr(config, "NARRATION_WPM", 125))
    target_words = 110 if video_type == "shorts" else round(target_minutes * wpm)
    scene_count = 4 if video_type == "shorts" else max(8, min(14, round(target_minutes * .75)))
    prompt = f"""You are the narrative architect for Private Gravity. Create a causal scene outline.
STORY BRIEF: {json.dumps(brief, ensure_ascii=False)}
CHARACTER BIBLE: {json.dumps(bible, ensure_ascii=False)}
FORMAT: {video_type}; TARGET: {target_words} narrated words across exactly {scene_count} scenes.

Return ONLY JSON {{"scenes": [...]}}. Each scene requires scene_id (s01 etc.), order,
title, narrative_function, summary, setting, characters_present (IDs from the bible),
dramatic_function, emotional_turn, causal_link to the next scene, target_words,
visual_direction, and image_queries (3 concrete stock-search phrases centered on settings,
objects, gestures, environments or silhouettes). The first scene must function as an opening;
the final scene as resolution/aftermath. Escalate through decisions and consequences. No lecture,
chapter summary, bullet lesson, generic CTA, or unsupported twist."""
    outline = _generate_json(
        prompt, "story outline", lambda value: validate_outline(value, bible)
    )
    outline["source_hash"] = source_hash
    outline["target_word_count"] = target_words
    outline["target_duration_minutes"] = target_minutes
    atomic_write_json(path, outline)
    return outline


def _write_scene_batch(brief: dict, bible: dict, outline: dict, scenes: list[dict],
                       previous_ending: str) -> list[dict]:
    prompt = f"""You are the scene writer for Private Gravity. Write broadcast-ready English
narration for ONLY the requested scenes. The narrator tells an original cinematic story; never
explains psychology to the audience. Preserve character identity, chronology, speculative rules,
causality and the allocated word counts (within 20%). Make sentences natural for spoken audio.

STORY BRIEF: {json.dumps(brief, ensure_ascii=False)}
CHARACTER BIBLE: {json.dumps(bible, ensure_ascii=False)}
FULL OUTLINE: {json.dumps(outline, ensure_ascii=False)}
SCENES TO WRITE: {json.dumps(scenes, ensure_ascii=False)}
PREVIOUS BATCH ENDING: {previous_ending or 'This is the opening batch.'}

Return ONLY JSON {{"scenes": [...]}} in requested order. Each object needs scene_id, title,
section_type, narration, setting, characters_present, dramatic_function, emotional_tone,
visual_direction, image_query, image_query_2, image_query_3, caption_text, on_screen_text.
Search queries must depict story-world environments/objects/actions and not educational diagrams.
Use on_screen_text only for diegetic or dramatically necessary text; otherwise empty string.
No placeholders, headings inside narration, moral summary, subscribe request or bullet points."""
    required = (
        "scene_id", "title", "section_type", "narration", "setting",
        "dramatic_function", "emotional_tone", "visual_direction", "image_query",
    )

    def validate_batch(data):
        result = data.get("scenes")
        if not isinstance(result, list) or len(result) != len(scenes):
            raise NarrativeValidationError("screenplay batch did not return every requested scene")
        requested = [scene["scene_id"] for scene in scenes]
        returned = [scene.get("scene_id") for scene in result]
        if returned != requested:
            raise NarrativeValidationError("screenplay batch scene IDs/order do not match outline")
        for scene in result:
            missing = [key for key in required if not str(scene.get(key, "")).strip()]
            if missing or len(scene["narration"].split()) < 12:
                raise NarrativeValidationError(
                    f"incomplete screenplay scene {scene.get('scene_id')}: {', '.join(missing)}"
                )
        return result

    return _generate_json(prompt, "screenplay scene batch", validate_batch)


def _editorial_review(script: dict) -> dict:
    prompt = f"""Act as a strict fiction editor. Review this Private Gravity screenplay without
rewriting it. Return ONLY JSON with numeric 1-10 scores for coherence, character_consistency,
emotional_escalation, originality, opening_strength, ending_resonance, visual_feasibility;
plus arrays strengths, risks, continuity_issues and recommended_scene_ids. Do not predict views.
SCREENPLAY: {json.dumps(script, ensure_ascii=False)}"""
    score_fields = (
        "coherence", "character_consistency", "emotional_escalation", "originality",
        "opening_strength", "ending_resonance", "visual_feasibility",
    )

    def validate_review(value):
        scores = value.get("scores", value)
        if any(not isinstance(scores.get(field), (int, float))
               or not 1 <= float(scores[field]) <= 10 for field in score_fields):
            raise NarrativeValidationError("editorial review requires 1-10 rubric scores")
        value["scores"] = {field: float(scores[field]) for field in score_fields}
        for field in ("strengths", "risks", "continuity_issues", "recommended_scene_ids"):
            if not isinstance(value.get(field), list):
                raise NarrativeValidationError(f"editorial review requires array: {field}")
        return value

    return _generate_json(prompt, "editorial review", validate_review)


def write_script(
    research: dict,
    video_type: str = "normal",
    output_dir: str | None = None,
    target_duration_minutes: float | None = None,
    narrative_review: bool | None = None,
    force: bool = False,
) -> dict:
    """Build/reuse bible and outline, then write the screenplay in bounded batches."""
    if video_type not in {"normal", "shorts"}:
        raise ValueError("video_type must be 'normal' or 'shorts'")
    brief = validate_story_brief(dict(research))
    output = Path(output_dir or config.OUTPUT_DIR)
    output.mkdir(parents=True, exist_ok=True)
    target = 0.85 if video_type == "shorts" else float(
        target_duration_minutes or brief.get("target_duration_minutes")
        or getattr(config, "TARGET_DURATION_MINUTES", 15)
    )
    if video_type == "normal" and not 12 <= target <= 20:
        raise ValueError("Regular Private Gravity videos must target 12-20 minutes")

    print("   -> Building character bible...")
    bible = _character_bible(brief, output, force)
    print("   -> Building causal story outline...")
    outline = _outline(brief, bible, video_type, target, output, force)

    sections = []
    previous_ending = ""
    for offset in range(0, len(outline["scenes"]), 3):
        batch = outline["scenes"][offset:offset + 3]
        print(f"   -> Writing scenes {offset + 1}-{offset + len(batch)}...")
        written = _write_scene_batch(brief, bible, outline, batch, previous_ending)
        for scene, plan in zip(written, batch):
            scene["id"] = int(plan["order"])
            scene["outline_scene_id"] = plan["scene_id"]
            scene["section_type"] = plan["narrative_function"]
            scene.setdefault("title", plan["title"])
            scene.setdefault("setting", plan["setting"])
            scene.setdefault("characters_present", plan["characters_present"])
            scene.setdefault("dramatic_function", plan["dramatic_function"])
            scene.setdefault("emotional_tone", plan["emotional_turn"])
            scene.setdefault("visual_direction", plan["visual_direction"])
            scene["bullet_points"] = []
        sections.extend(written)
        previous_ending = written[-1].get("narration", "")[-700:]

    script = {
        "schema_version": SCHEMA_VERSION,
        "content_mode": CONTENT_MODE,
        "video_type": video_type,
        "title": brief["video_title"] + (" #Shorts" if video_type == "shorts" else ""),
        "description": brief["description"],
        "tags": brief.get("tags", []),
        "narrative_type": brief["narrative_type"],
        "psychological_theme": brief["psychological_theme"],
        "planned_words_per_minute": int(getattr(config, "NARRATION_WPM", 125)),
        "target_duration_minutes": target,
        "source_hash": stable_hash({"brief": brief, "bible": bible, "outline": outline}),
        "characters": bible["characters"],
        "sections": sections,
    }
    script = validate_script(script, bible)
    atomic_write_json(output / "script.json", script)

    review = deterministic_review(script, target)
    use_editor = (getattr(config, "NARRATIVE_REVIEW_ENABLED", True)
                  if narrative_review is None else narrative_review)
    if use_editor:
        print("   -> Running optional editorial quality review...")
        review["editorial_assessment"] = _editorial_review(script)
    review["source_hash"] = stable_hash(script)
    atomic_write_json(output / "narrative_review.json", review)
    return script


def regenerate_scene(script: dict, brief: dict, bible: dict, outline: dict,
                     scene_id: int, output_dir: str | None = None) -> dict:
    """Regenerate one scene while preserving the approved narrative architecture."""
    output = Path(output_dir or config.OUTPUT_DIR)
    existing = next((scene for scene in script.get("sections", [])
                     if int(scene.get("id", -1)) == int(scene_id)), None)
    if not existing:
        raise ValueError(f"Unknown screenplay scene: {scene_id}")
    outline_id = existing.get("outline_scene_id")
    plan = next((scene for scene in outline.get("scenes", [])
                 if scene.get("scene_id") == outline_id or scene.get("order") == int(scene_id)), None)
    if not plan:
        raise ValueError(f"No outline plan for screenplay scene: {scene_id}")
    prior = [scene for scene in script["sections"] if int(scene["id"]) < int(scene_id)]
    previous_ending = prior[-1]["narration"][-700:] if prior else ""
    replacement = _write_scene_batch(brief, bible, outline, [plan], previous_ending)[0]
    replacement.update({
        "id": int(scene_id), "outline_scene_id": plan["scene_id"],
        "section_type": plan["narrative_function"], "bullet_points": [],
    })
    revised = dict(script)
    revised["sections"] = [replacement if int(scene["id"]) == int(scene_id) else scene
                           for scene in script["sections"]]
    revised = validate_script(revised, bible)
    atomic_write_json(output / "script.json", revised)
    review = deterministic_review(revised, float(revised.get("target_duration_minutes", 15)))
    review["source_hash"] = stable_hash(revised)
    atomic_write_json(output / "narrative_review.json", review)
    return revised
