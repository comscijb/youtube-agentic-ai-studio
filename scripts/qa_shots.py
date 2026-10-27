"""Run the layered visual QA on existing shots, without regenerating them.

Run:  python -m scripts.qa_shots                                   # configured layers (IMAGEGEN_QA_LAYERS)
      python -m scripts.qa_shots --layers deterministic,local_vlm  # explicit layers
      python -m scripts.qa_shots --gemini                          # Gemini answers the focused questions
      python -m scripts.qa_shots --only s04                        # one scene (or a shot id, e.g. s04_02)

Layer 1 (CPU detectors) runs on every shot first; then ComfyUI's models are
freed, the local VLM answers every focused question of the batch in one go,
and is unloaded. Each result is stored on the shot (``qa``: verdict, issues
with boxes, notes — shown on the GUI shot card) and in asset_manifest.json
(``visual_qa``). Fix failed shots with generate_shots --fix-only or in the GUI.
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
from video.qa import fixes
from video.qa.layered import LayeredQA, configured_layers


def run(output, *, only: str | None = None, layers: list[str] | None = None, qa: LayeredQA | None = None,
        progress: Callable[[str], None] = print) -> dict:
    output = Path(output)
    vb = load_json(output / vbible.FILE_NAME) or {}
    shot_list = load_json(output / shots_module.FILE_NAME) or {}
    shots = shot_list.get("shots", [])
    narration = shots_module.scene_narration(shots)
    selected = [shot for shot in shots if not shot.get("reuse_of") and shot.get("image_path")
                and (output / shot["image_path"]).is_file()
                and (not only or only in (shot["scene_id"], shot["shot_id"]))]
    summary = {"checked": 0, "passed": 0, "failed": [], "skipped": 0, "edit": [], "regenerate": []}
    if not selected:
        return summary
    if qa is None:
        from video.imagegen_comfyui import ComfyClient
        qa = LayeredQA(layers, comfy=ComfyClient(), progress=progress)
    progress(f"   QA layers: {', '.join(qa.layers)} — layer 1 on {len(selected)} shot(s)…")
    items = [(output / shot["image_path"],
              visual_qa.expected_for(shot, vb, narration.get(shot.get("scene_id", ""), "")),
              int((shot.get("qa") or {}).get("edits", 0))) for shot in selected]
    pendings = [qa.prepare(*item) for item in items]
    asked = sum(len(pending["questions"]) for pending in pendings)
    if asked:
        progress(f"   Layer 2: {asked} focused question(s)…")
    qa.answer(pendings)
    for shot, pending in zip(selected, pendings):
        result = qa.finish(pending)
        fixes.record_result(shot, result)
        gen.update_provenance(output, shot["shot_id"], visual_qa=result)
        summary["checked"] += 1
        if result["status"] == "pass":
            summary["passed"] += 1
        elif result["status"] == "fail":
            summary["failed"].append(shot["shot_id"])
            summary[result["verdict"]].append(shot["shot_id"])
            progress(f"   ⛔ {shot['shot_id']} → {result['verdict']}: {result['verdict_reason']}")
        else:
            summary["skipped"] += 1
            progress(f"   ⚠ {shot['shot_id']}: {'; '.join(result['warnings'])}")
    atomic_write_json(output / shots_module.FILE_NAME, shot_list)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Layered visual QA of existing shots (no regeneration).")
    parser.add_argument("--output-dir", default=None, help="explicit folder (overrides the project)")
    projects.add_argument(parser)
    parser.add_argument("--only", help="scene id (s04) or shot id (s04_02)")
    parser.add_argument("--layers", help="comma list of deterministic, local_vlm, gemini (default: IMAGEGEN_QA_LAYERS)")
    parser.add_argument("--gemini", action="store_true", help="add Gemini vision as layer 3")
    args = parser.parse_args(argv)
    output = projects.cli_output(args)
    layers = configured_layers(args.layers, gemini=True if args.gemini else None)
    summary = run(output, only=args.only, layers=layers)
    print(f"Visual QA: {summary['checked']} checked, {summary['passed']} passed, "
          f"{len(summary['failed'])} failed ({len(summary['edit'])} to edit, "
          f"{len(summary['regenerate'])} to regenerate), {summary['skipped']} skipped.")
    if summary["failed"]:
        print("Fix them with: python -m scripts.generate_shots --fix-only (or in the GUI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
