"""Schemas, validation and artifact helpers for Private Gravity stories."""
from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path


SCHEMA_VERSION = 2
NARRATIVE_CONTRACT_VERSION = 3
CONTENT_MODE = "cinematic_fiction"
SCENE_TYPES = (
    "opening", "setup", "development", "reversal", "climax", "resolution", "aftermath",
)
EDITORIAL_DIMENSIONS = (
    "structural_coherence", "causal_continuity", "chronological_continuity",
    "character_consistency", "character_agency", "dialogue_and_subtext",
    "dramatic_progression", "emotional_escalation", "reversal_quality",
    "climax_effectiveness", "ending_resonance", "originality",
    "literary_style", "repetition_control", "visual_feasibility",
)
NARRATIVE_TYPES = {
    "realistic_psychological_fiction",
    "philosophical_thought_experiment",
    "dark_human_parable",
}
NARRATIVE_TYPE_LABELS = {
    "realistic_psychological_fiction": "Realistic Psychological Fiction",
    "philosophical_thought_experiment": "Philosophical Thought Experiment",
    "dark_human_parable": "Dark Human Parable",
}
PSYCHOLOGICAL_THEMES = (
    "Loneliness and belonging",
    "Status and social comparison",
    "Identity and authenticity",
    "Ambition and sacrifice",
    "Regret and alternative lives",
    "Approval and rejection",
    "Envy and resentment",
    "Love, intimacy, and vulnerability",
    "Mortality and memory",
    "Meaning and existential uncertainty",
    "Perfectionism and control",
    "Human irrationality and self-deception",
)


class NarrativeValidationError(ValueError):
    pass


def extract_json(raw: str, label: str = "Gemini response") -> dict:
    text = re.sub(r"^```(?:json)?", "", str(raw or "").strip(), flags=re.I)
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise NarrativeValidationError(f"{label} contained no complete JSON object")
    try:
        value = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise NarrativeValidationError(
            f"{label} returned malformed JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, dict):
        raise NarrativeValidationError(f"{label} must be a JSON object")
    return value


def atomic_write_json(path, data: dict) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, destination)
    return str(destination)


def load_json(path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def stable_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_story_brief(brief: dict) -> dict:
    _require_mapping(brief, "story brief")
    _require_text_fields(brief, (
        "video_title", "narrative_type", "psychological_theme", "logline",
        "main_character_concept", "central_dramatic_question", "protagonist_desire",
        "underlying_emotional_need", "principal_conflict", "central_contradiction",
        "stakes", "potential_turning_point", "intended_emotional_experience",
        "philosophical_question", "ending_direction", "intended_audience",
        "thumbnail_concept", "originality_assessment", "production_feasibility",
    ), "story brief")
    if brief["narrative_type"] not in NARRATIVE_TYPES:
        raise NarrativeValidationError(
            f"Invalid narrative_type: {brief['narrative_type']!r}"
        )
    candidates = brief.get("premise_candidates")
    if not isinstance(candidates, list) or len(candidates) < 1:
        raise NarrativeValidationError("story brief requires premise_candidates")
    score_fields = (
        "originality_score", "emotional_power_score", "dramatic_potential_score",
        "visual_feasibility_score", "audience_access_score", "weighted_total",
    )
    for index, candidate in enumerate(candidates):
        _require_mapping(candidate, f"premise candidate {index + 1}")
        _require_text_fields(candidate, ("premise", "narrative_type"),
                             f"premise candidate {index + 1}")
        if candidate["narrative_type"] not in NARRATIVE_TYPES:
            raise NarrativeValidationError("premise candidate has invalid narrative_type")
        if any(not isinstance(candidate.get(field), (int, float)) for field in score_fields):
            raise NarrativeValidationError("premise candidate requires numeric weighted scores")
        if any(not 1 <= float(candidate[field]) <= 10 for field in score_fields):
            raise NarrativeValidationError("premise candidate scores must be between 1 and 10")
        if not isinstance(candidate.get("strengths", []), list) or not isinstance(candidate.get("risks", []), list):
            raise NarrativeValidationError("premise candidate strengths and risks must be arrays")
        candidate["weighted_total"] = round(
            float(candidate["dramatic_potential_score"]) * .30
            + float(candidate["emotional_power_score"]) * .25
            + float(candidate["originality_score"]) * .20
            + float(candidate["audience_access_score"]) * .15
            + float(candidate["visual_feasibility_score"]) * .10,
            2,
        )
    selected = brief.get("selected_candidate_index")
    if not isinstance(selected, int) or not 0 <= selected < len(candidates):
        raise NarrativeValidationError("selected_candidate_index must reference a candidate")
    brief["selected_candidate_index"] = max(
        range(len(candidates)), key=lambda index: candidates[index]["weighted_total"]
    )
    brief["schema_version"] = SCHEMA_VERSION
    brief["content_mode"] = CONTENT_MODE
    brief.setdefault("topic", brief["logline"])
    brief.setdefault("description", _fictional_description(brief))
    brief.setdefault("why_now", brief["intended_emotional_experience"])
    brief.setdefault("hook_question", brief["central_dramatic_question"])
    brief.setdefault("key_points", [
        brief["protagonist_desire"], brief["principal_conflict"],
        brief["potential_turning_point"], brief["philosophical_question"],
    ])
    brief.setdefault("tags", _default_tags(brief))
    return brief


def normalize_character_bible(bible: dict) -> dict:
    """Return a canonical copy without inventing missing narrative content.

    Gemini sometimes represents ``behavioral_patterns`` as ``list[str]``.
    That representation is semantically equivalent to the canonical string
    consumed by the rest of the application, so it can be joined safely.
    Unsupported, empty or mixed arrays are deliberately left untouched for
    the validator to reject with a precise diagnostic.
    """
    if not isinstance(bible, dict):
        return bible
    normalized = _trim_strings(deepcopy(bible))
    characters = normalized.get("characters")
    if not isinstance(characters, list):
        return normalized
    for character in characters:
        if not isinstance(character, dict):
            continue
        age = character.get("age")
        if isinstance(age, (int, float)) and not isinstance(age, bool):
            character["age"] = str(age)
        patterns = character.get("behavioral_patterns")
        if (isinstance(patterns, list) and patterns
                and all(isinstance(item, str) and item.strip() for item in patterns)):
            character["behavioral_patterns"] = "; ".join(item.strip() for item in patterns)
    return normalized


def validate_character_bible(bible: dict, story_hash: str | None = None) -> dict:
    bible = normalize_character_bible(bible)
    if not isinstance(bible, dict):
        raise _validation_error(
            "Character Bible", "$", "object", bible, "incorrect root type"
        )
    characters = bible.get("characters")
    if "characters" not in bible:
        raise _validation_error(
            "Character Bible", "characters", "nonempty array[object]", _MISSING, "missing field"
        )
    if not isinstance(characters, list):
        raise _validation_error(
            "Character Bible", "characters", "nonempty array[object]", characters, "incorrect field type"
        )
    if not characters:
        raise _validation_error(
            "Character Bible", "characters", "nonempty array[object]", characters, "empty required field"
        )
    ids = []
    protagonists = 0
    required = (
        "character_id", "name", "role", "age", "background", "personality",
        "core_desire", "underlying_need", "central_fear", "psychological_wound",
        "internal_contradiction", "behavioral_patterns", "initial_state",
        "development_arc", "final_state", "visual_identity",
    )
    for index, character in enumerate(characters):
        path = f"character[{index}]"
        if not isinstance(character, dict):
            raise _validation_error(
                "Character Bible", path, "object", character, "incorrect character record type"
            )
        for field in required:
            _require_character_text(character, index, field)
        cid = character["character_id"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", cid):
            raise _validation_error(
                "Character Bible", f"{path}.character_id",
                "lowercase snake_case string matching [a-z][a-z0-9_]{1,39}",
                cid, "invalid character ID",
            )
        if cid in ids:
            raise _validation_error(
                "Character Bible", f"{path}.character_id", "unique character ID",
                cid, "duplicate character ID",
            )
        ids.append(cid)
        if character["role"].strip().lower() == "protagonist":
            protagonists += 1
        _require_string_array(character, index, "relationships", allow_empty=True)
        _require_string_array(character, index, "distinctive_details", allow_empty=False)
    if protagonists != 1:
        raise _validation_error(
            "Character Bible", "characters[*].role", "exactly one 'protagonist'",
            f"count={protagonists}", "missing or ambiguous protagonist",
        )
    bible["schema_version"] = SCHEMA_VERSION
    bible["content_mode"] = CONTENT_MODE
    if story_hash:
        bible["source_story_hash"] = story_hash
    rules = bible.get("speculative_rules")
    if rules is None and "speculative_rules" not in bible:
        rules = []
        bible["speculative_rules"] = rules
    if not isinstance(rules, list):
        raise _validation_error(
            "Character Bible", "speculative_rules", "array[string]", rules,
            "incorrect field type",
        )
    if any(not isinstance(rule, str) or not rule.strip() for rule in rules):
        raise _validation_error(
            "Character Bible", "speculative_rules", "array of nonempty strings", rules,
            "invalid array element type or empty element",
        )
    if len({rule.strip().lower() for rule in rules}) != len(rules):
        raise _validation_error(
            "Character Bible", "speculative_rules", "unique nonempty strings", rules,
            "duplicate speculative rule",
        )
    return bible


def infer_scene_type(value, index: int = 0, total: int = 1) -> str:
    """Map legacy/free-text narrative labels to the small canonical taxonomy."""
    text = str(value or "").strip().lower()
    aliases = (
        ("aftermath", ("aftermath", "epilogue")),
        ("resolution", ("resolution", "ending", "denouement", "closure")),
        ("climax", ("climax", "final confrontation", "culmination")),
        ("reversal", ("reversal", "turning point", "reveal", "discovery", "twist")),
        ("opening", ("opening", "hook", "cold open", "introduction")),
        ("setup", ("setup", "inciting", "establish")),
        ("development", ("development", "escalation", "confrontation", "complication")),
    )
    if text in SCENE_TYPES:
        return text
    for canonical, words in aliases:
        if any(word in text for word in words):
            return canonical
    if index == 0:
        return "opening"
    if index == max(total - 1, 0):
        return "resolution"
    return "development"


def normalize_outline(outline: dict) -> dict:
    """Migrate schema-v2 outlines without discarding their free-text functions."""
    if not isinstance(outline, dict):
        return outline
    normalized = _trim_strings(deepcopy(outline))
    current_contract = normalized.get("narrative_contract_version", 0) == NARRATIVE_CONTRACT_VERSION
    scenes = normalized.get("scenes")
    if isinstance(scenes, list):
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            old_type = scene.get("section_type")
            free_text = scene.get("narrative_function")
            if not free_text and old_type and str(old_type).lower() not in SCENE_TYPES:
                scene["narrative_function"] = old_type
                free_text = old_type
            if current_contract and old_type and str(old_type).lower() not in SCENE_TYPES:
                scene["section_type"] = str(old_type).lower()
            else:
                scene["section_type"] = infer_scene_type(old_type or free_text, index, len(scenes))
    normalized["narrative_contract_version"] = NARRATIVE_CONTRACT_VERSION
    return normalized


def normalize_script(script: dict, outline: dict | None = None) -> dict:
    """Migrate old scripts while keeping canonical type and prose function separate."""
    if not isinstance(script, dict):
        return script
    normalized = _trim_strings(deepcopy(script))
    current_contract = normalized.get("narrative_contract_version", 0) == NARRATIVE_CONTRACT_VERSION
    plans = {}
    if isinstance(outline, dict):
        for plan in normalize_outline(outline).get("scenes", []):
            plans[plan.get("scene_id")] = plan
            plans[plan.get("order")] = plan
    sections = normalized.get("sections")
    if isinstance(sections, list):
        for index, scene in enumerate(sections):
            if not isinstance(scene, dict):
                continue
            plan = plans.get(scene.get("outline_scene_id")) or plans.get(scene.get("id")) or {}
            old_type = scene.get("section_type")
            if not scene.get("narrative_function"):
                scene["narrative_function"] = (
                    plan.get("narrative_function") or old_type or scene.get("dramatic_function", "")
                )
            proposed_type = plan.get("section_type") or old_type or scene.get("narrative_function")
            if current_contract and old_type and str(old_type).lower() not in SCENE_TYPES:
                scene["section_type"] = str(old_type).lower()
            else:
                scene["section_type"] = infer_scene_type(proposed_type, index, len(sections))
            if "target_words" not in scene and isinstance(plan.get("target_words"), int):
                scene["target_words"] = plan["target_words"]
    normalized["narrative_contract_version"] = NARRATIVE_CONTRACT_VERSION
    return normalized


def rebalance_scene_word_targets(outline: dict, total_target: int) -> dict:
    """Scale varied scene weights to an exact total without flattening the outline."""
    normalized = normalize_outline(outline)
    scenes = normalized.get("scenes", [])
    if not scenes or total_target < len(scenes) * 20:
        raise NarrativeValidationError("target word count is too small for the scene count")
    weights = [max(20, int(scene.get("target_words", 20))) for scene in scenes]
    raw = [total_target * weight / sum(weights) for weight in weights]
    allocated = [max(20, int(value)) for value in raw]
    delta = total_target - sum(allocated)
    order = sorted(range(len(scenes)), key=lambda i: raw[i] - int(raw[i]), reverse=delta > 0)
    cursor = 0
    while delta:
        index = order[cursor % len(order)]
        change = 1 if delta > 0 else -1
        if allocated[index] + change >= 20:
            allocated[index] += change
            delta -= change
        cursor += 1
    for scene, words in zip(scenes, allocated):
        scene["target_words"] = words
    normalized["target_word_count"] = total_target
    return normalized


def validate_outline(outline: dict, bible: dict, require_dramaturgy: bool = False) -> dict:
    outline = normalize_outline(outline)
    _require_mapping(outline, "story outline")
    scenes = outline.get("scenes")
    if not isinstance(scenes, list) or len(scenes) < 3:
        raise NarrativeValidationError("story outline requires at least three scenes")
    character_ids = {character["character_id"] for character in bible["characters"]}
    seen = set()
    previous_order = 0
    for index, scene in enumerate(scenes, 1):
        _require_mapping(scene, f"outline scene {index}")
        _require_text_fields(scene, (
            "scene_id", "title", "section_type", "narrative_function", "summary", "setting",
            "dramatic_function", "emotional_turn", "causal_link", "visual_direction",
        ), f"outline scene {index}")
        sid = scene["scene_id"]
        if scene["section_type"] not in SCENE_TYPES:
            raise NarrativeValidationError(f"Invalid section_type in outline scene {sid}")
        if require_dramaturgy:
            _require_text_fields(scene, (
                "initial_dramatic_situation", "character_objective", "obstacle",
                "dramatic_action", "decision", "consequence", "new_information",
                "chronology_marker",
            ), f"outline scene {index}")
            updates = scene.get("continuity_updates")
            if (not isinstance(updates, list) or
                    any(not isinstance(item, str) or not item.strip() for item in updates)):
                raise NarrativeValidationError(
                    f"outline scene {sid} continuity_updates must be an array of nonempty strings"
                )
        if sid in seen:
            raise NarrativeValidationError(f"Duplicate outline scene_id: {sid}")
        seen.add(sid)
        order = scene.get("order")
        if not isinstance(order, int) or order <= previous_order:
            raise NarrativeValidationError("outline scene order must be strictly increasing integers")
        previous_order = order
        present = scene.get("characters_present", [])
        if (not isinstance(present, list)
                or any(not isinstance(cid, str) or cid not in character_ids for cid in present)):
            raise NarrativeValidationError(f"Unknown character reference in outline scene {sid}")
        queries = scene.get("image_queries", [])
        if (not isinstance(queries, list) or len(queries) < 3
                or any(not isinstance(query, str) or not query.strip() for query in queries)):
            raise NarrativeValidationError(
                f"outline scene {sid} requires at least three nonempty string image_queries"
            )
        target_words = scene.get("target_words", 0)
        if not isinstance(target_words, int) or target_words < 20:
            raise NarrativeValidationError(f"outline scene {sid} requires target_words >= 20")
    types = [scene["section_type"] for scene in scenes]
    if types[0] not in ("opening", "setup"):
        raise NarrativeValidationError("outline is missing an opening narrative function")
    if types[-1] not in ("resolution", "aftermath"):
        raise NarrativeValidationError("outline is missing an ending narrative function")
    if require_dramaturgy:
        if "climax" not in types:
            raise NarrativeValidationError("outline is missing a canonical climax")
        decisions = {scene["decision"].strip().lower() for scene in scenes}
        consequences = {scene["consequence"].strip().lower() for scene in scenes}
        if len(decisions) < 2 or len(consequences) < 2:
            raise NarrativeValidationError(
                "outline scenes repeat decisions/consequences instead of meaningful progression"
            )
    outline["schema_version"] = SCHEMA_VERSION
    outline["content_mode"] = CONTENT_MODE
    return outline


def validate_script(script: dict, character_bible: dict | None = None,
                    outline: dict | None = None) -> dict:
    script = normalize_script(script, outline)
    _require_mapping(script, "screenplay")
    _require_text_fields(script, (
        "title", "description", "narrative_type", "psychological_theme"
    ), "screenplay")
    if script["narrative_type"] not in NARRATIVE_TYPES:
        raise NarrativeValidationError("screenplay has an invalid narrative_type")
    sections = script.get("sections")
    if not isinstance(sections, list) or len(sections) < 3:
        raise NarrativeValidationError("screenplay requires at least three complete scenes")
    known_characters = {
        item["character_id"] for item in (character_bible or {}).get("characters", [])
    }
    outline_scenes = normalize_outline(outline).get("scenes", []) if outline else []
    if outline_scenes and len(outline_scenes) != len(sections):
        raise NarrativeValidationError("screenplay scene count does not match the approved outline")
    seen = set()
    previous_id = 0
    for index, scene in enumerate(sections, 1):
        _require_mapping(scene, f"screenplay scene {index}")
        sid = scene.get("id")
        if not isinstance(sid, int) or sid < 1 or sid in seen or sid <= previous_id:
            raise NarrativeValidationError(f"Invalid or duplicate screenplay scene id: {sid!r}")
        seen.add(sid)
        previous_id = sid
        _require_text_fields(scene, (
            "section_type", "narrative_function", "title", "narration", "setting", "dramatic_function",
            "emotional_tone", "visual_direction", "image_query",
        ), f"screenplay scene {sid}")
        if scene["section_type"] not in SCENE_TYPES:
            raise NarrativeValidationError(f"Invalid section_type in screenplay scene {sid}")
        if outline_scenes:
            plan = outline_scenes[index - 1]
            if (scene.get("outline_scene_id") != plan.get("scene_id")
                    or scene["section_type"] != plan["section_type"]
                    or scene["narrative_function"] != plan["narrative_function"]):
                raise NarrativeValidationError(
                    f"screenplay scene {sid} does not preserve outline identity/function"
                )
        minimum_words = 12 if script.get("video_type") == "shorts" else 40
        if len(scene["narration"].split()) < minimum_words:
            raise NarrativeValidationError(
                f"screenplay scene {sid} narration is incomplete ({len(scene['narration'].split())} words)"
            )
        present = scene.get("characters_present", [])
        if not isinstance(present, list) or any(not isinstance(cid, str) for cid in present):
            raise NarrativeValidationError(
                f"screenplay scene {sid} characters_present must be an array of strings"
            )
        if known_characters and any(cid not in known_characters for cid in present):
            raise NarrativeValidationError(f"Unknown character reference in screenplay scene {sid}")
        for query_field in ("image_query_2", "image_query_3"):
            if not isinstance(scene.get(query_field), str) or not scene[query_field].strip():
                raise NarrativeValidationError(
                    f"screenplay scene {sid} requires nonempty {query_field}"
                )
        scene.setdefault("image_query_4", "")
        if not isinstance(scene["image_query_4"], str):
            raise NarrativeValidationError(
                f"screenplay scene {sid} image_query_4 must be a string when supplied"
            )
        scene["bullet_points"] = []
        scene.setdefault("caption_text", "")
        scene.setdefault("on_screen_text", "")
        if not isinstance(scene["caption_text"], str) or not isinstance(scene["on_screen_text"], str):
            raise NarrativeValidationError(
                f"screenplay scene {sid} caption_text and on_screen_text must be strings"
            )
    script["schema_version"] = SCHEMA_VERSION
    script["content_mode"] = CONTENT_MODE
    script.setdefault("characters", list((character_bible or {}).get("characters", [])))
    script.setdefault("tags", [])
    script["estimated_duration_seconds"] = int(
        len(" ".join(scene["narration"] for scene in sections).split())
        / max(float(script.get("planned_words_per_minute", 125)), 1) * 60
    )
    return script


def deterministic_review(script: dict, target_duration_minutes: float,
                         outline: dict | None = None,
                         repair_attempts: list | None = None) -> dict:
    """Measure objective readiness; editorial opinion can never erase these blockers."""
    script = normalize_script(script, outline)
    sections = script["sections"]
    words = sum(len(scene["narration"].split()) for scene in sections)
    expected = max(1, int(round(
        target_duration_minutes * script.get("planned_words_per_minute", 125)
    )))
    is_shorts = script.get("video_type") == "shorts"
    total_min, total_max = ((.75, 1.30) if is_shorts else (.90, 1.10))
    blocking_min, blocking_max = ((.60, 1.50) if is_shorts else (.85, 1.20))
    scene_min, scene_max = ((.70, 1.35) if is_shorts else (.85, 1.15))
    total_ratio = words / expected
    warnings = []
    blocking_issues = []
    major_issues = []
    proposed_corrections = []

    plan_by_id = {}
    if isinstance(outline, dict):
        for plan in normalize_outline(outline).get("scenes", []):
            plan_by_id[plan.get("scene_id")] = plan
            plan_by_id[plan.get("order")] = plan
    declared = [scene.get("target_words") for scene in sections]
    use_declared = all(isinstance(value, int) and value > 0 for value in declared)
    fallback_targets = []
    if not use_declared:
        base, remainder = divmod(expected, len(sections))
        fallback_targets = [base + (1 if i < remainder else 0) for i in range(len(sections))]
    scene_budgets = []
    for index, scene in enumerate(sections):
        plan = plan_by_id.get(scene.get("outline_scene_id")) or plan_by_id.get(scene.get("id")) or {}
        target = plan.get("target_words") or (
            scene.get("target_words") if use_declared else fallback_targets[index]
        )
        actual = len(scene["narration"].split())
        ratio = actual / max(target, 1)
        deficit = target - actual
        status = "within_target"
        if ratio < scene_min:
            status = "under_target"
        elif ratio > scene_max:
            status = "over_target"
        scene_budgets.append({
            "scene_id": scene.get("id"),
            "outline_scene_id": scene.get("outline_scene_id"),
            "title": scene.get("title", ""),
            "section_type": scene.get("section_type"),
            "target_words": target,
            "actual_words": actual,
            "ratio": round(ratio, 4),
            "deviation_percent": round((ratio - 1) * 100, 1),
            "word_deficit": deficit,
            "status": status,
        })
        if status != "within_target":
            direction = "below" if deficit > 0 else "above"
            message = (
                f"Scene {scene.get('id')} '{scene.get('title', '')}' is {abs(deficit)} words "
                f"{direction} its {target}-word target ({actual}/{target}; {ratio:.1%})."
            )
            major_issues.append({
                "severity": "major", "issue_type": "scene_word_budget",
                "scene_ids": [scene.get("id")], "claim": message,
                "evidence": [f"actual={actual}", f"target={target}", f"ratio={ratio:.4f}"],
                "recommendation": "Revise this scene with substantive dramatic action; do not pad it.",
            })
            proposed_corrections.append({
                "scene_id": scene.get("id"), "action": "expand" if deficit > 0 else "tighten",
                "word_delta": abs(deficit), "preserve": ["identity", "causality", "continuity"],
            })

    if not total_min <= total_ratio <= total_max:
        direction = "below" if total_ratio < total_min else "above"
        issue = {
            "severity": "blocking" if not blocking_min <= total_ratio <= blocking_max else "major",
            "issue_type": "total_word_budget", "scene_ids": [],
            "claim": (
                f"Narration is {direction} the accepted total range: {words}/{expected} words "
                f"({total_ratio:.1%}; accepted {total_min:.0%}-{total_max:.0%})."
            ),
            "evidence": [f"actual={words}", f"target={expected}", f"ratio={total_ratio:.4f}"],
            "recommendation": "Correct only deficient or overlong scenes using the scene budget report.",
        }
        (blocking_issues if issue["severity"] == "blocking" else major_issues).append(issue)
        warnings.append(issue["claim"])
    prohibited = (
        "what you'll learn", "key takeaways", "but what happened next will shock you",
        "little did he know", "subscribe and hit the bell", "throughout human history",
    )
    joined = " ".join(scene["narration"].lower() for scene in sections)
    found = [phrase for phrase in prohibited if phrase in joined]
    if found:
        warnings.append("Formulaic or educational phrasing detected: " + ", ".join(found))
    if any(scene.get("bullet_points") for scene in sections):
        warnings.append("Narrative scenes must not contain educational bullet overlays.")
    first_function = sections[0].get("section_type")
    last_function = sections[-1].get("section_type")
    complete_shape = (
        first_function in ("opening", "setup")
        and last_function in ("resolution", "aftermath")
        and any(scene.get("section_type") == "climax" for scene in sections)
    )
    if not complete_shape:
        issue = {
            "severity": "blocking", "issue_type": "story_shape", "scene_ids": [],
            "claim": "The screenplay lacks a canonical opening, climax, or resolution/aftermath.",
            "evidence": [f"section_types={[scene.get('section_type') for scene in sections]}"],
            "recommendation": "Restore the missing dramatic function without replacing valid scenes.",
        }
        blocking_issues.append(issue)
        warnings.append(issue["claim"])
    asset_search = all(scene.get("image_query") and scene.get("visual_direction") for scene in sections)
    if not asset_search:
        warnings.append("One or more scenes lack asset-search metadata.")
    style_watchlist = ("shadow", "darkness", "glass", "polished", "luxury", "marble", "silence")
    repeated_terms = {}
    for term in style_watchlist:
        count = len(re.findall(rf"\b{re.escape(term)}\w*\b", joined))
        if count >= max(4, len(sections)):
            repeated_terms[term] = count
    if repeated_terms:
        warnings.append("Possible motif overuse: " + ", ".join(
            f"{term} ({count})" for term, count in repeated_terms.items()
        ))

    status = "human_review_required"
    if not complete_shape:
        status = "structurally_invalid"
    elif blocking_issues or major_issues:
        status = "revision_required"
    return {
        "schema_version": SCHEMA_VERSION,
        "narrative_contract_version": NARRATIVE_CONTRACT_VERSION,
        "content_mode": CONTENT_MODE,
        "status": status,
        "word_count": words,
        "target_word_count": expected,
        "word_count_ratio": round(total_ratio, 4),
        "deviation_percent": round((total_ratio - 1) * 100, 1),
        "estimated_narration_minutes": round(words / max(script.get("planned_words_per_minute", 125), 1), 2),
        "accepted_total_ratio": {"minimum": total_min, "maximum": total_max},
        "blocking_total_ratio": {"minimum": blocking_min, "maximum": blocking_max},
        "accepted_scene_ratio": {"minimum": scene_min, "maximum": scene_max},
        "scene_count": len(sections),
        "scene_budgets": scene_budgets,
        "blocking_issues": blocking_issues,
        "major_issues": major_issues,
        "proposed_corrections": proposed_corrections,
        "continuity_findings": [],
        "dramaturgical_findings": [],
        "repair_attempts": list(repair_attempts or []),
        "manual_review_scene_ids": sorted({
            scene_id for issue in blocking_issues + major_issues
            for scene_id in issue.get("scene_ids", []) if scene_id is not None
        }),
        "deterministic_checks": {
            "unique_scene_ids": True,
            "nonempty_narration": True,
            "asset_search_metadata": asset_search,
            "character_references": True,
            "complete_story_shape": complete_shape,
            "total_word_budget": total_min <= total_ratio <= total_max,
            "scene_word_budgets": all(item["status"] == "within_target" for item in scene_budgets),
        },
        "warnings": warnings,
        "editorial_assessment": None,
        "editorial_validation_complete": False,
        "human_review_required": True,
        "legal_notice": "Editorial scores are heuristics, not predictions of audience performance.",
    }


def validate_editorial_assessment(value: dict) -> dict:
    _require_mapping(value, "editorial assessment")
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(EDITORIAL_DIMENSIONS):
        raise NarrativeValidationError(
            "editorial assessment requires exactly the 15 named dimensions"
        )
    for name, rating in dimensions.items():
        if not isinstance(rating, dict) or not isinstance(rating.get("score"), (int, float)):
            raise NarrativeValidationError(f"editorial dimension {name} requires numeric score")
        if not 1 <= float(rating["score"]) <= 10:
            raise NarrativeValidationError(f"editorial dimension {name} score must be 1-10")
        evidence = rating.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(
                not isinstance(item, str) or not item.strip() for item in evidence):
            raise NarrativeValidationError(f"editorial dimension {name} requires evidence")
    findings = value.get("findings")
    if not isinstance(findings, list):
        raise NarrativeValidationError("editorial assessment requires findings array")
    required = ("severity", "issue_type", "scene_ids", "claim", "evidence",
                "why_it_matters", "recommendation", "certainty")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or any(field not in finding for field in required):
            raise NarrativeValidationError(f"editorial finding {index + 1} is incomplete")
        if finding["severity"] not in ("blocking", "major", "minor", "suggestion"):
            raise NarrativeValidationError(f"editorial finding {index + 1} has invalid severity")
        if not isinstance(finding["scene_ids"], list) or not isinstance(finding["evidence"], list):
            raise NarrativeValidationError(f"editorial finding {index + 1} needs scene_ids/evidence arrays")
        if finding.get("certainty") == "confirmed" and (
                not finding["scene_ids"] or not finding["evidence"]):
            raise NarrativeValidationError(
                f"confirmed editorial finding {index + 1} requires scene references and evidence"
            )
    return value


def merge_editorial_review(deterministic: dict, assessment: dict | None) -> dict:
    merged = deepcopy(deterministic)
    if assessment is None:
        merged["status"] = (
            merged["status"] if merged["status"] in ("structurally_invalid", "revision_required")
            else "human_review_required"
        )
        merged["editorial_validation_complete"] = False
        return merged
    assessment = validate_editorial_assessment(assessment)
    merged["editorial_assessment"] = assessment
    merged["editorial_validation_complete"] = True
    findings = assessment["findings"]
    merged["continuity_findings"] = [
        item for item in findings if "continu" in item["issue_type"].lower()
    ]
    merged["dramaturgical_findings"] = [
        item for item in findings if item["issue_type"].lower() in {
            "agency", "dramaturgy", "causality", "reversal", "climax", "passivity"
        }
    ]
    semantic_blockers = [item for item in findings if item["severity"] == "blocking"]
    semantic_majors = [item for item in findings if item["severity"] == "major"]
    merged["blocking_issues"] += semantic_blockers
    merged["major_issues"] += semantic_majors
    merged["manual_review_scene_ids"] = sorted({
        scene_id for item in findings for scene_id in item.get("scene_ids", [])
        if isinstance(scene_id, int)
    } | set(merged.get("manual_review_scene_ids", [])))
    if merged["status"] == "structurally_invalid":
        pass
    elif merged["blocking_issues"] or merged["major_issues"]:
        merged["status"] = "revision_required"
    else:
        merged["status"] = "ready_for_human_review"
    merged["human_review_required"] = True
    return merged


def invalidate_downstream(stage: str, output_dir: str) -> list[str]:
    """Remove only derived artifacts made incompatible by an upstream change."""
    groups = {
        "research": (
            "research.json", "story_brief.json", "character_bible.json",
            "story_outline.json", "script.json", "narrative_review.json",
            "narration.mp3", "narration_mixed.mp3", "narration.srt",
            "scene_timeline.json", "asset_manifest.json", "thumbnail.jpg",
            "final_video.mp4", "final_short.mp4",
        ),
        "script": (
            "script.json", "narrative_review.json", "narration.mp3",
            "narration_mixed.mp3", "narration.srt", "scene_timeline.json",
            "asset_manifest.json", "thumbnail.jpg", "final_video.mp4", "final_short.mp4",
        ),
        "story_brief": (
            "character_bible.json", "story_outline.json", "script.json",
            "narrative_review.json", "narration.mp3", "narration_mixed.mp3",
            "narration.srt", "scene_timeline.json", "asset_manifest.json",
            "thumbnail.jpg", "final_video.mp4", "final_short.mp4",
        ),
        "script_changed": (
            "narration.mp3", "narration_mixed.mp3", "narration.srt",
            "scene_timeline.json", "asset_manifest.json", "thumbnail.jpg",
            "final_video.mp4", "final_short.mp4",
        ),
        "narration": ("narration.mp3", "narration_mixed.mp3", "narration.srt",
                      "scene_timeline.json", "final_video.mp4", "final_short.mp4"),
        "images": ("asset_manifest.json", "thumbnail.jpg", "final_video.mp4", "final_short.mp4"),
    }
    removed = []
    root = Path(output_dir)
    for name in groups.get(stage, ()):
        path = root / name
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    if stage in {"research", "script", "story_brief", "script_changed", "images"}:
        images = root / "images"
        if images.is_dir():
            for path in images.glob("section_*_img*.jpg"):
                path.unlink()
                removed.append(str(path))
    return removed


def invalidate_scene_outputs(scene_id: int, output_dir: str) -> list[str]:
    """Invalidate derived media after one screenplay scene is regenerated."""
    root = Path(output_dir)
    removed = []
    for name in (
        "narration.mp3", "narration_mixed.mp3", "narration.srt",
        "scene_timeline.json", "asset_manifest.json", "thumbnail.jpg",
        "final_video.mp4", "final_short.mp4",
    ):
        path = root / name
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    images = root / "images"
    if images.is_dir():
        for path in images.glob(f"section_{int(scene_id):02d}_img*.jpg"):
            path.unlink()
            removed.append(str(path))
    return removed


def artifact_matches(path, source_hash: str, validator, *validator_args):
    value = load_json(path)
    if not value or value.get("source_hash", value.get("source_story_hash")) != source_hash:
        return None
    try:
        return validator(value, *validator_args)
    except NarrativeValidationError:
        return None


def _fictional_description(brief: dict) -> str:
    return (
        f"An original fictional story about {brief['psychological_theme'].lower()}. "
        f"{brief['logline']} This episode is a work of fiction."
    )


def _default_tags(brief: dict) -> list[str]:
    theme_words = re.findall(r"[A-Za-z]+", brief["psychological_theme"])
    return ["Private Gravity", "Cinematic Fiction", "Psychological Fiction", *theme_words[:3]]


def _require_mapping(value, label: str):
    if not isinstance(value, dict):
        raise NarrativeValidationError(f"{label} must be an object")


def _require_text_fields(value: dict, fields, label: str):
    missing = [field for field in fields if not isinstance(value.get(field), str) or not value[field].strip()]
    if missing:
        raise NarrativeValidationError(f"{label} missing nonempty fields: {', '.join(missing)}")


_MISSING = object()


def _trim_strings(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_trim_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _trim_strings(item) for key, item in value.items()}
    return value


def _received_type(value) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, list):
        if not value:
            return "empty list"
        element_types = sorted({"null" if item is None else type(item).__name__ for item in value})
        return f"list[{', '.join(element_types)}]"
    return type(value).__name__


def _validation_error(artifact: str, path: str, expected: str, received, problem: str):
    return NarrativeValidationError(
        f"{artifact} validation failed:\n"
        f"{path}\n"
        f"Expected: {expected}\n"
        f"Received: {_received_type(received)}\n"
        f"Problem: {problem}"
    )


def _require_character_text(character: dict, index: int, field: str) -> None:
    path = f"character[{index}].{field}"
    if field not in character:
        raise _validation_error(
            "Character Bible", path, "nonempty string", _MISSING, "missing field"
        )
    value = character[field]
    expected = (
        "nonempty string or nonempty array[string]"
        if field == "behavioral_patterns" else "nonempty string"
    )
    if not isinstance(value, str):
        if field == "behavioral_patterns" and isinstance(value, list):
            problem = ("empty required field" if not value
                       else "invalid array element type or empty element")
        else:
            problem = "incorrect field type"
        raise _validation_error(
            "Character Bible", path, expected, value, problem
        )
    if not value.strip():
        raise _validation_error(
            "Character Bible", path, expected, value, "empty required field"
        )


def _require_string_array(character: dict, index: int, field: str,
                          allow_empty: bool) -> None:
    path = f"character[{index}].{field}"
    if field not in character:
        raise _validation_error(
            "Character Bible", path, "array[string]", _MISSING, "missing field"
        )
    value = character[field]
    if not isinstance(value, list):
        raise _validation_error(
            "Character Bible", path, "array[string]", value, "incorrect field type"
        )
    if not allow_empty and not value:
        raise _validation_error(
            "Character Bible", path, "nonempty array[string]", value, "empty required field"
        )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise _validation_error(
            "Character Bible", path, "array of nonempty strings", value,
            "invalid array element type or empty element",
        )
