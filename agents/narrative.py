"""Schemas, validation and artifact helpers for Private Gravity stories."""
from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path


SCHEMA_VERSION = 2
NARRATIVE_CONTRACT_VERSION = 4
# Contract v3 introduced canonical section_type; v4 adds optional arc_movements.
CANONICAL_TYPE_CONTRACT = 3
CONTENT_MODE = "cinematic_fiction"
SCENE_TYPES = (
    "opening", "setup", "development", "reversal", "climax", "resolution", "aftermath",
)
LEGACY_EDITORIAL_DIMENSIONS = (
    "structural_coherence", "causal_continuity", "chronological_continuity",
    "character_consistency", "character_agency", "dialogue_and_subtext",
    "dramatic_progression", "emotional_escalation", "reversal_quality",
    "climax_effectiveness", "ending_resonance", "originality",
    "literary_style", "repetition_control", "visual_feasibility",
)
IDENTITY_EDITORIAL_DIMENSIONS = (
    "channel_identity_adherence", "hook_strength", "title_curiosity",
    "philosophical_integration", "ending_openness", "strategy_success_then_cost",
)
EDITORIAL_DIMENSIONS = LEGACY_EDITORIAL_DIMENSIONS + IDENTITY_EDITORIAL_DIMENSIONS
# Dimensions that may be marked not applicable for non-Type-A stories.
TYPE_A_ONLY_DIMENSIONS = ("strategy_success_then_cost",)
TYPE_A = "realistic_psychological_fiction"
ARC_MOVEMENTS = (
    "hook", "character_wound", "mechanism", "escalation_success",
    "hidden_cost", "reversal", "philosophical_layer", "ending",
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


_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"ya29\.[0-9A-Za-z_\-.]+"),
    re.compile(r"(?i)\b((?:api[_-]?key|(?:access[_-]?|refresh[_-]?|id[_-]?)?token"
               r"|client[_-]?secret|secret|password|authorization)"
               r"[\"']?\s*[=:]\s*[\"']?)[^\s\"'&,}]+"),
    re.compile(r"(?i)([?&](?:key|api_key|token)=)[^&\s]+"),
)


def redact_secrets(text: str, secrets=()) -> str:
    """Remove configured secrets and common key/token shapes from free text."""
    result = str(text or "")
    for secret in secrets:
        if secret and len(str(secret)) >= 6:
            result = result.replace(str(secret), "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(
            lambda match: (match.group(1) if match.groups() else "") + "[REDACTED]", result
        )
    return result


def write_debug_artifact(run_dir, stage: str, sections: dict, secrets=()) -> Path | None:
    """Persist raw stage diagnostics to ``<run_dir>/debug/<stage>-<attempt>.txt``.

    Never raises: diagnostics must not mask the original failure.
    """
    if not run_dir:
        return None
    try:
        folder = Path(run_dir) / "debug"
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", str(stage).lower()).strip("-") or "stage"
        attempt = 1
        while (folder / f"{slug}-{attempt}.txt").exists():
            attempt += 1
        body = "\n\n".join(
            f"===== {title} =====\n{redact_secrets(value, secrets)}"
            for title, value in sections.items()
        )
        path = folder / f"{slug}-{attempt}.txt"
        path.write_text(body + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


MOJIBAKE_MARKERS = ("â€", "Ã©", "Ã", "â€™", "â€œ", "â€”")
# A UTF-8 lead byte decoded as cp1252 followed by 1-3 continuation bytes
# decoded the same way ("â€™" for "’", "Ã©" for "é").
_CP1252_CONTINUATION = (
    "\u0080-¿ŒœŠšŸŽžƒˆ˜"
    "–—‘’‚“”„†‡•…"
    "‰‹›€™"
)
_MOJIBAKE_RUN = re.compile(f"[Â-ô][{_CP1252_CONTINUATION}]{{1,3}}")


class MojibakeError(ValueError):
    """Narration contains UTF-8 text that was decoded with the wrong codec."""


def has_mojibake(text) -> bool:
    return any(marker in str(text or "") for marker in MOJIBAKE_MARKERS)


def repair_mojibake(text: str) -> str:
    """Undo cp1252-mis-decoded UTF-8 run by run; leave anything else untouched."""
    def to_byte(char: str) -> bytes:
        # Bytes undefined in cp1252 (0x81, 0x8D, 0x8F, 0x90, 0x9D) survive
        # Windows decoding as the same-numbered control characters.
        return bytes([ord(char)]) if ord(char) < 256 else char.encode("cp1252")

    def fix(match):
        try:
            return b"".join(to_byte(char) for char in match.group()).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return match.group()
    return _MOJIBAKE_RUN.sub(fix, str(text))


def assert_no_mojibake(script: dict, stage: str = "narration") -> None:
    """Stop before TTS or caption rendering when narration text is mis-decoded."""
    bad = []
    for scene in script.get("sections", []):
        for field in ("narration", "caption_text", "on_screen_text", "title"):
            value = scene.get(field)
            if has_mojibake(value):
                marker = next(m for m in MOJIBAKE_MARKERS if m in value)
                at = value.index(marker)
                bad.append(f"scene {scene.get('id')} {field}: ...{value[max(0, at - 20):at + 12]}...")
                break
    if bad:
        raise MojibakeError(
            f"Mis-decoded text (mojibake) found before {stage}; it would be spoken and captioned "
            "incorrectly. " + "; ".join(bad)
            + ". Repair with: python -m scripts.repair_text_encoding (dry run first)."
        )


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
    current_contract = normalized.get("narrative_contract_version", 0) >= CANONICAL_TYPE_CONTRACT
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
    current_contract = normalized.get("narrative_contract_version", 0) >= CANONICAL_TYPE_CONTRACT
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


OUTLINE_SCENE_TEXT_FIELDS = (
    "scene_id", "title", "section_type", "narrative_function", "summary", "setting",
    "dramatic_function", "emotional_turn", "causal_link", "visual_direction",
)
OUTLINE_DRAMATURGY_FIELDS = (
    "initial_dramatic_situation", "character_objective", "obstacle",
    "dramatic_action", "decision", "consequence", "new_information",
    "chronology_marker",
)


def outline_scene_issues(outline: dict, bible: dict,
                         require_dramaturgy: bool = False) -> dict[str, list[str]]:
    """Collect per-scene problems keyed by scene_id (``#<position>`` when absent).

    Only problems local to one scene are reported here; whole-outline checks
    (scene order, opening/ending/climax) stay in ``validate_outline``.
    """
    scenes = normalize_outline(outline).get("scenes") if isinstance(outline, dict) else None
    if not isinstance(scenes, list):
        return {}
    character_ids = {character.get("character_id") for character in bible.get("characters", [])
                     if isinstance(character, dict)}
    issues: dict[str, list[str]] = {}
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            issues[f"#{index}"] = ["scene must be an object"]
            continue
        sid = scene.get("scene_id")
        key = sid.strip() if isinstance(sid, str) and sid.strip() else f"#{index}"
        problems = []
        fields = OUTLINE_SCENE_TEXT_FIELDS + (OUTLINE_DRAMATURGY_FIELDS if require_dramaturgy else ())
        missing = [field for field in fields
                   if not isinstance(scene.get(field), str) or not scene[field].strip()]
        if missing:
            problems.append(f"missing nonempty fields: {', '.join(missing)}")
        if "section_type" not in missing and scene["section_type"] not in SCENE_TYPES:
            problems.append(f"section_type '{scene['section_type']}' must be one of {', '.join(SCENE_TYPES)}")
        if require_dramaturgy:
            updates = scene.get("continuity_updates")
            if (not isinstance(updates, list) or
                    any(not isinstance(item, str) or not item.strip() for item in updates)):
                problems.append("continuity_updates must be an array of nonempty strings")
        present = scene.get("characters_present", [])
        if not isinstance(present, list) or any(not isinstance(cid, str) for cid in present):
            problems.append("characters_present must be an array of character_id strings")
        else:
            unknown = [cid for cid in present if cid not in character_ids]
            if unknown:
                problems.append(f"characters_present has unknown character IDs: {', '.join(unknown)}")
        queries = scene.get("image_queries", [])
        if (not isinstance(queries, list) or len(queries) < 3
                or any(not isinstance(query, str) or not query.strip() for query in queries)):
            problems.append("image_queries requires at least three nonempty strings")
        target_words = scene.get("target_words", 0)
        if not isinstance(target_words, int) or target_words < 20:
            problems.append("target_words must be an integer >= 20")
        if "arc_movements" in scene:
            movements = scene["arc_movements"]
            if not isinstance(movements, list) or any(
                    not isinstance(item, str) for item in movements):
                problems.append("arc_movements must be an array of strings")
            else:
                unknown = [item for item in movements if item not in ARC_MOVEMENTS]
                if unknown:
                    problems.append(f"arc_movements has unknown values: {', '.join(unknown)}; "
                                    f"allowed: {', '.join(ARC_MOVEMENTS)}")
        if problems:
            issues.setdefault(key, []).extend(problems)
    return issues


def format_scene_issues(label: str, issues: dict[str, list[str]]) -> str:
    details = "; ".join(f"scene {sid}: {', '.join(problems)}" for sid, problems in issues.items())
    return f"{label} has invalid scenes. {details}"


def validate_outline(outline: dict, bible: dict, require_dramaturgy: bool = False) -> dict:
    outline = normalize_outline(outline)
    _require_mapping(outline, "story outline")
    scenes = outline.get("scenes")
    if not isinstance(scenes, list) or len(scenes) < 3:
        raise NarrativeValidationError("story outline requires at least three scenes")
    issues = outline_scene_issues(outline, bible, require_dramaturgy)
    if issues:
        raise NarrativeValidationError(format_scene_issues("story outline", issues))
    seen = set()
    previous_order = 0
    for scene in scenes:
        sid = scene["scene_id"]
        if sid in seen:
            raise NarrativeValidationError(f"Duplicate outline scene_id: {sid}")
        seen.add(sid)
        order = scene.get("order")
        if not isinstance(order, int) or order <= previous_order:
            raise NarrativeValidationError(
                f"outline scene {sid}: order must be an integer greater than the previous "
                f"scene's ({previous_order}); received {order!r}"
            )
        previous_order = order
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


STYLE_INTENSIFIERS = (
    "utterly", "unmitigated", "unadulterated", "absolute", "sheer", "devastating", "profound",
    "immaculate", "impeccable",
)
_INTENSIFYING_ADVERBS = {
    "utterly", "profoundly", "deeply", "impossibly", "unbearably", "achingly", "perfectly",
    "completely", "absolutely", "entirely", "terribly", "infinitely", "unspeakably",
}
# A stacked pair must follow a determiner or preposition ("with pure, unmitigated
# estrangement"); this keeps clauses such as "She turned, nodding slowly" out.
_MODIFIER_LEADS = {
    "a", "an", "the", "his", "her", "their", "its", "my", "your", "our", "with", "of", "in",
    "into", "to", "from", "by", "for", "at", "on", "such", "this", "that", "some", "no", "and",
    "was", "is", "were", "only", "but",
}
_COMMON_ADJECTIVES = {
    "pure", "cold", "quiet", "soft", "dark", "bright", "deep", "sharp", "raw", "bare", "thin",
    "heavy", "faint", "warm", "calm", "still", "sweet", "bitter", "harsh", "gentle", "cruel",
    "empty", "hollow", "slow", "steady", "perfect", "total", "complete", "utter", "vast",
    "grim", "stark", "pale", "clean", "smooth", "tight", "brittle", "dull", "fierce",
}
_NOT_NOUNS = {
    "and", "or", "but", "as", "to", "the", "a", "an", "of", "in", "on", "with", "that", "than",
    "then", "he", "she", "they", "it", "his", "her", "their", "was", "is", "were",
}
_ADJECTIVE_SUFFIX = re.compile(r"(ful|less|ous|ive|al|ed|ing|ic|ent|ant|able|ible|ish|ary)$")
# Lookaheads so overlapping candidates ("with sorrow, but with pure, ...") are all seen.
_STACKED_PAIR = re.compile(r"(?=\b([a-z']+)\s+([a-z'-]+),\s+([a-z'-]+)\s+([a-z'-]+)\b)")
_ADVERB_STACK = re.compile(r"(?=\b([a-z]+ly)\s+([a-z'-]+)\s+([a-z'-]+)\b)")
_INTENSIFIER = re.compile(r"\b(" + "|".join(STYLE_INTENSIFIERS) + r")\b")


def _adjective_like(word: str) -> bool:
    return (word in _COMMON_ADJECTIVES or word in STYLE_INTENSIFIERS
            or (len(word) > 4 and not word.endswith("ly") and bool(_ADJECTIVE_SUFFIX.search(word))))


def _noun_like(word: str) -> bool:
    return word not in _NOT_NOUNS and not word.endswith("ly")


def stacked_modifier_lint(sections: list[dict], threshold: int | None = None) -> dict:
    """Count stacked modifiers and intensifiers per scene (warning-level only)."""
    if threshold is None:
        try:
            import config
            threshold = int(getattr(config, "NARRATIVE_STYLE_LINT_THRESHOLD", 3))
        except ImportError:  # pragma: no cover
            threshold = 3
    per_scene, examples, total = {}, [], 0
    for scene in sections:
        text = str(scene.get("narration", "")).lower()
        spans, found = [], []
        for match in _STACKED_PAIR.finditer(text):
            lead, first, second, noun = match.groups()
            if (lead in _MODIFIER_LEADS and _adjective_like(first) and _adjective_like(second)
                    and _noun_like(noun)):
                spans.append(match.span(2))
                found.append(f"{first}, {second} {noun}")
        for match in _ADVERB_STACK.finditer(text):
            adverb, adjective, noun = match.groups()
            if adverb in _INTENSIFYING_ADVERBS and _adjective_like(adjective) and _noun_like(noun):
                spans.append(match.span(1))
                found.append(f"{adverb} {adjective} {noun}")
        stacked = len(found)
        intensifiers = 0
        for match in _INTENSIFIER.finditer(text):
            covered = any(start <= match.start() < end + 40 for start, end in spans)
            if not covered:
                intensifiers += 1
                found.append(match.group(1))
        count = stacked + intensifiers
        total += count
        examples += [f"scene {scene.get('id')}: {item}" for item in found]
        per_scene[str(scene.get("id"))] = {
            "stacked_modifiers": stacked, "intensifiers": intensifiers, "total": count,
            "examples": found[:5],
        }
    return {"total": total, "threshold": threshold, "per_scene": per_scene,
            "examples": examples[:10]}


# Over-target scenes become a major issue only past this ratio (or when the total is out of range).
OVER_BUDGET_MAJOR_RATIO = 1.30


def missing_arc_movements(outline: dict | None, narrative_type: str | None) -> list[str]:
    """Type A movements no scene declares; empty for B/C or outlines predating the field."""
    if narrative_type != TYPE_A or not isinstance(outline, dict):
        return []
    scenes = [scene for scene in outline.get("scenes") or [] if isinstance(scene, dict)]
    if not any("arc_movements" in scene for scene in scenes):
        return []
    declared = {item for scene in scenes for item in scene.get("arc_movements") or []
                if isinstance(item, str)}
    return [movement for movement in ARC_MOVEMENTS if movement not in declared]


def deterministic_review(script: dict, target_duration_minutes: float,
                         outline: dict | None = None,
                         repair_attempts: list | None = None,
                         brief: dict | None = None) -> dict:
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
    minor_issues = []
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
            # Under-development always matters. A slightly long scene inside a total
            # that is within range is a pacing note, not a revision trigger.
            proportionate = (deficit < 0 and not is_shorts and ratio <= OVER_BUDGET_MAJOR_RATIO
                             and total_min <= total_ratio <= total_max)
            issue = {
                "severity": "minor" if proportionate else "major", "issue_type": "scene_word_budget",
                "scene_ids": [scene.get("id")], "claim": message,
                "evidence": [f"actual={actual}", f"target={target}", f"ratio={ratio:.4f}"],
                "recommendation": ("Consider tightening this scene if it drags when read aloud."
                                   if proportionate else
                                   "Revise this scene with substantive dramatic action; do not pad it."),
            }
            (minor_issues if proportionate else major_issues).append(issue)
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
    style_lint = stacked_modifier_lint(sections)
    if style_lint["total"] > style_lint["threshold"]:
        issue = {
            "severity": "minor", "issue_type": "style",
            "scene_ids": [int(sid) for sid, item in style_lint["per_scene"].items() if item["total"]],
            "claim": (f"{style_lint['total']} stacked modifiers or intensifiers "
                      f"(threshold {style_lint['threshold']}) read badly aloud."),
            "evidence": style_lint["examples"],
            "recommendation": "Keep one precise word or replace the emphasis with a concrete action.",
        }
        minor_issues.append(issue)
        warnings.append(issue["claim"])

    narrative_type = script.get("narrative_type")
    missing_movements = missing_arc_movements(outline, narrative_type)
    if missing_movements:
        major_issues.append({
            "severity": "major", "issue_type": "arc_movements", "scene_ids": [],
            "claim": ("The Type A outline declares no scene for these movements: "
                      + ", ".join(missing_movements) + "."),
            "evidence": [f"missing={missing_movements}"],
            "recommendation": "Express the missing movements or confirm the deliberate compression.",
        })
    title_brief = brief or {"psychological_theme": script.get("psychological_theme", "")}
    title_findings = validate_title(script.get("title", ""), title_brief)
    for finding in title_findings:
        (blocking_issues if finding["severity"] == "blocking" else major_issues).append(finding)

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
        "narrative_type": narrative_type,
        "title_review": {
            "title": script.get("title", ""),
            "passes": title_passes(title_findings),
            "findings": title_findings,
        },
        "missing_arc_movements": missing_movements,
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
        "minor_issues": minor_issues,
        "style_lint": style_lint,
        "warnings": warnings,
        "editorial_assessment": None,
        "editorial_validation_complete": False,
        "human_review_required": True,
        "legal_notice": "Editorial scores are heuristics, not predictions of audience performance.",
    }


_THESIS_PATTERNS = (
    ("psychology of", r"\bpsychology of\b"), ("science of", r"\bscience of\b"),
    ("philosophy of", r"\bphilosophy of\b"), ("a story about", r"\ba story about\b"),
    ("explained", r"\bexplained\b"), ("the truth about", r"\bthe truth about\b"),
    ("why", r"\bwhy\b"), ("how", r"\bhow\b"), ("lesson", r"\blessons?\b"),
    ("what happens when", r"\bwhat happens when\b"),
)
_PERSON_WORDS = {
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his", "she", "her", "hers",
    "they", "them", "their", "man", "men", "woman", "women", "boy", "girl", "child", "kid",
    "father", "mother", "dad", "mom", "son", "daughter", "wife", "husband", "brother", "sister",
    "friend", "friends", "boss", "stranger", "neighbor", "neighbour", "doctor", "teacher",
    "widow", "widower", "family", "everyone", "nobody", "someone", "people", "partner",
    "lover", "twin", "grandfather", "grandmother", "colleague", "rival", "employee", "artist",
}
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand",
}
_TIME_WORDS = {
    "year", "years", "month", "months", "week", "weeks", "day", "days", "night", "nights",
    "hour", "hours", "minute", "minutes", "decade", "decades", "age", "birthday", "lifetime",
}
_THEME_STOPWORDS = {
    "and", "or", "of", "the", "a", "an", "to", "with", "in", "on", "for", "vs", "versus",
    "its", "their", "being", "existence", "human", "fear", "need",
}
_SUFFIXES = ("iousness", "iously", "ious", "ness", "ment", "ation", "ions", "ion", "ies",
             "ied", "ying", "ing", "ful", "ed", "es", "s", "y", "e")
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
TITLE_MAX_CHARS = 70
TITLE_MAX_WORDS = 12
THUMBNAIL_MAX_WORDS = 5


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def theme_words(brief: dict | None) -> list[str]:
    theme = str((brief or {}).get("psychological_theme", "") or "")
    return [word for word in _words(theme) if len(word) >= 3 and word not in _THEME_STOPWORDS]


def _matches_theme(word: str, themes: list[str]) -> bool:
    stem = _stem(word)
    for theme in themes:
        other = _stem(theme)
        shorter, longer = sorted((stem, other), key=len)
        if stem == other or (len(shorter) >= 4 and longer.startswith(shorter)):
            return True
    return False


def _strip_shorts(title: str) -> str:
    return re.sub(r"\s*#shorts\s*$", "", str(title or ""), flags=re.I).strip()


def _title_finding(severity: str, rule: str, claim: str, evidence: list[str],
                   recommendation: str) -> dict:
    return {
        "severity": severity, "issue_type": "title_policy", "rule": rule, "scene_ids": [],
        "claim": claim, "evidence": evidence, "recommendation": recommendation,
    }


def _human_anchor(text: str, words: list[str]) -> bool:
    return bool(_QUOTED.search(text)) or any(
        word in _PERSON_WORDS or word in _NUMBER_WORDS or word in _TIME_WORDS
        or word.isdigit() for word in words
    )


def _opens_on_a_person(words: list[str]) -> bool:
    """True when the title is a statement about a concrete person.

    Examples: "He Secretly Wanted...", "The Man Who Needed...", "At 37, She...".
    """
    if not words:
        return False
    if words[0] in _PERSON_WORDS or words[0].isdigit():
        return True
    if words[0] in {"the", "a", "an", "my", "his", "her"} and len(words) > 1 \
            and words[1] in _PERSON_WORDS:
        return True
    return words[0] == "at" and len(words) > 1 and (words[1].isdigit() or words[1] in _NUMBER_WORDS)


def validate_title(title: str, brief: dict | None = None) -> list[dict]:
    """Deterministic title policy: the title must not explain the thesis.

    Blocking findings make the title unpublishable; a major finding warns that
    the title has no concrete human anchor. The ``#Shorts`` suffix is ignored.
    A theme word is tolerated inside a quoted line, or when the whole title is a
    statement about a concrete person ("The Man Who Needed Everyone to Envy Him");
    otherwise naming the theme explains the thesis.
    """
    text = _strip_shorts(title)
    findings = []
    if not text:
        return [_title_finding("blocking", "empty", "The title is empty.", [],
                               "Write a title about a person and a situation.")]
    lowered = text.lower()
    unquoted = _QUOTED.sub(" ", text)
    for name, pattern in _THESIS_PATTERNS:
        if re.search(pattern, _QUOTED.sub(" ", lowered)):
            findings.append(_title_finding(
                "blocking", "thesis_pattern",
                f"The title uses the explainer pattern '{name}'.", [text],
                "Present a person and a situation instead of announcing the theme.",
            ))
    if ":" in unquoted:
        findings.append(_title_finding(
            "blocking", "colon_subtitle", "The title uses a colon subtitle.", [text],
            "Use one curiosity-driven line without a subtitle.",
        ))
    words = _words(text)
    if (re.match(r"^the\s+[a-z][a-z'-]*\s+of\b", lowered)
            and len(words) > 1 and words[1] not in _PERSON_WORDS):
        findings.append(_title_finding(
            "blocking", "abstract_noun_form",
            f"The title uses the abstract form 'The <Noun> of ...' ('{' '.join(words[:3])}').",
            [text], "Name a person and what they did, wanted or lost.",
        ))
    themes = theme_words(brief)
    unquoted_words = _words(unquoted)
    named = sorted({word for word in unquoted_words if _matches_theme(word, themes)})
    if named and not _opens_on_a_person(words):
        findings.append(_title_finding(
            "blocking", "names_theme",
            f"The title names the psychological theme ({', '.join(named)}).",
            [text, f"psychological_theme={(brief or {}).get('psychological_theme', '')}"],
            "Let the audience discover the theme; keep the theme word out of the title.",
        ))
    if len(text) > TITLE_MAX_CHARS or len(text.split()) > TITLE_MAX_WORDS:
        findings.append(_title_finding(
            "blocking", "too_long",
            f"The title has {len(text)} characters and {len(text.split())} words "
            f"(limit {TITLE_MAX_CHARS} characters, {TITLE_MAX_WORDS} words).",
            [text], "Shorten the title.",
        ))
    if not _human_anchor(text, words):
        findings.append(_title_finding(
            "major", "no_human_anchor",
            "The title has no concrete human anchor (person, pronoun, age, number, time span "
            "or quoted line).", [text], "Anchor the title in a specific person or situation.",
        ))
    return findings


def validate_thumbnail_text(text: str, brief: dict | None = None) -> list[dict]:
    value = str(text or "").strip()
    findings = []
    if not value:
        return [_title_finding("blocking", "thumbnail_empty", "Thumbnail text is empty.", [],
                               "Write up to five words, often a line of inner voice.")]
    if len(value.split()) > THUMBNAIL_MAX_WORDS:
        findings.append(_title_finding(
            "blocking", "thumbnail_too_long",
            f"Thumbnail text has {len(value.split())} words (limit {THUMBNAIL_MAX_WORDS}).",
            [value], "Cut it to five words or fewer.",
        ))
    if "#" in value:
        findings.append(_title_finding("blocking", "thumbnail_hashtag",
                                       "Thumbnail text contains a hashtag.", [value],
                                       "Remove hashtags from the thumbnail."))
    named = sorted({word for word in _words(value) if _matches_theme(word, theme_words(brief))})
    if named:
        findings.append(_title_finding(
            "blocking", "thumbnail_names_theme",
            f"Thumbnail text names the psychological theme ({', '.join(named)}).", [value],
            "Use a line of inner voice instead of the theme.",
        ))
    for finding in findings:
        finding["issue_type"] = "thumbnail_text_policy"
    return findings


def title_passes(findings: list[dict]) -> bool:
    return not any(item["severity"] == "blocking" for item in findings)


def select_title(brief: dict) -> dict:
    """Choose the first passing title and thumbnail text; record every result.

    Returns a ``title_review`` record with status ``passed`` or
    ``revision_required``. Candidates come from ``title_candidates``
    (objects with title/rationale, or strings), then ``video_title``.
    """
    candidates = []
    for item in brief.get("title_candidates") or []:
        if isinstance(item, dict) and str(item.get("title", "")).strip():
            candidates.append({"title": str(item["title"]).strip(),
                               "rationale": str(item.get("rationale", "")).strip()})
        elif isinstance(item, str) and item.strip():
            candidates.append({"title": item.strip(), "rationale": ""})
    if brief.get("video_title") and all(
            item["title"] != brief["video_title"] for item in candidates):
        candidates.append({"title": str(brief["video_title"]).strip(), "rationale": ""})
    for item in candidates:
        item["findings"] = validate_title(item["title"], brief)
        item["passes"] = title_passes(item["findings"])
    thumbnails = []
    for text in brief.get("thumbnail_text_candidates") or []:
        if isinstance(text, str) and text.strip():
            findings = validate_thumbnail_text(text, brief)
            thumbnails.append({"text": text.strip(), "findings": findings,
                               "passes": title_passes(findings)})
    chosen = next((item for item in candidates if item["passes"]), None)
    thumb = next((item for item in thumbnails if item["passes"]), None)
    status = "passed" if chosen and thumb else "revision_required"
    selected = chosen or (candidates[0] if candidates else {"title": "", "findings": []})
    return {
        "status": status,
        "selected_title": selected["title"],
        "selected_title_findings": selected["findings"],
        "selected_thumbnail_text": (thumb or (thumbnails[0] if thumbnails else {"text": ""}))["text"],
        "title_candidates": candidates,
        "thumbnail_text_candidates": thumbnails,
    }


def validate_editorial_assessment(value: dict, narrative_type: str | None = None,
                                  allow_legacy: bool = True) -> dict:
    """Validate an editorial assessment.

    New assessments carry all EDITORIAL_DIMENSIONS. Reports written before the
    identity dimensions existed (exactly the 15 legacy keys) remain readable
    when ``allow_legacy``. A Type-A-only dimension may be marked
    ``{"score": null, "not_applicable": true, "evidence": [reason]}`` for other
    narrative types, and must be scored for Type A.
    """
    _require_mapping(value, "editorial assessment")
    dimensions = value.get("dimensions")
    keys = set(dimensions) if isinstance(dimensions, dict) else set()
    legacy = allow_legacy and keys == set(LEGACY_EDITORIAL_DIMENSIONS)
    if keys != set(EDITORIAL_DIMENSIONS) and not legacy:
        missing = [name for name in EDITORIAL_DIMENSIONS if name not in keys]
        extra = sorted(keys - set(EDITORIAL_DIMENSIONS))
        raise NarrativeValidationError(
            f"editorial assessment requires exactly the {len(EDITORIAL_DIMENSIONS)} named "
            f"dimensions; missing: {', '.join(missing) or 'none'}; unexpected: {', '.join(extra) or 'none'}"
        )
    for name, rating in dimensions.items():
        if (isinstance(rating, dict) and rating.get("not_applicable") is True
                and name in TYPE_A_ONLY_DIMENSIONS):
            if narrative_type == TYPE_A:
                raise NarrativeValidationError(
                    f"editorial dimension {name} must be scored for realistic_psychological_fiction"
                )
            if rating.get("score") is not None:
                raise NarrativeValidationError(
                    f"editorial dimension {name} marked not_applicable must have score null"
                )
        elif not isinstance(rating, dict) or not isinstance(rating.get("score"), (int, float)):
            raise NarrativeValidationError(f"editorial dimension {name} requires numeric score")
        elif not 1 <= float(rating["score"]) <= 10:
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


_DURATION_TERMS = re.compile(r"duration|word count|words|runtime|length|minutes|wpm", re.I)
_NUMBER = re.compile(r"\d")
_WORD_COUNT = re.compile(r"(\d[\d,]*)\s+(?:narrated\s+|spoken\s+)?words?\b", re.I)
_SCENE_COUNT = re.compile(r"(\d+)\s+scenes\b", re.I)
_AGE = re.compile(r"\b(?:aged?\s+(\d{1,3})|(\d{1,3})[- ]years?[- ]old)\b", re.I)
# Word counts below this are passage-level ("a 40-word speech") and cannot be
# checked against scene or episode totals.
_MIN_VERIFIABLE_WORD_COUNT = 100


def _finding_text(finding: dict) -> str:
    evidence = finding.get("evidence") or []
    return " ".join([str(finding.get("claim", ""))] + [str(item) for item in evidence])


def _known_word_counts(deterministic: dict) -> set[int]:
    counts = {deterministic.get("word_count"), deterministic.get("target_word_count")}
    for budget in deterministic.get("scene_budgets") or []:
        counts.update({budget.get("actual_words"), budget.get("target_words")})
    return {value for value in counts if isinstance(value, int)}


def _bible_ages(bible: dict | None) -> dict[str, int]:
    ages = {}
    for character in (bible or {}).get("characters") or []:
        match = re.search(r"\d{1,3}", str(character.get("age", "")))
        if match and character.get("name"):
            ages[str(character["name"])] = int(match.group())
    return ages


def screen_editorial_findings(findings: list[dict], deterministic: dict,
                              bible: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Split editorial findings into kept and discarded.

    LLM reviewers cannot count words: duration and word-count verdicts belong
    to the deterministic report (``deterministic_owner``). Remaining numeric
    claims about words, scenes or ages are checked against the artifacts and
    dropped when contradicted (``contradicted_by_artifacts``).
    """
    kept, discarded = [], []
    known = _known_word_counts(deterministic)
    scene_count = deterministic.get("scene_count")
    ages = _bible_ages(bible)
    for finding in findings:
        claim = str(finding.get("claim", ""))
        if (_DURATION_TERMS.search(str(finding.get("issue_type", "")))
                or (_NUMBER.search(claim) and _DURATION_TERMS.search(claim))):
            discarded.append({**finding, "reason": "deterministic_owner", "verified_value": {
                "word_count": deterministic.get("word_count"),
                "target_word_count": deterministic.get("target_word_count"),
                "estimated_narration_minutes": deterministic.get("estimated_narration_minutes"),
            }})
            continue
        text = _finding_text(finding)
        contradiction = None
        words = [int(value.replace(",", "")) for value in _WORD_COUNT.findall(text)]
        words = [value for value in words if value >= _MIN_VERIFIABLE_WORD_COUNT]
        if words and not any(abs(value - count) <= max(2, count * .02)
                              for value in words for count in known):
            contradiction = {"asserted_word_counts": words, "word_count": deterministic.get("word_count")}
        scenes = [int(value) for value in _SCENE_COUNT.findall(text)]
        if contradiction is None and isinstance(scene_count, int) and any(
                value > scene_count for value in scenes):
            contradiction = {"asserted_scene_counts": scenes, "scene_count": scene_count}
        asserted_ages = [int(a or b) for a, b in _AGE.findall(text)]
        named = {name: age for name, age in ages.items()
                 if name in text or name.split()[0] in text}
        if contradiction is None and asserted_ages and named and not set(asserted_ages) & set(named.values()):
            contradiction = {"asserted_ages": asserted_ages, "bible_ages": named}
        if contradiction:
            discarded.append({**finding, "reason": "contradicted_by_artifacts",
                              "verified_value": contradiction})
        else:
            kept.append(finding)
    return kept, discarded


def merge_editorial_review(deterministic: dict, assessment: dict | None,
                           bible: dict | None = None) -> dict:
    merged = deepcopy(deterministic)
    merged.setdefault("discarded_editorial_findings", [])
    if assessment is None:
        merged["status"] = (
            merged["status"] if merged["status"] in ("structurally_invalid", "revision_required")
            else "human_review_required"
        )
        merged["editorial_validation_complete"] = False
        return merged
    assessment = validate_editorial_assessment(assessment, merged.get("narrative_type"))
    merged["editorial_assessment"] = assessment
    merged["editorial_validation_complete"] = True
    findings, discarded = screen_editorial_findings(assessment["findings"], merged, bible)
    merged["discarded_editorial_findings"] = discarded
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
