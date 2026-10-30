"""Apply human edits to any project's screenplay.

Run:  python -m scripts.apply_edits --project <slug> --edits <file.json> [--dry-run]

The edits file is a JSON list of {"scene_id", "find", "replace"} (scene_id: the screenplay id or
the outline id, e.g. 3 or "s03"). Every ``find`` must occur exactly once in its scene, or nothing
is changed. The previous script is kept as ``script.pre_edit-<time>.json``; the edits are recorded
as ``human_editorial`` (in script.json and narrative_review.json); the deterministic review and the
premise-brief checks run again offline; editorial findings on edited scenes are marked
``addressed_by_human_edit``; narration, timeline, mix, video, thumbnail and any approval are
invalidated (the visual bible and the shot list become stale through their source hashes).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from agents.human_edits import EditError, apply_edits, rereview
from agents.narrative import atomic_write_json, invalidate_downstream, load_json, validate_script
from studio import projects

APPROVAL_FILES = ("review_approval.json", "review_data.json")


def apply_to_project(path, edits_path, *, dry_run: bool = False) -> dict:
    path = Path(path)
    names = ("script.json", "story_brief.json", "character_bible.json", "story_outline.json")
    data = {name: load_json(path / name) for name in names}
    missing = [name for name, value in data.items() if not value]
    if missing:
        raise EditError(f"{path.name} is missing: {', '.join(missing)}")
    try:
        edits = json.loads(Path(edits_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EditError(f"cannot read {edits_path}: {exc}") from None
    script, bible, outline = data["script.json"], data["character_bible.json"], data["story_outline.json"]
    edited, applied = apply_edits(script, edits)
    edited = validate_script(edited, bible, outline)
    edited["editorial_edits"] = list(script.get("editorial_edits") or []) + applied
    previous = load_json(path / "narrative_review.json") or {}
    review = rereview(edited, data["story_brief.json"], bible, outline, previous, applied)
    result = {"edits": applied, "status": review["status"], "word_count": review.get("word_count"),
              "addressed": len(review.get("addressed_editorial_findings") or []), "removed": []}
    if dry_run:
        return result
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    atomic_write_json(path / f"script.pre_edit-{stamp}.json", script)
    atomic_write_json(path / "script.json", edited)
    atomic_write_json(path / "narrative_review.json", review)
    removed = invalidate_downstream("script_changed", str(path))
    for name in APPROVAL_FILES:
        if (path / name).is_file():
            (path / name).unlink()
            removed.append(str(path / name))
    if projects.is_project(path):
        projects.touch(path)
    result["removed"] = removed
    result["backup"] = f"script.pre_edit-{stamp}.json"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Apply find/replace edits to a project's screenplay.")
    parser.add_argument("--project", required=True, help="project slug")
    parser.add_argument("--edits", required=True, help="JSON list of {scene_id, find, replace}")
    parser.add_argument("--dry-run", action="store_true", help="check the edits; write nothing")
    args = parser.parse_args(argv)
    try:
        path = projects.path_for(args.project)
        result = apply_to_project(path, args.edits, dry_run=args.dry_run)
    except (projects.ProjectNotFound, EditError) as exc:
        print(f"Edits not applied: {exc}", file=sys.stderr)
        return 1
    verb = "would apply" if args.dry_run else "applied"
    print(f"{path.name}: {verb} {len(result['edits'])} edit(s); narrative status: {result['status']}; "
          f"words: {result['word_count']}; findings addressed by the edits: {result['addressed']}")
    if not args.dry_run:
        print(f"  previous script: {result['backup']}; invalidated files: {len(result['removed'])}")
        print("Next: re-run Narration (GUI → Projeto), then the visual bible and shots if stale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
