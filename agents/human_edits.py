"""Human editorial edits on a screenplay (``find``/``replace`` per scene).

Used by ``scripts.promote_benchmark`` and ``scripts.apply_edits``:

* every ``find`` must occur exactly once in its scene's narration, or nothing is changed;
* applied edits are recorded with the author ``human_editorial``;
* the deterministic review runs again offline (with the premise-brief faithfulness checks),
  re-merged with the existing editorial assessment; findings on edited scenes are marked
  ``addressed_by_human_edit`` and no longer drive the status.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from agents.narrative import (
    deterministic_review,
    merge_editorial_review,
    screen_editorial_findings,
    stable_hash,
)

EDIT_AUTHOR = "human_editorial"


class EditError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _scene_index(script: dict) -> dict:
    index = {}
    for position, scene in enumerate(script.get("sections", [])):
        index[scene.get("outline_scene_id")] = position
        index[str(scene.get("id"))] = position
        index[scene.get("id")] = position
    return index


def apply_edits(script: dict, edits: list[dict]) -> tuple[dict, list[dict]]:
    """Return an edited copy and the applied-edit records; raise before any change."""
    if not isinstance(edits, list):
        raise EditError("edits file must contain a JSON list of {scene_id, find, replace}")
    edited = json.loads(json.dumps(script))
    index = _scene_index(edited)
    applied, problems = [], []
    for number, edit in enumerate(edits, 1):
        scene_id = edit.get("scene_id") if isinstance(edit, dict) else None
        find = edit.get("find") if isinstance(edit, dict) else None
        replace = edit.get("replace") if isinstance(edit, dict) else None
        if scene_id not in index or not isinstance(find, str) or not find \
                or not isinstance(replace, str):
            problems.append(f"edit {number}: needs a known scene_id and string find/replace")
            continue
        scene = edited["sections"][index[scene_id]]
        occurrences = scene["narration"].count(find)
        if occurrences != 1:
            problems.append(f"edit {number} ({scene_id}): find occurs {occurrences} times "
                            f"(must be exactly once): {find[:60]!r}")
            continue
        scene["narration"] = scene["narration"].replace(find, replace)
        applied.append({"scene_id": scene_id, "section_id": scene["id"], "find": find,
                        "replace": replace, "author": EDIT_AUTHOR, "applied_at": _now()})
    if problems:
        raise EditError("no change made; " + "; ".join(problems))
    return edited, applied


def rereview(script: dict, brief: dict, bible: dict, outline: dict, previous: dict,
             edits: list[dict], live_editorial: bool = False) -> dict:
    """Deterministic review again, re-merged with the existing (or a fresh) editorial review."""
    from agents import premise_brief
    target = float(script.get("target_duration_minutes") or brief.get("target_duration_minutes") or 15)
    report = deterministic_review(script, target, outline, previous.get("repair_attempts"), brief)
    report = premise_brief.add_findings(report, brief, bible=bible, outline=outline, script=script)
    edited_ids = {edit["section_id"] for edit in edits}
    if live_editorial:
        from agents.scriptwriter import _editorial_review
        print("   -> Running a live editorial review of the edited screenplay...")
        assessment = _editorial_review(script, brief, bible, outline, report)
        merged = premise_brief.resolve_candidates(merge_editorial_review(report, assessment, bible),
                                                  assessment.get("brief_check"), script)
    else:
        assessment = json.loads(json.dumps(previous.get("editorial_assessment") or {})) or None
        addressed = []
        if assessment:
            # Discard findings contradicting deterministic facts first, so they
            # are never reported as addressed by an edit.
            kept, discarded = screen_editorial_findings(assessment.get("findings", []), report, bible)
            remaining = []
            for finding in kept:
                if finding.get("addressed_by_human_edit") or edited_ids & set(finding.get("scene_ids") or []):
                    finding["addressed_by_human_edit"] = True
                    addressed.append(finding)
                else:
                    remaining.append(finding)
            # Addressed findings stay visible but no longer drive the status.
            merged = merge_editorial_review(report, dict(assessment, findings=remaining), bible)
            merged = premise_brief.resolve_candidates(merged, assessment.get("brief_check"), script)
            merged["editorial_assessment"]["findings"] = remaining + addressed
            merged["discarded_editorial_findings"] = discarded
        else:
            merged = merge_editorial_review(report, None)
        merged["addressed_editorial_findings"] = addressed       # earlier edits' findings included
        merged["editorial_review_stale"] = bool(edits)
    merged["editorial_edits"] = list(previous.get("editorial_edits") or []) + edits
    merged["source_hash"] = stable_hash(script)
    return merged
