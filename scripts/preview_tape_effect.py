"""Side-by-side preview of the film look, and its encode overhead (Phase 3.8-D).

Run:  python -m scripts.preview_tape_effect --seconds 20 --look super8
      python -m scripts.preview_tape_effect --seconds 20 --look grain --input path/to/final_video.mp4

Takes a 20-s excerpt of the project's final video (the archived one when the project was archived)
centred on a scene cut, so you can see that the cut stays clean while grain and dust keep running
across it; renders before | after side by side and writes <project>/film_preview_<look>.mp4. The
excerpt already has its captions burnt in, so here they go through the effect too; in a real render
the captions are added after it and stay crisp. Prints the encode overhead and the
photosensitivity numbers.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from agents.narrative import load_json
from studio import projects
from video import tape

SIZE = (1920, 1080)
FPS = 30


def project_paths(explicit: str | None) -> tuple[Path, Path | None]:
    """(video, scene_timeline.json or None)."""
    folder = projects.resolve()
    archived = projects.load(folder).get("archive_path") if projects.is_project(folder) else None
    roots = [folder] + ([Path(archived)] if archived else [])
    timeline = next((path for root in roots for path in Path(root).rglob("scene_timeline.json")), None)
    if explicit:
        return Path(explicit), timeline
    for root in roots:
        for candidate in (Path(root) / "final_video.mp4", Path(root) / "video" / "final_video.mp4"):
            if candidate.is_file():
                return candidate, timeline
    raise FileNotFoundError("no final_video.mp4 in the active project or its archive; pass --input")


def start_around_cut(timeline: Path | None, seconds: float, default: float = 30.0) -> tuple[float, float | None]:
    """An excerpt start that puts the first scene cut after 30 s in the middle (start, cut)."""
    data = load_json(timeline) if timeline else None
    cuts = [float(scene["start"]) for scene in (data or {}).get("scenes", [])[1:] if float(scene["start"]) > 30]
    if not cuts:
        return default, None
    return max(0.0, cuts[0] - seconds / 2), cuts[0]


def preview_graph(look: str, dust: Path | None) -> str:
    chain = tape.filter_graph(look, SIZE, FPS, dust=dust, offset=tape.start_offset("preview"))
    return (f"[0:v]scale={SIZE[0]}:{SIZE[1]},fps={FPS},split=2[a][b];[b]{chain}[t];"
            f"[a]scale=960:540[l];[t]scale=960:540[r];[l][r]hstack=inputs=2[v]")


def timed(command: list[str]) -> float:
    started = time.monotonic()
    subprocess.run(command, check=True, capture_output=True)
    return time.monotonic() - started


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Before/after preview of the film look.")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--look", choices=sorted(tape.LOOKS), default="super8")
    parser.add_argument("--input", help="a video (default: the active project's final_video.mp4)")
    parser.add_argument("--start", type=float, help="excerpt start in seconds (default: around a scene cut)")
    args = parser.parse_args(argv)
    try:
        source, timeline = project_paths(args.input)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    start, cut = (args.start, None) if args.start is not None else start_around_cut(timeline, args.seconds)
    exe = tape.ffmpeg_exe()
    output = projects.resolve() / f"film_preview_{args.look}.mp4"
    dust = tape.dust_layer(args.look, SIZE, FPS)
    clip = ["-ss", str(start), "-t", str(args.seconds), "-i", str(source)]
    encode = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    subprocess.run([exe, "-y", "-loglevel", "error", *clip, "-filter_complex", preview_graph(args.look, dust),
                    "-map", "[v]", *encode, str(output)], check=True)
    effect = tape.filter_graph(args.look, SIZE, FPS, dust=dust, offset=tape.start_offset("preview"))
    base = [exe, "-y", "-loglevel", "error", *clip, "-vf", f"scale={SIZE[0]}:{SIZE[1]},fps={FPS}"]
    plain = timed([*base, *encode, "-f", "null", "-"])
    with_look = timed([*base[:-1], base[-1] + "," + effect, *encode, "-f", "null", "-"])
    extra = with_look - plain
    safety = tape.check_safety(tape.LOOKS[args.look], FPS)
    print(f"Preview: {output}" + (f" (scene cut at {cut - start:.1f} s into the excerpt)" if cut else ""))
    print(f"Encode of {args.seconds:.0f} s at 1080p: {plain:.1f} s plain, {with_look:.1f} s with '{args.look}' "
          f"(+{extra:.1f} s, {extra / plain:+.0%})")
    print(f"Photosensitivity: max luminance step {safety['max_luma_step']:.1%} between frames (≤ 10 %), "
          f"flicker ±{safety['flicker_amplitude']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
