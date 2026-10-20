"""Structured (JSON) Gemini generation with bounded, non-truncating repairs.

Every stage prompt is split into a ``contract`` (role, schema and rules) and a
``context`` (brief, bible, outline, scenes). Prompts are always contract first,
so a repair can resend the complete contract without slicing anything:

* parse repair    -> full contract + the complete unparseable response;
* semantic repair -> full contract + the complete document as compact JSON;
* oversized repair of a scene list -> only the invalid scenes (plus read-only
  neighbours) are resent, and corrected scenes are merged back by scene_id so
  every valid scene is preserved untouched.

On any failure the raw response and the error are written to
``<run_dir>/debug/<stage>-<attempt>.txt``.
"""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

import config
from agents.gemini_client import GeminiGenerationError
from agents.narrative import NarrativeValidationError, extract_json, write_debug_artifact

DEFAULT_STAGE_TEMPERATURES = {
    "premise": 0.9,
    "character_bible": 0.7,
    "outline": 0.6,
    "scenes": 0.85,
    "expansion": 0.7,
    "editorial_review": 0.2,
    "repair": 0.2,
}
DEFAULT_REPAIR_MAX_CHARS = 120_000
_VALIDATION_ERRORS = (NarrativeValidationError, ValueError, KeyError, TypeError)

# Run directory for debug artifacts; set by the pipeline entry points.
debug_dir: ContextVar[str | None] = ContextVar("narrative_debug_dir", default=None)


@dataclass(frozen=True)
class SceneScope:
    """Describes how to repair a scene list one scene at a time.

    ``issues`` maps a document to ``{scene_key: [problems]}``. A scene key is
    its ``id_key`` value, or ``#<position>`` (1-based) when the id is missing.
    """
    issues: Callable[[dict], dict[str, list[str]]]
    list_key: str = "scenes"
    id_key: str = "scene_id"


def stage_temperature(stage_key: str) -> float:
    temperatures = getattr(config, "GEMINI_STAGE_TEMPERATURES", None) or {}
    return float(temperatures.get(stage_key, DEFAULT_STAGE_TEMPERATURES.get(stage_key, 0.7)))


def repair_budget() -> int:
    return int(getattr(config, "GEMINI_REPAIR_MAX_CHARS", DEFAULT_REPAIR_MAX_CHARS)
               or DEFAULT_REPAIR_MAX_CHARS)


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def compose_prompt(contract: str, context: str = "") -> str:
    contract = contract.strip()
    context = (context or "").strip()
    return f"{contract}\n\n{context}" if context else contract


def generate_json(contract: str, label: str, validator=None, *, generator,
                  context: str = "", stage: str | None = None,
                  temperature_key: str | None = None,
                  scene_scope: SceneScope | None = None,
                  run_dir: str | None = None) -> dict:
    """Generate one JSON object, allowing exactly one bounded repair."""
    job = _Job(generator, label, stage or _slug(label), temperature_key,
               run_dir if run_dir is not None else debug_dir.get())
    raw = job.call(compose_prompt(contract, context), job.stage, temperature_key)
    try:
        value = extract_json(raw, label)
    except NarrativeValidationError as initial_error:
        job.dump("parse-failure", initial_error, raw)
        repair = _parse_repair_prompt(contract, context, label, initial_error, raw)
        if repair is None:
            # The unparseable text is too large to resend whole, and there is
            # no document to repair scene by scene: regenerate once instead.
            print(f"   WARNING: {label} parse repair exceeds the repair budget; regenerating once")
            repair = compose_prompt(contract, context)
        return job.repair(repair, validator, initial_error)

    if validator is None:
        return value
    try:
        return validator(value)
    except _VALIDATION_ERRORS as initial_error:
        job.dump("validation-failure", initial_error, raw)
        repair = _semantic_repair_prompt(contract, context, label, initial_error, value)
        if repair is not None:
            return job.repair(repair, validator, initial_error)
        if scene_scope is None:
            raise NarrativeValidationError(
                f"{label} repair was not attempted: the complete document exceeds "
                f"GEMINI_REPAIR_MAX_CHARS ({repair_budget()}) and has no scene-level repair. "
                f"Initial error: {initial_error}"
            ) from initial_error
        return _scene_scoped_repair(job, contract, context, value, initial_error,
                                    validator, scene_scope)


class _Job:
    def __init__(self, generator, label, stage, temperature_key, run_dir):
        self.generator = generator
        self.label = label
        self.stage = stage
        self.temperature_key = temperature_key
        self.run_dir = run_dir

    def call(self, prompt: str, stage: str, temperature_key: str | None) -> str:
        kwargs = {"stage": stage, "response_mime_type": "application/json"}
        if temperature_key:
            kwargs["temperature"] = stage_temperature(temperature_key)
        try:
            return self.generator(prompt, **kwargs)
        except GeminiGenerationError as exc:
            self.dump("generation-error", exc, "", prompt_chars=len(prompt), stage=stage)
            raise

    def repair(self, prompt: str, validator, initial_error) -> dict:
        raw = self.call(prompt, f"{self.stage}-repair", "repair")
        try:
            repaired = extract_json(raw, f"repaired {self.label}")
            return validator(repaired) if validator else repaired
        except _VALIDATION_ERRORS as repaired_error:
            self.dump("repair-failure", repaired_error, raw, initial_error=initial_error,
                      stage=f"{self.stage}-repair")
            raise NarrativeValidationError(
                f"{self.label} repair failed after one bounded attempt. "
                f"Initial error: {initial_error}. Repaired artifact error: {repaired_error}"
            ) from repaired_error

    def dump(self, kind: str, error, raw: str, *, stage: str | None = None, **extra) -> None:
        sections = {"stage": stage or self.stage, "failure": kind, "error": str(error)}
        sections.update({key: str(value) for key, value in extra.items()})
        sections["raw response"] = raw or "(none)"
        path = write_debug_artifact(
            self.run_dir, stage or self.stage, sections,
            secrets=(getattr(config, "GEMINI_API_KEY", ""),),
        )
        if path:
            print(f"   Debug output saved: {path}")


def _parse_repair_prompt(contract, context, label, error, raw) -> str | None:
    header = f"""The response below could not be parsed as the required {label} JSON.
Return exactly one complete JSON object with no markdown or commentary. Preserve every readable
story decision from the response and satisfy the complete contract.

PARSING ERROR:
{error}"""
    return _fit(header, contract, context, f"UNPARSEABLE RESPONSE:\n{raw}")


def _semantic_repair_prompt(contract, context, label, error, value) -> str | None:
    header = f"""The {label} JSON parsed successfully but failed semantic validation.
Return the COMPLETE corrected JSON object only. Correct only the fields identified below.
Preserve all valid character identities, motivations, relationships, story decisions, array order,
and every valid field exactly. Do not summarize, replace, or broadly rewrite the artifact.

VALIDATION ERROR:
{error}"""
    return _fit(header, contract, context, f"CURRENT VALID JSON:\n{compact_json(value)}")


def _scene_key(scene, position: int, id_key: str) -> str:
    sid = scene.get(id_key) if isinstance(scene, dict) else None
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    if isinstance(sid, int) and not isinstance(sid, bool):
        return str(sid)
    return f"#{position}"


def _scene_scoped_repair(job: _Job, contract, context, value, error, validator,
                         scope: SceneScope) -> dict:
    scenes = value.get(scope.list_key)
    if not isinstance(scenes, list) or not scenes:
        raise NarrativeValidationError(
            f"{job.label} scene-scoped repair impossible: '{scope.list_key}' is not a scene list. "
            f"Initial error: {error}"
        ) from error
    keys = [_scene_key(scene, index, scope.id_key) for index, scene in enumerate(scenes, 1)]
    issues = scope.issues(value)
    targets = [index for index, key in enumerate(keys) if key in issues]
    if not targets:
        return _scene_patch_repair(job, contract, context, value, error, validator, scope, keys)

    neighbours = sorted({
        near for index in targets for near in (index - 1, index + 1)
        if 0 <= near < len(scenes) and near not in targets
    })
    diagnostics = "\n".join(f"- scene {keys[i]}: {'; '.join(issues[keys[i]])}" for i in targets)
    header = f"""Part of the {job.label} JSON failed validation. Repair ONLY the invalid scenes listed
below. Return exactly {{"{scope.list_key}": [...]}} containing one complete corrected object per
invalid scene, in the same order, keeping each {scope.id_key}. Every scene must satisfy the complete
contract. Do not return the neighbour scenes; they are read-only continuity context.

VALIDATION ERRORS BY SCENE:
{diagnostics}"""
    invalid = compact_json([scenes[i] for i in targets])
    nearby = compact_json([scenes[i] for i in neighbours])
    prompt = _fit(header, contract, context,
                  f"INVALID SCENES TO CORRECT:\n{invalid}\n\nNEIGHBOUR SCENES (read-only):\n{nearby}")
    if prompt is None:
        raise NarrativeValidationError(
            f"{job.label} scene-scoped repair still exceeds GEMINI_REPAIR_MAX_CHARS "
            f"({repair_budget()}). Initial error: {error}"
        ) from error

    def merge(repaired: dict) -> dict:
        returned = repaired.get(scope.list_key)
        if not isinstance(returned, list) or len(returned) != len(targets):
            raise NarrativeValidationError(
                f"scene-scoped repair must return exactly {len(targets)} scenes "
                f"({', '.join(keys[i] for i in targets)})"
            )
        by_key = {_scene_key(scene, 0, scope.id_key): scene
                  for scene in returned if isinstance(scene, dict)}
        merged_scenes = list(scenes)
        for position, index in enumerate(targets):
            replacement = by_key.get(keys[index], returned[position])
            if not isinstance(replacement, dict):
                raise NarrativeValidationError(f"repaired scene {keys[index]} must be an object")
            if not keys[index].startswith("#"):
                replacement = {**replacement, scope.id_key: scenes[index][scope.id_key]}
            merged_scenes[index] = replacement
        return {**value, scope.list_key: merged_scenes}

    print(f"   -> Scene-scoped {job.label} repair: {', '.join(keys[i] for i in targets)}")
    return job.repair(prompt, lambda repaired: _validated(validator, merge(repaired)), error)


def _scene_patch_repair(job: _Job, contract, context, value, error, validator,
                        scope: SceneScope, keys: list[str]) -> dict:
    """Repair a whole-document problem (e.g. missing climax) with field patches."""
    scenes = value[scope.list_key]
    summary_fields = ("order", "title", "section_type", "narrative_function", "decision",
                      "consequence", "target_words")
    index = [
        {scope.id_key: key, **{field: scene.get(field) for field in summary_fields
                               if isinstance(scene, dict) and field in scene}}
        for key, scene in zip(keys, scenes)
    ]
    header = f"""The {job.label} JSON failed a whole-document check. The scene index below lists
every scene; full scene bodies are omitted because the document is too large to resend. Return
exactly {{"patches": [{{"{scope.id_key}": "...", "fields": {{...}}}}]}} with the smallest set of
field changes that fixes the error. Patch only fields that must change; every patched value must
satisfy the complete contract.

VALIDATION ERROR:
{error}"""
    prompt = _fit(header, contract, context, f"SCENE INDEX:\n{compact_json(index)}")
    if prompt is None:
        raise NarrativeValidationError(
            f"{job.label} patch repair still exceeds GEMINI_REPAIR_MAX_CHARS "
            f"({repair_budget()}). Initial error: {error}"
        ) from error

    def merge(repaired: dict) -> dict:
        patches = repaired.get("patches")
        if not isinstance(patches, list) or not patches:
            raise NarrativeValidationError("patch repair must return a nonempty patches array")
        merged_scenes = list(scenes)
        for patch in patches:
            key = str(patch.get(scope.id_key, "")) if isinstance(patch, dict) else ""
            fields = patch.get("fields") if isinstance(patch, dict) else None
            if key not in keys or not isinstance(fields, dict):
                raise NarrativeValidationError(f"patch for unknown scene {key or '(missing)'}")
            position = keys.index(key)
            fields = {name: item for name, item in fields.items() if name != scope.id_key}
            merged_scenes[position] = {**merged_scenes[position], **fields}
        return {**value, scope.list_key: merged_scenes}

    print(f"   -> Patch-based {job.label} repair for a whole-document error")
    return job.repair(prompt, lambda repaired: _validated(validator, merge(repaired)), error)


def _validated(validator, value):
    return validator(value) if validator else value


def _fit(header: str, contract: str, context: str, payload: str) -> str | None:
    """Assemble a repair prompt within budget: context is dropped before anything else."""
    for include_context in (True, False):
        parts = [header, f"CONTRACT:\n{contract.strip()}"]
        if include_context and context.strip():
            parts.append(f"CONTEXT:\n{context.strip()}")
        parts.append(payload)
        prompt = "\n\n".join(parts)
        if len(prompt) <= repair_budget():
            return prompt
    return None


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "stage"
