"""A quick render of any minute with the Phase 4.2 cut, next to the old one (Phase 4.2-G).

Run:  python -m scripts.preview_cut --project <slug> --seconds 60 [--from 0]
Works on a live project or an archived one: the files it needs are copied into a working folder
(projects/<slug>/preview/ is never created; the archive stays intact). It writes, in previews/<slug>/:
  new_<from>-<to>.mp4   the new cut (derived views, key lines, beat grades, sound design, music plan)
  old_<from>-<to>.mp4   the same minute of the published final_video.mp4, when it exists
  video_attention.json  the video attention map of the new minute
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

NEEDED = ("script.json", "narration.mp3", "narration.wav", "narration_words.json", "scene_timeline.json", "shot_list.json",
          "attention_map.json", "sfx_cues.json", "story_brief.json", "asset_manifest.json", "narration.srt", "visual_bible.json")


def _archive_files(slug: str) -> tuple[Path | None, dict]:
    from agents.narrative import load_json
    from studio import archive
    try:
        root = archive.root() / slug
    except Exception:                                    # noqa: BLE001
        return None, {}
    manifest = load_json(root / "archive_manifest.json") or {}
    mapping = {}
    for item in manifest.get("files", []):
        original = str(item["original"]).replace("\\", "/")
        relative = original.split(f"{slug}/", 1)[-1]
        mapping[relative] = root / item["archive"]
    return root, mapping


def workspace(slug: str, folder: Path) -> Path:
    """A working copy with the narration, timings, screenplay, shot list and the shot images."""
    from studio import projects
    live = projects.root() / slug
    _, mapping = _archive_files(slug)
    folder.mkdir(parents=True, exist_ok=True)

    def source(relative: str) -> Path | None:
        candidate = live / relative
        if candidate.is_file():
            return candidate
        return mapping.get(relative)
    for name in NEEDED:
        found = source(name)
        if found and found.is_file():
            shutil.copyfile(found, folder / name)
    recorded = Path("docs/reports/4.1/recorded")
    if not (folder / "attention_map.json").is_file():   # the 4.1 calibration recorded maps for the archived episodes
        from agents.narrative import load_json
        calibration = load_json(recorded / "calibration.json") or load_json(Path("docs/reports/4.1/calibration.json")) or {}
        name = next((key for key, value in calibration.items() if value.get("slug") == slug), None)
        if name and (recorded / name / "attention_map.json").is_file():
            shutil.copyfile(recorded / name / "attention_map.json", folder / "attention_map.json")
    images = [relative for relative in set(mapping) | {path.relative_to(live).as_posix() for path in live.rglob("*") if path.is_file()}
              if relative.startswith("images/shots/") and "/history/" not in relative and "/depth/" not in relative
              and "/removal/" not in relative and relative.endswith((".jpg", ".png"))]
    for relative in images:
        found = source(relative)
        if found and found.is_file():
            (folder / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(found, folder / relative)
    return folder


def old_cut(slug: str, start: float, seconds: float, target: Path) -> Path | None:
    from studio import projects
    from video.music import _run
    _, mapping = _archive_files(slug)
    final = projects.root() / slug / "final_video.mp4"
    final = final if final.is_file() else mapping.get("final_video.mp4")
    if not final or not Path(final).is_file():
        return None
    result = _run(["-y", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(final), "-c:v", "libx264", "-preset",
                   "veryfast", "-crf", "20", "-c:a", "aac", str(target)])
    return target if result.returncode == 0 else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--from", dest="start", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    from agents.narrative import load_json
    from video.creator import create_video
    from video.music import prepare_final_audio
    out = Path("previews") / args.project
    work = workspace(args.project, out / "work")
    script = load_json(work / "script.json")
    if not script:
        print(f"Sem script.json para {args.project}")
        return 1
    narration = next((work / name for name in ("narration.mp3", "narration.wav") if (work / name).is_file()), None)
    if narration is None:
        print("Sem narração no projeto")
        return 1
    mixed = work / "narration_mixed.mp3"
    prepare_final_audio(str(narration), str(mixed), episode=script, output_dir=str(work))
    tag = f"{int(args.start)}-{int(args.start + args.seconds)}"
    path = create_video(script, str(mixed), str(work), preview={"start": args.start, "seconds": args.seconds})
    shutil.copyfile(path, out / f"new_{tag}.mp4")
    if (work / "video_attention.json").is_file():
        shutil.copyfile(work / "video_attention.json", out / "video_attention.json")
    old = old_cut(args.project, args.start, args.seconds, out / f"old_{tag}.mp4")
    print(f"Novo corte: {out / f'new_{tag}.mp4'}")
    print(f"Corte antigo: {old}" if old else "Corte antigo: final_video.mp4 não encontrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
