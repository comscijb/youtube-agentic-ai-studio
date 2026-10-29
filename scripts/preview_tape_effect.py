"""Side-by-side preview of the tape (VHS) effect, and its encode overhead (Phase 3.6-F).

Run:  python -m scripts.preview_tape_effect --seconds 20 --intensity subtle
      python -m scripts.preview_tape_effect --seconds 20 --intensity medium --input path/to/final_video.mp4

Takes a 20-s excerpt of the project's final video (the archived one when the project was archived),
renders before | after side by side with a tracking glitch every 5 s (standing in for scene cuts) and
writes <project>/tape_preview_<intensity>.mp4. The excerpt already has its captions burnt in, so here they
go through the effect too; in a real render the captions are added after it and stay crisp. Prints the
encode overhead: how much longer the same excerpt takes with the effect.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from studio import projects
from video import tape

SIZE = (1920, 1080)
FPS = 30


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def find_input(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    folder = projects.resolve()
    local = folder / "final_video.mp4"
    if local.is_file():
        return local
    archived = projects.load(folder).get("archive_path")
    if archived and Path(archived, "video", "final_video.mp4").is_file():
        return Path(archived, "video", "final_video.mp4")
    raise FileNotFoundError("no final_video.mp4 in the active project or its archive; pass --input")


def preview_graph(level: str, seconds: float, every: float = 5.0, scanlines: str | None = None) -> str:
    cuts = [every * index for index in range(1, int(seconds // every) + 1) if every * index < seconds]
    chain = tape.filter_graph(level, SIZE, FPS, tape.glitch_windows(cuts, seconds), scanlines=scanlines)
    return (f"[0:v]scale={SIZE[0]}:{SIZE[1]},fps={FPS},split=2[a][b];[b]{chain}[t];"
            f"[a]scale=960:540[l];[t]scale=960:540[r];[l][r]hstack=inputs=2[v]")


def timed(command: list[str]) -> float:
    started = time.monotonic()
    subprocess.run(command, check=True, capture_output=True)
    return time.monotonic() - started


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Before/after preview of the tape effect.")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--intensity", choices=sorted(tape.LEVELS), default="subtle")
    parser.add_argument("--input", help="a video (default: the active project's final_video.mp4)")
    parser.add_argument("--start", type=float, default=30, help="excerpt start in seconds")
    args = parser.parse_args(argv)
    try:
        source = find_input(args.input)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    exe = ffmpeg()
    output = projects.resolve() / f"tape_preview_{args.intensity}.mp4"
    scanlines = tape.write_scanlines(output.with_name("tape_scanlines.png"), SIZE,
                                     tape.LEVELS[args.intensity]["scanline_alpha"])
    clip = ["-ss", str(args.start), "-t", str(args.seconds), "-i", str(source)]
    encode = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    subprocess.run([exe, "-y", "-loglevel", "error", *clip, "-filter_complex", preview_graph(args.intensity, args.seconds, scanlines=scanlines),
                    "-map", "[v]", *encode, str(output)], check=True)
    cuts = [5.0 * index for index in range(1, int(args.seconds // 5) + 1)]
    effect = tape.filter_graph(args.intensity, SIZE, FPS, tape.glitch_windows(cuts, args.seconds), scanlines=scanlines)
    base = [exe, "-y", "-loglevel", "error", *clip, "-vf", f"scale={SIZE[0]}:{SIZE[1]},fps={FPS}"]
    plain = timed([*base, *encode, "-f", "null", "-"])
    with_tape = timed([*base[:-1], base[-1] + "," + effect, *encode, "-f", "null", "-"])
    extra = with_tape - plain
    print(f"Preview: {output}")
    print(f"Encode of {args.seconds:.0f} s at 1080p: {plain:.1f} s plain, {with_tape:.1f} s with '{args.intensity}' "
          f"(+{extra:.1f} s, ≈ +{extra / args.seconds * 60:.0f} s per minute of video)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
