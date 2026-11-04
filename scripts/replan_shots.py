"""Rewrite shot wording that draws a second person, with no Gemini call (Phase 3.9.4-A.3).

Run:  python -m scripts.replan_shots --project <slug> --only-wording [--dry-run]

A shot that lists a character and says "a lone figure" gets that character from the reference and the
figure from the words. This rewrites those actions and recomposes their prompts. Images stay as they
are: use "Gerar de novo (lote)" in the viewer on the shots it lists.
"""
from __future__ import annotations

import argparse
import sys

from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from studio import projects


def rewrite_project(project, *, dry_run: bool = False) -> list[dict]:
    """One row {"shot_id", "before", "after"} per changed shot."""
    path = project / shots_module.FILE_NAME
    shot_list = load_json(path) or {}
    vb = load_json(project / vbible.FILE_NAME) or {}
    changed = []
    for shot in shot_list.get("shots", []):
        before = str(shot.get("action", ""))
        after = shots_module.rewrite_action(before, shot.get("characters") or [], vb, bool(shot.get("others_present")))
        if after == before:
            continue
        changed.append({"shot_id": shot["shot_id"], "before": before, "after": after})
        if dry_run:
            continue
        shot.setdefault("wording_rewritten_from", before)
        shot["action"] = after
        composed = shots_module.compose_shot(shot, vb, project, assume_references=True)
        shot["prompt"] = composed["prompt"]
        shot["reference_slots"] = composed["slots"]
    if changed and not dry_run:
        atomic_write_json(path, shot_list)
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--only-wording", action="store_true", required=True, help="no new Gemini calls")
    parser.add_argument("--dry-run", action="store_true", help="list the changes; write nothing")
    args = parser.parse_args(argv)
    try:
        project = projects.path_for(args.project)
    except projects.ProjectNotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    changed = rewrite_project(project, dry_run=args.dry_run)
    for row in changed:
        print(f"{row['shot_id']}: {row['after']}".encode("ascii", "replace").decode("ascii"))
    print(f"{len(changed)} shot(s) {'would change' if args.dry_run else 'changed'}"
          + ("" if args.dry_run or not changed else "; regenerate them with Gerar de novo (lote) in the viewer"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
