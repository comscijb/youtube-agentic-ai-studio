"""Generate 6 character-free style boards for the chosen variant; approve 1-2.

Run:  python -m scripts.generate_style_refs              # candidates (ComfyUI)
      python -m scripts.generate_style_refs --approve 3  # approve board #3 (also in the GUI)
      python -m scripts.generate_style_refs --status

Approved boards become style anchors shared by every episode
(config.STYLE_REFS_DIR). Shots use one as a reference when a slot is free.
"""
from __future__ import annotations

import argparse
import sys

import config
from studio import projects
from agents import identity, style_refs
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyUnavailable, ImageGenerator, ImageJob

BOARDS = 6


def status() -> int:
    print(f"Style: {identity.visual_style()} / {identity.style_variant()} (hash {identity.style_hash()})")
    candidates = sorted(style_refs.candidates_dir().glob("board_*.jpg"))
    print(f"  candidates: {len(candidates)}  approved anchors: "
          + (", ".join(entry["path"] for entry in style_refs.approved()) or "none"))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Style anchors (character-free style boards).")
    parser.add_argument("--output-dir", default=None, help="explicit folder (overrides the project)")
    projects.add_argument(parser)
    parser.add_argument("--approve", type=int, metavar="N")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    args.output_dir = str(projects.cli_output(args))
    if args.status:
        return status()
    if args.approve:
        try:
            style_refs.approve(args.approve)
        except FileNotFoundError as exc:
            parser.error(str(exc))
        print(f"Approved style board #{args.approve}.")
        return status()
    if not identity.is_illustration():
        parser.error("style anchors are for VISUAL_STYLE=dark_illustration")
    generator = ImageGenerator(args.output_dir)
    folder = style_refs.candidates_dir()
    try:
        for index in range(BOARDS):
            print(f"   → Style board {index + 1}/{BOARDS}")
            generator.generate_with_retry(ImageJob(
                job_id=f"style_board_{index + 1}", prompt=style_refs.board_prompt(index),
                seed=9000 + index, output_path=str(folder / f"board_{index + 1}.jpg"),
                size=gen.native_size()), new_seeds=2)
    except ComfyUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Boards saved in {folder}. Approve 1-2 in the GUI or with --approve N.")
    return status()


if __name__ == "__main__":
    sys.exit(main())
