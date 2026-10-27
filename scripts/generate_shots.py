"""Generate the episode's shot images with ComfyUI (resumable, cached).

Run:  python -m scripts.generate_shots --limit 5     # quick look at style/consistency
      python -m scripts.generate_shots               # full episode (long; resumable)
      python -m scripts.generate_shots --only s04    # one scene (or one shot id)
      python -m scripts.generate_shots --fix-only    # edit/regenerate only what failed QA

Phase-batched (3.5-E): all pending shots are generated with Klein (layer-1 QA on
the CPU as they come), then ComfyUI's models are freed and the local VLM checks
the batch, then Klein comes back to region-edit or regenerate what failed;
QA → fix repeats for the fixed shots (IMAGEGEN_QA_ROUNDS).

Builds visual_bible.json and shot_list.json first when missing (Gemini).
Refuses to start until every character in the shot list has an approved
reference portrait. Re-running skips finished shots; cached images are reused.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from studio import projects
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyUnavailable, ImageGenerator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate shot images with ComfyUI.")
    parser.add_argument("--output-dir", default=None, help="explicit folder (overrides the project)")
    projects.add_argument(parser)
    parser.add_argument("--limit", type=int, help="generate at most N pending shots")
    parser.add_argument("--only", help="scene id (s04) or shot id (s04_02)")
    parser.add_argument("--rebuild-shot-list", action="store_true")
    parser.add_argument("--no-thumbnail", action="store_true")
    parser.add_argument("--fix-only", action="store_true",
                        help="no new shots: QA what was never checked and fix what failed QA")
    args = parser.parse_args(argv)
    output = projects.cli_output(args)
    shot_list = gen.ensure_shot_list(output, force=args.rebuild_shot_list and not args.fix_only)
    estimate = gen.estimate_gpu_seconds(shot_list, output)
    print(f"Shot list: {len(shot_list['shots'])} shots, {shot_list['unique_shots']} unique"
          + (f"; estimated GPU time for pending shots: {estimate / 60:.0f} min" if estimate else
             "; run python -m scripts.calibrate_imagegen for a time estimate"))
    pending = [shot["shot_id"] for shot in shot_list["shots"]
               if not shot.get("reuse_of") and not gen._done(shot, output)]
    if args.fix_only and len(pending) == len([s for s in shot_list["shots"] if not s.get("reuse_of")]):
        print("Nothing to fix: no shot of this shot list has an image yet (the list was planned after the "
              "images on disk). Generate them first: python -m scripts.generate_shots")
        return 0
    generator = ImageGenerator(output)
    try:
        summary = gen.generate_shots(output, generator, limit=args.limit, only=args.only, fix_only=args.fix_only)
        if not args.no_thumbnail and not args.only and not args.limit and not args.fix_only:
            print("   → Thumbnail image…")
            gen.generate_thumbnail_image(output, generator)
    except gen.ApprovalRequired as exc:
        print(exc, file=sys.stderr)
        return 3
    except ComfyUnavailable as exc:
        print(exc, file=sys.stderr)
        print("Progress so far is saved; re-run the same command to resume.", file=sys.stderr)
        return 2
    print(f"Done: {summary['generated']} generated, {summary['cached']} from cache, "
          f"{summary['fallback']} stock fallbacks, {len(summary['failed'])} failed "
          f"in {summary['seconds'] / 60:.1f} min.")
    if summary["failed"]:
        print("Failed shots: " + ", ".join(summary["failed"]) + " (re-run to retry)")
    if "qa_checked" in summary:
        print(f"QA: {summary['qa_checked']} check(s), {summary['qa_edits']} region edit(s), "
              f"{summary['qa_regenerations']} new seed(s); still failing: {', '.join(summary['qa_failed']) or 'none'}"
              f"; kept best and flagged: {', '.join(summary['qa_flagged']) or 'none'}")
        if summary["qa_failed"]:
            print("Continue with: python -m scripts.generate_shots --fix-only")
    if summary.get("flagged"):
        print("Flagged shots (skipped until edited in the GUI, or re-planned with --rebuild-shot-list): "
              + ", ".join(summary["flagged"]))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
