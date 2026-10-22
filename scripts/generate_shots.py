"""Generate the episode's shot images with ComfyUI (resumable, cached).

Run:  python -m scripts.generate_shots --limit 5     # quick look at style/consistency
      python -m scripts.generate_shots               # full episode (long; resumable)
      python -m scripts.generate_shots --only s04    # one scene (or one shot id)

Builds visual_bible.json and shot_list.json first when missing (Gemini).
Refuses to start until every character in the shot list has an approved
reference portrait. Re-running skips finished shots; cached images are reused.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyUnavailable, ImageGenerator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate shot images with ComfyUI.")
    parser.add_argument("--output-dir", default=config.OUTPUT_DIR)
    parser.add_argument("--limit", type=int, help="generate at most N pending shots")
    parser.add_argument("--only", help="scene id (s04) or shot id (s04_02)")
    parser.add_argument("--rebuild-shot-list", action="store_true")
    parser.add_argument("--no-thumbnail", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output_dir)
    shot_list = gen.ensure_shot_list(output, force=args.rebuild_shot_list)
    estimate = gen.estimate_gpu_seconds(shot_list, output)
    print(f"Shot list: {len(shot_list['shots'])} shots, {shot_list['unique_shots']} unique"
          + (f"; estimated GPU time for pending shots: {estimate / 60:.0f} min" if estimate else
             "; run python -m scripts.calibrate_imagegen for a time estimate"))
    generator = ImageGenerator(output)
    try:
        summary = gen.generate_shots(output, generator, limit=args.limit, only=args.only)
        if not args.no_thumbnail and not args.only and not args.limit:
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
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
