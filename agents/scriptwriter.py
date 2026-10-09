"""Private Gravity character, outline and scene-writing pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

import config
from agents.gemini_client import generate
from agents.narrative import (
    CONTENT_MODE,
    EDITORIAL_DIMENSIONS,
    NARRATIVE_CONTRACT_VERSION,
    SCHEMA_VERSION,
    NarrativeValidationError,
    atomic_write_json,
    deterministic_review,
    extract_json,
    load_json,
    merge_editorial_review,
    rebalance_scene_word_targets,
    stable_hash,
    validate_character_bible,
    validate_editorial_assessment,
    validate_outline,
    validate_script,
    validate_story_brief,
)


def _generate_json(prompt: str, label: str, validator=None) -> dict:
    """Generate JSON with one parse repair or one targeted semantic repair."""
    raw = generate(prompt)
    try:
        value = extract_json(raw, label)
    except NarrativeValidationError as initial_error:
        # Parsing failed, so there is no reliable document to patch. One broad
        # syntax/completeness repair is appropriate, and remains bounded.
        repair = f"""The response below could not be parsed as the required {label} JSON.
Return exactly one complete JSON object with no markdown or commentary. Preserve every readable
story decision from the response and satisfy the original contract.

PARSING ERROR:
{initial_error}

ORIGINAL CONTRACT:
{prompt[:14000]}

UNPARSEABLE RESPONSE:
{raw[:10000]}"""
        return _repair_and_validate(repair, label, validator, initial_error)

    if validator is None:
        return value
    try:
        return validator(value)
    except (NarrativeValidationError, ValueError, KeyError, TypeError) as initial_error:
        # JSON is already valid. Send the intact document and exact validation
        # diagnostics, asking the model to touch only the invalid portions.
        repair = f"""The {label} JSON parsed successfully but failed semantic validation.
Return the COMPLETE corrected JSON object only. Correct only the fields identified below.
Preserve all valid character identities, motivations, relationships, story decisions, array order,
and every valid field exactly. Do not summarize, replace, or broadly rewrite the artifact.

VALIDATION ERROR:
{initial_error}

CURRENT VALID JSON:
{json.dumps(value, ensure_ascii=False, indent=2)[:18000]}"""
        return _repair_and_validate(repair, label, validator, initial_error)


def _repair_and_validate(repair_prompt: str, label: str, validator, initial_error) -> dict:
    repaired_raw = generate(repair_prompt)
    try:
        repaired = extract_json(repaired_raw, f"repaired {label}")
        return validator(repaired) if validator else repaired
    except (NarrativeValidationError, ValueError, KeyError, TypeError) as repaired_error:
        raise NarrativeValidationError(
            f"{label} repair failed after one bounded attempt. "
            f"Initial error: {initial_error}. Repaired artifact error: {repaired_error}"
        ) from repaired_error


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
            # Persist deterministic canonicalization of older cache entries
            # (notably list[str] behavioral_patterns) without an API call.
            atomic_write_json(path, cached)
            return cached
    prompt = f"""You are the character architect for Private Gravity. Build a compact,
production-ready character bible for this story brief:
{json.dumps(brief, ensure_ascii=False)}

Return ONLY one JSON object using this exact type contract. The object below is a TYPE/SHAPE
example only; do not copy its character or story content into the requested story:
{{
  "characters": [
    {{
      "character_id": "mara_voss",
      "name": "Mara Voss",
      "role": "protagonist",
      "age": "42",
      "background": "A municipal archivist who has spent twenty years avoiding conflict",
      "personality": "Meticulous, guarded, perceptive, and privately sentimental",
      "core_desire": "To keep her carefully controlled life intact",
      "underlying_need": "To admit that safety has become isolation",
      "central_fear": "Being seen clearly and then rejected",
      "psychological_wound": "A public betrayal taught her to conceal every vulnerable impulse",
      "internal_contradiction": "She longs for intimacy while punishing every attempt to approach her",
      "behavioral_patterns": "Rehearses difficult conversations; checks every locked door twice",
      "relationships": ["Jon: estranged brother whose calls she avoids"],
      "initial_state": "Efficient, isolated, and convinced that control is peace",
      "development_arc": "Moves from defensive control toward one costly act of honesty",
      "final_state": "Still afraid, but willing to remain present",
      "visual_identity": "Dark wool coat, precise posture, and a worn brass key ring",
      "distinctive_details": ["Aligns objects before speaking", "Touches the key ring when anxious"]
    }}
  ],
  "speculative_rules": []
}}

For behavioral_patterns use a canonical STRING, for example: "Avoids difficult conversations;
checks his phone for social approval." Never use null, an empty string, an empty array, generic
placeholders, or omit a required field. Keep the cast compact and preserve the brief's exact story
decisions. If the premise is not speculative, speculative_rules must be an empty array."""
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
                               "target_minutes": target_minutes,
                               "contract": NARRATIVE_CONTRACT_VERSION})
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
title, section_type (exactly one of opening, setup, development, reversal, climax, resolution,
aftermath), narrative_function (specific free-text purpose), summary, setting,
characters_present (IDs from the bible), initial_dramatic_situation, character_objective,
obstacle, dramatic_action, decision, consequence, new_information, dramatic_function,
emotional_turn, causal_link to the next scene, chronology_marker (specific relative time),
continuity_updates (array of explicit changes to knowledge, ownership, objects, relationships or
story rules; empty only when nothing changes), target_words,
visual_direction, and image_queries (an array of exactly 3 nonempty string stock-search phrases centered on settings,
objects, gestures, environments or silhouettes). The first scene must function as an opening;
the final scene as resolution/aftermath, with a climax before it. Every scene must change the
situation through a decision, discovery, confrontation or consequence. Assign varied word budgets
that sum approximately to the target; do not divide them mechanically. Escalate through decisions and consequences. No lecture,
chapter summary, bullet lesson, generic CTA, or unsupported twist."""
    outline = _generate_json(
        prompt, "story outline", lambda value: validate_outline(
            {**value, "narrative_contract_version": NARRATIVE_CONTRACT_VERSION}, bible, True
        )
    )
    outline = rebalance_scene_word_targets(outline, target_words)
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
causality and EACH allocated word count (85%-115%). Dramatize objectives, obstacles, decisions,
consequences and discoveries through action, specific dialogue and subtext. The protagonist must
make consequential choices; supporting characters need independent agency. Never pad with repeated
atmosphere, restated feelings, generic transitions or summary. Make sentences natural for spoken audio.

STORY BRIEF: {json.dumps(brief, ensure_ascii=False)}
CHARACTER BIBLE: {json.dumps(bible, ensure_ascii=False)}
FULL OUTLINE: {json.dumps(outline, ensure_ascii=False)}
SCENES TO WRITE: {json.dumps(scenes, ensure_ascii=False)}
PREVIOUS BATCH ENDING: {previous_ending or 'This is the opening batch.'}

Return ONLY JSON {{"scenes": [...]}} in requested order. Each object needs scene_id, title,
section_type (the exact canonical value from the outline), narrative_function (the exact free-text
function from the outline), narration, setting, characters_present, dramatic_function, emotional_tone,
visual_direction, image_query, image_query_2, image_query_3, caption_text, on_screen_text.
Search queries must depict story-world environments/objects/actions and not educational diagrams.
Use on_screen_text only for diegetic or dramatically necessary text; otherwise empty string.
No placeholders, headings inside narration, moral summary, subscribe request or bullet points."""
    required = (
        "scene_id", "title", "section_type", "narrative_function", "narration", "setting",
        "dramatic_function", "emotional_tone", "visual_direction", "image_query",
        "image_query_2", "image_query_3",
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
            narration = scene.get("narration")
            present = scene.get("characters_present")
            if (missing or not isinstance(narration, str) or len(narration.split()) < 12
                    or not isinstance(present, list)
                    or any(not isinstance(cid, str) for cid in present)):
                raise NarrativeValidationError(
                    f"incomplete screenplay scene {scene.get('scene_id')}: {', '.join(missing)}"
                )
        return result

    return _generate_json(prompt, "screenplay scene batch", validate_batch)


def _editorial_review(script: dict, brief: dict, bible: dict, outline: dict) -> dict:
    prompt = f"""Act as a skeptical senior fiction editor. Review without rewriting. Test every
continuity claim against the supplied Character Bible, outline and exact scene evidence. A high
score never cancels a contradiction or deterministic duration failure. Distinguish confirmed facts
from suspicions; do not invent off-page motives or events.

Return ONLY JSON with:
- dimensions: exactly these 15 keys, each {{"score": 1-10, "evidence": [specific scene evidence]}}:
{json.dumps(list(EDITORIAL_DIMENSIONS))}
- findings: an array of objects with severity (blocking|major|minor|suggestion), issue_type,
scene_ids (integer array), claim, evidence (string array), why_it_matters, recommendation,
certainty (confirmed|probable|possible).
- strengths and revision_priorities as arrays.

STORY BRIEF: {json.dumps(brief, ensure_ascii=False)}
CHARACTER BIBLE: {json.dumps(bible, ensure_ascii=False)}
OUTLINE: {json.dumps(outline, ensure_ascii=False)}
SCREENPLAY: {json.dumps(script, ensure_ascii=False)}"""
    return _generate_json(prompt, "editorial review", validate_editorial_assessment)


def _expand_underlength_scenes(script: dict, brief: dict, bible: dict,
                               outline: dict) -> tuple[dict, list[dict]]:
    """Make bounded, surgical expansion passes and preserve unaffected scenes exactly."""
    if script.get("video_type") == "shorts" or not getattr(
            config, "NARRATIVE_AUTO_REVISION_ENABLED", True):
        return script, []
    report = deterministic_review(
        script, float(script.get("target_duration_minutes", 15)), outline
    )
    deficient = [
        item for item in report["scene_budgets"]
        if item["status"] == "under_target"
    ]
    batch_size = max(1, int(getattr(config, "NARRATIVE_EXPANSION_BATCH_SIZE", 3)))
    max_calls = max(0, int(getattr(config, "NARRATIVE_EXPANSION_MAX_CALLS", 3)))
    revised = script
    attempts = []
    for offset in range(0, len(deficient), batch_size):
        if len(attempts) >= max_calls:
            break
        batch = deficient[offset:offset + batch_size]
        ids = [item["scene_id"] for item in batch]
        selected = [scene for scene in revised["sections"] if scene["id"] in ids]
        neighbors = [
            {"id": scene["id"], "title": scene["title"],
             "ending": scene["narration"][-350:]}
            for scene in revised["sections"]
            if any(abs(scene["id"] - scene_id) == 1 for scene_id in ids)
        ]
        prompt = f"""You are performing one bounded screenplay expansion pass. Return ONLY the
selected scenes, in order, as {{"scenes": [...]}}. Preserve every scene ID, canonical section_type,
narrative_function, established fact, chronology, character identity, causal outcome and useful
line already present. Add substantive dramatic action, consequential choice, credible discovery,
specific dialogue and subtext until each scene reaches 85%-115% of its target. Do not add filler,
repeated atmosphere, exposition summaries, new characters, new rules or unsupported twists.

EXACT DEFICITS: {json.dumps(batch, ensure_ascii=False)}
SELECTED SCENES: {json.dumps(selected, ensure_ascii=False)}
NEIGHBOR CONTEXT: {json.dumps(neighbors, ensure_ascii=False)}
STORY BRIEF: {json.dumps(brief, ensure_ascii=False)}
CHARACTER BIBLE: {json.dumps(bible, ensure_ascii=False)}
OUTLINE: {json.dumps(outline, ensure_ascii=False)}"""

        def validate_expansion(value):
            scenes = value.get("scenes")
            if not isinstance(scenes, list) or [scene.get("id") for scene in scenes] != ids:
                raise NarrativeValidationError("targeted expansion changed scene IDs or order")
            targets = {item["scene_id"]: item["target_words"] for item in batch}
            for scene in scenes:
                if not isinstance(scene.get("narration"), str):
                    raise NarrativeValidationError("targeted expansion requires narration")
                ratio = len(scene["narration"].split()) / targets[scene["id"]]
                if ratio < .85 or ratio > 1.15:
                    raise NarrativeValidationError(
                        f"expanded scene {scene['id']} remains outside 85%-115% ({ratio:.1%})"
                    )
            return scenes

        attempt = {"scene_ids": ids, "kind": "targeted_substantive_expansion", "success": False}
        try:
            replacements = _generate_json(prompt, "targeted scene expansion", validate_expansion)
            by_id = {scene["id"]: scene for scene in replacements}
            updated = []
            for original in revised["sections"]:
                replacement = by_id.get(original["id"])
                if replacement is None:
                    updated.append(original)
                    continue
                merged = dict(original)
                merged.update(replacement)
                for field in ("id", "outline_scene_id", "section_type", "narrative_function",
                              "target_words"):
                    merged[field] = original.get(field)
                merged["bullet_points"] = []
                updated.append(merged)
            candidate = dict(revised)
            candidate["sections"] = updated
            revised = validate_script(candidate, bible, outline)
            attempt["success"] = True
        except NarrativeValidationError as exc:
            attempt["error"] = str(exc)
        attempts.append(attempt)
    return revised, attempts


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
            scene["section_type"] = plan["section_type"]
            scene["narrative_function"] = plan["narrative_function"]
            scene["target_words"] = plan["target_words"]
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
        "narrative_contract_version": NARRATIVE_CONTRACT_VERSION,
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
    script = validate_script(script, bible, outline)
    script, repair_attempts = _expand_underlength_scenes(script, brief, bible, outline)
    atomic_write_json(output / "script.json", script)

    review = deterministic_review(script, target, outline, repair_attempts)
    use_editor = (getattr(config, "NARRATIVE_REVIEW_ENABLED", True)
                  if narrative_review is None else narrative_review)
    if use_editor:
        print("   -> Running optional editorial quality review...")
        review = merge_editorial_review(
            review, _editorial_review(script, brief, bible, outline)
        )
    else:
        review = merge_editorial_review(review, None)
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
        "section_type": plan["section_type"],
        "narrative_function": plan["narrative_function"],
        "target_words": plan["target_words"], "bullet_points": [],
    })
    revised = dict(script)
    revised["sections"] = [replacement if int(scene["id"]) == int(scene_id) else scene
                           for scene in script["sections"]]
    revised = validate_script(revised, bible, outline)
    atomic_write_json(output / "script.json", revised)
    review = deterministic_review(
        revised, float(revised.get("target_duration_minutes", 15)), outline,
        [{"scene_ids": [int(scene_id)], "kind": "manual_scene_regeneration", "success": True}],
    )
    review = merge_editorial_review(review, None)
    review["editorial_review_stale"] = True
    review["source_hash"] = stable_hash(revised)
    atomic_write_json(output / "narrative_review.json", review)
    return revised
    merge_editorial_review,
    rebalance_scene_word_targets,
    validate_editorial_assessment,
