"""Before/after preview of the motion (Phase 3.9-B): Ken Burns | parallax + ambient, and its cost.

Run:  python -m scripts.preview_motion --project <slug> --seconds 30
      python -m scripts.preview_motion --project <slug> --seconds 30 --start 120

Takes the shots covering the stretch (the project's, or its archive's when the project was archived),
renders the same frames twice — the previous Ken Burns (left) and the 3.9 motion (right: parallax from
the cached depth maps, ambient overlays where the shot has one, hard cuts inside a scene) — writes
<project>/motion_preview.mp4 and prints the frame-rendering time of each, extrapolated to a 12-minute
episode. Shots without a depth model or that fail a parallax check keep Ken Burns (the reason is
printed).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import config
from agents.narrative import load_json
from studio import projects


def project_root(slug: str | None) -> Path:
    """The folder holding shot_list.json and images/shots: the project, else its archive."""
    folder = projects.path_for(slug) if slug else projects.resolve()
    if (folder / "shot_list.json").is_file():
        return folder
    archived = projects.load(folder).get("archive_path") if projects.is_project(folder) else None
    if archived and Path(archived, "text", "shot_list.json").is_file():
        return Path(archived)
    raise FileNotFoundError(f"no shot_list.json in {folder} or its archive")


def shots_in(root: Path, start: float, seconds: float) -> list[dict]:
    shot_list = load_json(root / "shot_list.json") or load_json(root / "text" / "shot_list.json") or {}
    picked = []
    for index, shot in enumerate(shot_list.get("shots", [])):
        if shot["end"] <= start or shot["start"] >= start + seconds or not shot.get("image_path"):
            continue
        image = root / shot["image_path"].replace("\\", "/")
        if not image.is_file():
            continue
        picked.append({"shot_id": shot["shot_id"], "image": str(image), "shot_type": shot.get("shot_type", "medium"),
                       "start": max(0.0, shot["start"] - start), "end": min(seconds, shot["end"] - start),
                       "index": index, "ambient": shot.get("ambient") or "", "output_dir": str(root),
                       "transition": "cut"})
    return picked


def render(prepared: list[dict], seconds: float, fps: int) -> tuple[list[np.ndarray], float]:
    from video import creator
    frames, started = [], time.monotonic()
    for number in range(int(seconds * fps)):
        t = number / fps
        position = creator.shot_at(prepared, t)
        frames.append(creator._shot_frame(prepared[position], t))
    return frames, time.monotonic() - started


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ken Burns vs 3.9 motion, side by side.")
    parser.add_argument("--project")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--start", type=float, default=0)
    args = parser.parse_args(argv)
    from video import creator, tape
    try:
        root = project_root(args.project)
    except (FileNotFoundError, projects.ProjectNotFound) as exc:
        print(exc, file=sys.stderr)
        return 1
    shots = shots_in(root, args.start, args.seconds)
    if not shots:
        print("no shot images in that stretch", file=sys.stderr)
        return 1
    fps = int(config.VIDEO_FPS)
    with patch.object(config, "MOTION_STYLE", "kenburns"), patch.object(config, "AMBIENT_ENABLED", False):
        before, before_s = render(creator._prepare_shots([dict(shot, ambient="") for shot in shots]), args.seconds, fps)
    prepared = creator._prepare_shots(shots)
    for shot in prepared:
        print(f"   {shot['shot_id']}: {shot['motion']['kind']}" + (f" ({shot['motion'].get('reason')})"
                                                                    if shot["motion"].get("reason") else "")
              + (f" + {shot['ambient']}" if shot.get("ambient") else ""))
    after, after_s = render(prepared, args.seconds, fps)
    output = projects.resolve() / "motion_preview.mp4"
    width, height = 960, 540
    command = [tape.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width * 2}x{height}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "20", "-pix_fmt", "yuv420p", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    for left, right in zip(before, after):
        pair = np.hstack([np.asarray(Image.fromarray(left).resize((width, height))),
                          np.asarray(Image.fromarray(right).resize((width, height)))])
        process.stdin.write(pair.tobytes())
    process.stdin.close()
    process.wait()
    per_minute = lambda seconds: seconds / args.seconds * 60
    print(f"Preview: {output} (left Ken Burns, right 3.9 motion)")
    print(f"Frame rendering for {args.seconds:.0f} s: Ken Burns {before_s:.1f} s, 3.9 motion {after_s:.1f} s "
          f"({after_s / before_s - 1:+.0%}); for a 12-min episode about +{(per_minute(after_s) - per_minute(before_s)) * 12 / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
