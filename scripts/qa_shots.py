"""Run the visual QA (Gemini vision) on existing shots, without regenerating them.

Run:  python -m scripts.qa_shots                 # every generated shot of the active project
      python -m scripts.qa_shots --only s04      # one scene (or a shot id, e.g. s04_02)

Each result is stored on the shot (``qa``: status and notes, shown on the GUI shot
card) and in asset_manifest.json (``visual_qa``). Failed shots can be regenerated
with a new seed from the GUI or with generate_shots --only <shot_id>.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from studio import projects
from video import shot_generation as gen
from video import visual_qa


def run(output, *, only: str | None = None, progress: Callable[[str], None] = print, generator=None) -> dict:
    output = Path(output)
    vb = load_json(output / vbible.FILE_NAME) or {}
    shot_list = load_json(output / shots_module.FILE_NAME) or {}
    shots = shot_list.get("shots", [])
    narration = shots_module.scene_narration(shots)
    summary = {"checked": 0, "passed": 0, "failed": [], "skipped": 0}
    for shot in shots:
        if shot.get("reuse_of") or not shot.get("image_path") or not (output / shot["image_path"]).is_file():
            continue
        if only and only not in (shot["scene_id"], shot["shot_id"]):
            continue
        expected = visual_qa.expected_for(shot, vb, narration.get(shot.get("scene_id", ""), ""))
        result = visual_qa.review(output / shot["image_path"], expected, generator=generator)
        shot["qa"] = {"status": result["status"], "notes": visual_qa.qa_note(result)}
        gen.update_provenance(output, shot["shot_id"], visual_qa=result)
        summary["checked"] += 1
        if result["status"] == "pass":
            summary["passed"] += 1
        elif result["status"] == "fail":
            summary["failed"].append(shot["shot_id"])
            progress(f"   ⛔ {shot['shot_id']}: {'; '.join(result['blocking'])}")
        else:
            summary["skipped"] += 1
            progress(f"   ⚠ {shot['shot_id']}: {result['warning']}")
        atomic_write_json(output / shots_module.FILE_NAME, shot_list)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Visual QA of existing shots (no regeneration).")
    parser.add_argument("--output-dir", default=None, help="explicit folder (overrides the project)")
    projects.add_argument(parser)
    parser.add_argument("--only", help="scene id (s04) or shot id (s04_02)")
    args = parser.parse_args(argv)
    output = projects.cli_output(args)
    summary = run(output, only=args.only)
    print(f"Visual QA: {summary['checked']} checked, {summary['passed']} passed, "
          f"{len(summary['failed'])} failed, {summary['skipped']} skipped.")
    if summary["failed"]:
        print("Failed: " + ", ".join(summary["failed"])
              + " — regenerate them with a new seed in the GUI or: python -m scripts.generate_shots --only <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
