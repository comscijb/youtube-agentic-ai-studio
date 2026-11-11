"""Report how a project's shots follow their narration (Phase 3.9.9). Report only: nothing is changed.

Run:  python -m scripts.check_adherence --project <slug>
      python -m scripts.check_adherence --folder "D:/Private Gravity/Archive/<slug>/text"    # an archived project

Lists the shots whose location the narration contradicts (and where they would move), the ones that only need a review
(a place named in a memory), the drawn actions that contradict the narration (outside vs inside the car, standing vs
seated), and the staging continuity problems when a staging file exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from agents import adherence
from agents.narrative import load_json


def run(folder: Path) -> dict:
    shot_list = load_json(folder / "shot_list.json") or {}
    vb = load_json(folder / "visual_bible.json") or {}
    result = adherence.report(shot_list.get("shots", []), vb)
    staging = load_json(folder / "staging.json")
    if staging:
        from agents import staging as staging_module
        result["continuity"] = staging_module.continuity(shot_list.get("shots", []))
    result["shots"] = len(shot_list.get("shots", []))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    parser.add_argument("--folder")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    folder = Path(args.folder) if args.folder else Path(config.PROJECTS_DIR) / str(args.project)
    result = run(folder)
    if args.json:
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 0
    say = lambda line: print(str(line).encode("ascii", "replace").decode("ascii"), flush=True)
    say(f"{result['shots']} shots: {len(result['relocations'])} wrong location, {len(result['review'])} to review, "
        f"{len(result['contradictions'])} contradictions")
    for item in result["relocations"]:
        say(f"  {item['shot_id']}: {item['from']} -> {item['to']} ({', '.join(item['evidence'])})")
    for item in result["review"]:
        say(f"  {item['shot_id']}: review - {item['note']}")
    for item in result["contradictions"]:
        say(f"  {item['shot_id']}: " + "; ".join(item["problems"]))
    for item in result.get("continuity", []):
        say(f"  {item['shots']}: continuity - {item['problem']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
