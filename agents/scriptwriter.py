"""Private Gravity character, outline and scene-writing pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import config
from agents import structured
from agents.gemini_client import generate
from agents.identity import TITLE_POLICY, identity_block
from agents.structured import SceneScope, compact_json
from agents.narrative import (
    CONTENT_MODE,
    ARC_MOVEMENTS,
    EDITORIAL_DIMENSIONS,
    TYPE_A,
    NARRATIVE_CONTRACT_VERSION,
    SCHEMA_VERSION,
    NarrativeValidationError,
    atomic_write_json,
    deterministic_review,
    format_scene_issues,
    load_json,
    merge_editorial_review,
    outline_scene_issues,
    rebalance_scene_word_targets,
    stable_hash,
    validate_character_bible,
    validate_editorial_assessment,
    validate_outline,
    validate_script,
    validate_story_brief,
)


def _generate_json(contract: str, label: str, validator=None, **options) -> dict:
    """Generate JSON (contract first, then context) with one bounded repair.

    See ``agents.structured.generate_json`` for the repair strategy. ``generate``
    is resolved at call time so tests can patch this module's name.
    """
    return structured.generate_json(contract, label, validator, generator=generate, **options)


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
    contract = f"""You are the character architect for Private Gravity. Build a compact,
production-ready character bible for the STORY BRIEF supplied after this contract.

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
        contract, "character bible",
        lambda value: validate_character_bible(value, source_hash),
        context=f"STORY BRIEF: {compact_json(brief)}",
        stage="character_bible", temperature_key="character_bible",
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
    narrative_type = brief.get("narrative_type")
    movement_rule = (
        "arc_movements is REQUIRED for this Type A story: tag every scene with the movement(s) it "
        "expresses so that, across the outline, all of the movements appear unless the story "
        "deliberately compresses one."
        if narrative_type == TYPE_A else
        "arc_movements is optional for this narrative type; use [] when it does not apply."
    )
    contract = f"""You are the narrative architect for Private Gravity. Create a causal scene outline
for the STORY BRIEF and CHARACTER BIBLE supplied after this contract.
FORMAT: {video_type}; TARGET: {target_words} narrated words across exactly {scene_count} scenes.

{identity_block(narrative_type)}

Return ONLY JSON {{"scenes": [...]}}. Each scene requires scene_id (s01 etc.), order,
title, section_type (exactly one of opening, setup, development, reversal, climax, resolution,
aftermath), narrative_function (specific free-text purpose), summary, setting,
characters_present (IDs from the bible), initial_dramatic_situation, character_objective,
obstacle, dramatic_action, decision, consequence, new_information, dramatic_function,
emotional_turn, causal_link to the next scene, chronology_marker (specific relative time),
continuity_updates (array of explicit changes to knowledge, ownership, objects, relationships or
story rules; empty only when nothing changes), target_words,
arc_movements (array of values from {json.dumps(list(ARC_MOVEMENTS))}; {movement_rule}),
visual_direction, and image_queries (an array of exactly 3 nonempty string stock-search phrases centered on settings,
objects, gestures, environments or silhouettes). The first scene must function as an opening;
the final scene as resolution/aftermath, with a climax before it. Every scene must change the
situation through a decision, discovery, confrontation or consequence. Assign varied word budgets
that sum approximately to the target; do not divide them mechanically. Escalate through decisions and consequences. No lecture,
chapter summary, bullet lesson, generic CTA, or unsupported twist."""
    outline = _generate_json(
        contract, "story outline", lambda value: validate_outline(
            {**value, "narrative_contract_version": NARRATIVE_CONTRACT_VERSION}, bible, True
        ),
        context=f"STORY BRIEF: {compact_json(brief)}\nCHARACTER BIBLE: {compact_json(bible)}",
        stage="outline", temperature_key="outline",
        scene_scope=SceneScope(issues=lambda value: outline_scene_issues(value, bible, True)),
    )
    outline = rebalance_scene_word_targets(outline, target_words)
    outline["source_hash"] = source_hash
    outline["target_word_count"] = target_words
    outline["target_duration_minutes"] = target_minutes
    atomic_write_json(path, outline)
    return outline


def _write_scene_batch(brief: dict, bible: dict, outline: dict, scenes: list[dict],
                       previous_ending: str) -> list[dict]:
    contract = f"""You are the scene writer for Private Gravity. Write broadcast-ready English
narration for ONLY the SCENES TO WRITE supplied after this contract. The narrator tells an
original cinematic story; never explains psychology to the audience. Preserve character identity,
chronology, speculative rules, causality and EACH allocated word count (85%-115%). Dramatize
objectives, obstacles, decisions, consequences and discoveries through action. The protagonist
must make consequential choices. Never pad with repeated atmosphere, restated feelings, generic
transitions or summary. Make sentences natural for spoken audio. When a scene's outline lists
arc_movements, the narration must deliver those movements on the page (for character_wound, the
wound itself must appear in the screenplay).

{identity_block(brief.get("narrative_type"))}

Return ONLY JSON {{"scenes": [...]}} in requested order. Each object needs scene_id, title,
section_type (the exact canonical value from the outline), narrative_function (the exact free-text
function from the outline), narration, setting, characters_present, dramatic_function, emotional_tone,
visual_direction, image_query, image_query_2, image_query_3, caption_text, on_screen_text.
Search queries must depict story-world environments/objects/actions and not educational diagrams.
Use on_screen_text only for diegetic or dramatically necessary text; otherwise empty string.
No placeholders, headings inside narration, moral summary, subscribe request or bullet points."""
    context = f"""STORY BRIEF: {compact_json(brief)}
CHARACTER BIBLE: {compact_json(bible)}
FULL OUTLINE: {compact_json(outline)}
SCENES TO WRITE: {compact_json(scenes)}
PREVIOUS BATCH ENDING: {previous_ending or 'This is the opening batch.'}"""
    required = (
        "scene_id", "title", "section_type", "narrative_function", "narration", "setting",
        "dramatic_function", "emotional_tone", "visual_direction", "image_query",
        "image_query_2", "image_query_3",
    )
    requested = [scene["scene_id"] for scene in scenes]

    def scene_issues(data):
        issues = {}
        result = data.get("scenes") if isinstance(data, dict) else None
        for scene in result if isinstance(result, list) else []:
            if not isinstance(scene, dict) or scene.get("scene_id") not in requested:
                continue
            problems = []
            missing = [key for key in required if not str(scene.get(key, "")).strip()]
            if missing:
                problems.append(f"missing nonempty fields: {', '.join(missing)}")
            narration = scene.get("narration")
            if not isinstance(narration, str) or len(narration.split()) < 12:
                problems.append("narration must be a string of at least 12 words")
            present = scene.get("characters_present")
            if not isinstance(present, list) or any(not isinstance(cid, str) for cid in present):
                problems.append("characters_present must be an array of character_id strings")
            if problems:
                issues[scene["scene_id"]] = problems
        return issues

    def validate_batch(data):
        result = data.get("scenes")
        if not isinstance(result, list) or len(result) != len(scenes):
            raise NarrativeValidationError(
                f"screenplay batch must return exactly the requested scenes {requested}; "
                f"received {len(result) if isinstance(result, list) else 0}"
            )
        returned = [scene.get("scene_id") if isinstance(scene, dict) else None for scene in result]
        if returned != requested:
            raise NarrativeValidationError(
                f"screenplay batch scene IDs/order must be {requested}; received {returned}"
            )
        issues = scene_issues(data)
        if issues:
            raise NarrativeValidationError(format_scene_issues("screenplay batch", issues))
        return result

    label = f"scenes {requested[0]}-{requested[-1]}" if len(requested) > 1 else f"scene {requested[0]}"
    return _generate_json(
        contract, "screenplay scene batch", validate_batch, context=context,
        stage=label, temperature_key="scenes", scene_scope=SceneScope(issues=scene_issues),
    )


def _measurements(deterministic: dict | None) -> dict:
    """Deterministic facts the editor must take as given (never re-counted)."""
    report = deterministic or {}
    return {
        "total_words": report.get("word_count"),
        "target_words": report.get("target_word_count"),
        "estimated_narration_minutes": report.get("estimated_narration_minutes"),
        "scenes": [
            {"scene_id": item.get("scene_id"), "actual_words": item.get("actual_words"),
             "target_words": item.get("target_words")}
            for item in report.get("scene_budgets") or []
        ],
        "style_lint": report.get("style_lint", {}),
    }


def _editorial_review(script: dict, brief: dict, bible: dict, outline: dict,
                      deterministic: dict | None = None) -> dict:
    narrative_type = brief.get("narrative_type")
    strategy_rule = (
        "This is a Type A story: score it." if narrative_type == TYPE_A else
        'This is not a Type A story: use {"score": null, "not_applicable": true, '
        '"evidence": ["why it does not apply"]}.'
    )
    contract = f"""Act as a skeptical senior fiction editor. Review without rewriting. Test every
continuity claim against the supplied Character Bible, outline and exact scene evidence. A high
score never cancels a contradiction or deterministic duration failure. Distinguish confirmed facts
from suspicions; do not invent off-page motives or events. Judge the episode against the channel
identity below.

{identity_block(narrative_type)}

{TITLE_POLICY}

Return ONLY JSON with:
- dimensions: exactly these {len(EDITORIAL_DIMENSIONS)} keys, each {{"score": 1-10, "evidence": [specific scene evidence]}}:
{json.dumps(list(EDITORIAL_DIMENSIONS))}
  Identity dimensions, each citing scene evidence:
  channel_identity_adherence: ideas emerge through choice, consequence, image and subtext.
  hook_strength: the first 60-75 narrated words open on a situation with a striking fact and an
  open question. title_curiosity: the title follows the title policy and creates curiosity.
  philosophical_integration: meaning surfaces briefly after the reversal, never as a lecture.
  ending_openness: a final choice or recognition, an uncomfortable open question, no moral.
  strategy_success_then_cost: the strategy succeeds first and its hidden cost emerges
  progressively. {strategy_rule}
- findings: an array of objects with severity (blocking|major|minor|suggestion), issue_type,
scene_ids (integer array), claim, evidence (string array), why_it_matters, recommendation,
certainty (confirmed|probable|possible). Any lecture-like or explanatory passage is at least a
major finding with issue_type "lecture".
- strengths and revision_priorities as arrays.

WORD COUNTS AND DURATION ARE PRE-COMPUTED: the DETERMINISTIC MEASUREMENTS below are exact and
authoritative. Do not count words, estimate runtime or report duration, length or word-count
findings; those verdicts belong to the deterministic checks. Use style_lint as context when
scoring literary_style."""
    context = f"""DETERMINISTIC MEASUREMENTS: {compact_json(_measurements(deterministic))}
STORY BRIEF: {compact_json(brief)}
CHARACTER BIBLE: {compact_json(bible)}
OUTLINE: {compact_json(outline)}
SCREENPLAY: {compact_json(script)}"""
    return _generate_json(
        contract, "editorial review",
        lambda value: validate_editorial_assessment(value, narrative_type, allow_legacy=False),
        context=context, stage="editorial_review", temperature_key="editorial_review",
    )


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
        contract = f"""You are performing one bounded screenplay expansion pass. Return ONLY the
SELECTED SCENES supplied after this contract, in order, as {{"scenes": [...]}}. Preserve every scene
ID, canonical section_type, narrative_function, established fact, chronology, character identity,
causal outcome and useful line already present. Add substantive dramatic action, consequential
choice, credible discovery, specific dialogue and subtext until each scene reaches 85%-115% of its
target. Do not add filler, repeated atmosphere, exposition summaries, new characters, new rules or
unsupported twists.

EXACT DEFICITS: {compact_json(batch)}"""
        context = f"""SELECTED SCENES: {compact_json(selected)}
NEIGHBOR CONTEXT: {compact_json(neighbors)}
STORY BRIEF: {compact_json(brief)}
CHARACTER BIBLE: {compact_json(bible)}
OUTLINE: {compact_json(outline)}"""

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
            replacements = _generate_json(
                contract, "targeted scene expansion", validate_expansion, context=context,
                stage=f"expansion {'-'.join(str(scene_id) for scene_id in ids)}",
                temperature_key="expansion",
            )
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


SEMANTIC_REVISION_TYPES = (
    "lecture", "continuity", "unintroduced_character", "character_consistency", "agency",
    "causality", "repetition", "style",
)
# Reviewer spellings -> canonical eligible type. Unknown types are not eligible.
_SEMANTIC_ALIASES = {
    "lecture": "lecture", "didactic": "lecture", "exposition": "lecture",
    "explanation": "lecture", "lecturing": "lecture",
    "continuity": "continuity", "causal_continuity": "continuity",
    "chronological_continuity": "continuity", "continuity_error": "continuity",
    "object_continuity": "continuity", "costume_continuity": "continuity",
    "unintroduced_character": "unintroduced_character", "new_character": "unintroduced_character",
    "unestablished_character": "unintroduced_character",
    "unintroduced_characters": "unintroduced_character",
    "character_consistency": "character_consistency", "character_inconsistency": "character_consistency",
    "agency": "agency", "character_agency": "agency", "passivity": "agency",
    "causality": "causality", "causal_logic": "causality",
    "repetition": "repetition", "repetition_control": "repetition", "repetitive": "repetition",
    "style": "style", "literary_style": "style", "prose_style": "style",
}


def semantic_issue_type(raw) -> str | None:
    """Normalize a reviewer issue_type; None when it is not eligible for revision."""
    key = "_".join(str(raw or "").strip().lower().replace("-", " ").split())
    canonical = _SEMANTIC_ALIASES.get(key)
    allowed = getattr(config, "NARRATIVE_SEMANTIC_REVISION_TYPES", SEMANTIC_REVISION_TYPES)
    return canonical if canonical in allowed else None


def eligible_semantic_findings(review: dict, script: dict) -> dict[int, list[dict]]:
    """Map screenplay scene id -> eligible editorial findings (discarded ones never count)."""
    ids = {int(scene["id"]): int(scene["id"]) for scene in script.get("sections", [])}
    ids.update({scene.get("outline_scene_id"): int(scene["id"])
                for scene in script.get("sections", [])})
    by_scene: dict[int, list[dict]] = {}
    for finding in review.get("blocking_issues", []) + review.get("major_issues", []):
        if "certainty" not in finding:          # deterministic issue, not editorial
            continue
        if finding.get("certainty") not in ("confirmed", "probable"):
            continue
        if semantic_issue_type(finding.get("issue_type")) is None:
            continue
        for raw in finding.get("scene_ids") or []:
            scene_id = ids.get(raw if not isinstance(raw, str) or not raw.isdigit() else int(raw))
            if scene_id is not None:
                by_scene.setdefault(scene_id, []).append(finding)
    return by_scene


def _semantic_revision(script: dict, brief: dict, bible: dict, outline: dict, review: dict,
                       output: Path, target: float, repair_attempts: list) -> tuple[dict, dict]:
    """One bounded rewrite of the scenes cited by eligible editorial findings.

    Unaffected scenes stay byte-for-byte; each revised scene must stay within
    85%-115% of its allocation. Afterwards the deterministic review runs again
    plus at most one editorial re-review. Never loops further.
    """
    if not getattr(config, "NARRATIVE_SEMANTIC_REVISION_ENABLED", True):
        return script, review
    by_scene = eligible_semantic_findings(review, script)
    if not by_scene:
        return script, review
    batch_size = max(1, int(getattr(config, "NARRATIVE_SEMANTIC_REVISION_BATCH_SIZE", 3)))
    max_calls = max(0, int(getattr(config, "NARRATIVE_SEMANTIC_REVISION_MAX_CALLS", 1)))
    scene_ids = sorted(by_scene)
    revised = script
    revisions = []
    for offset in range(0, len(scene_ids), batch_size):
        if len(revisions) >= max_calls:
            break
        ids = scene_ids[offset:offset + batch_size]
        selected = [scene for scene in revised["sections"] if scene["id"] in ids]
        neighbors = [scene for scene in revised["sections"]
                     if scene["id"] not in ids and any(abs(scene["id"] - i) == 1 for i in ids)]
        findings = []
        for scene_id in ids:
            for finding in by_scene[scene_id]:
                if finding not in findings:
                    findings.append(finding)
        budgets = {scene["id"]: scene["target_words"] for scene in selected}
        contract = f"""You are performing one bounded editorial revision of a Private Gravity screenplay.
Return ONLY the SELECTED SCENES supplied after this contract, in order, as {{"scenes": [...]}} with
the same keys. Fix exactly the problems in the EDITORIAL FINDINGS by following their
recommendations, through dramatized action, dialogue and subtext. Preserve every scene id,
canonical section_type, narrative_function, established fact, chronology, character identity,
causal outcome and every line the findings do not touch. Do not add new characters, rules or
twists. Never let the narrator explain psychology or cite thinkers academically.

{identity_block(brief.get("narrative_type"))}

WORD BUDGETS (each revised narration must stay between 85% and 115% of its target):
{compact_json([{"id": i, "target_words": w, "minimum": int(w * .85) + 1, "maximum": int(w * 1.15)}
               for i, w in budgets.items()])}

EDITORIAL FINDINGS:
{compact_json([{key: finding.get(key) for key in ("severity", "issue_type", "scene_ids", "claim",
                "evidence", "recommendation")} for finding in findings])}"""
        context = f"""SELECTED SCENES: {compact_json(selected)}
NEIGHBOR SCENES (read-only): {compact_json(neighbors)}
STORY BRIEF: {compact_json(brief)}
CHARACTER BIBLE: {compact_json(bible)}"""

        def validate_revision(value, ids=ids, budgets=budgets):
            scenes = value.get("scenes")
            if not isinstance(scenes, list) or [scene.get("id") for scene in scenes] != ids:
                raise NarrativeValidationError(
                    f"semantic revision must return scenes {ids} in order"
                )
            for scene in scenes:
                if not isinstance(scene.get("narration"), str):
                    raise NarrativeValidationError(f"revised scene {scene['id']} requires narration")
                ratio = len(scene["narration"].split()) / budgets[scene["id"]]
                if ratio < .85 or ratio > 1.15:
                    raise NarrativeValidationError(
                        f"revised scene {scene['id']} is outside 85%-115% of its "
                        f"{budgets[scene['id']]}-word budget ({ratio:.1%})"
                    )
            return scenes

        record = {
            "kind": "targeted_semantic_revision", "scene_ids": ids, "success": False,
            "findings_addressed": [
                {key: finding.get(key) for key in ("severity", "issue_type", "scene_ids", "claim")}
                for finding in findings
            ],
            "word_counts": {str(scene["id"]): {"before": len(scene["narration"].split())}
                            for scene in selected},
        }
        try:
            replacements = _generate_json(
                contract, "semantic revision", validate_revision, context=context,
                stage=f"semantic_revision {'-'.join(str(i) for i in ids)}",
                temperature_key="expansion",
            )
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
            candidate = validate_script(dict(revised, sections=updated), bible, outline)
            for scene in candidate["sections"]:
                if str(scene["id"]) in record["word_counts"]:
                    record["word_counts"][str(scene["id"])]["after"] = len(scene["narration"].split())
            revised = candidate
            record["success"] = True
        except NarrativeValidationError as exc:
            record["error"] = str(exc)
        revisions.append(record)

    if not any(item["success"] for item in revisions):
        review = dict(review, semantic_revisions=revisions)
        return script, review

    atomic_write_json(output / "script.pre_revision.json", script)
    attempts = list(repair_attempts) + [
        {key: item[key] for key in ("kind", "scene_ids", "success")} for item in revisions
    ]
    new_review = deterministic_review(revised, target, outline, attempts, brief)
    rereview = {"performed": False}
    if getattr(config, "NARRATIVE_SEMANTIC_REREVIEW", True):
        print("   -> Re-running editorial review after the semantic revision...")
        new_review = merge_editorial_review(
            new_review, _editorial_review(revised, brief, bible, outline, new_review), bible
        )
        rereview = {
            "performed": True, "status": new_review["status"],
            "remaining_major_or_blocking": len([
                item for item in new_review["blocking_issues"] + new_review["major_issues"]
                if "certainty" in item
            ]),
        }
    else:
        new_review = merge_editorial_review(new_review, None)
        new_review["editorial_review_stale"] = True
        new_review["pre_revision_editorial_assessment"] = review.get("editorial_assessment")
    for item in revisions:
        item["rereview"] = rereview
    new_review["semantic_revisions"] = revisions
    new_review["pre_revision_status"] = review.get("status")
    return revised, new_review


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
    token = structured.debug_dir.set(str(output))
    try:
        return _write_script(brief, video_type, output, target, narrative_review, force)
    finally:
        structured.debug_dir.reset(token)


def _write_script(brief: dict, video_type: str, output: Path, target: float,
                  narrative_review: bool | None, force: bool) -> dict:
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
        "thumbnail_text": brief.get("thumbnail_text", ""),
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

    review = deterministic_review(script, target, outline, repair_attempts, brief)
    use_editor = (getattr(config, "NARRATIVE_REVIEW_ENABLED", True)
                  if narrative_review is None else narrative_review)
    if use_editor:
        print("   -> Running optional editorial quality review...")
        review = merge_editorial_review(
            review, _editorial_review(script, brief, bible, outline, review), bible
        )
        revised, review = _semantic_revision(
            script, brief, bible, outline, review, output, target, repair_attempts
        )
        if revised is not script:
            script = revised
            atomic_write_json(output / "script.json", script)
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
    token = structured.debug_dir.set(str(output))
    try:
        replacement = _write_scene_batch(brief, bible, outline, [plan], previous_ending)[0]
    finally:
        structured.debug_dir.reset(token)
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
        brief,
    )
    review = merge_editorial_review(review, None)
    review["editorial_review_stale"] = True
    review["source_hash"] = stable_hash(revised)
    atomic_write_json(output / "narrative_review.json", review)
    return revised
