"""Backfill an already published video: English captions, language fields, description (Phase 3.6-D).

Run (live; the first run asks for consent again — the new youtube.force-ssl scope):
    python -m scripts.youtube_backfill --video-id w-XazyuEUv8 --captions --language --description
    python -m scripts.youtube_backfill --video-id w-XazyuEUv8 --description --dry-run   # show, change nothing

Finds the project in data/publications.json; when it is archived, copies back only the files needed
(narration.srt, script, brief, timeline, manifest — the archive stays intact). --description rebuilds
the description from the template (hook, chapters, CTA); the hook, chapter titles and comment
question come from description_parts.json, written by Gemini once when missing (--no-gemini keeps
the logline and the scene titles). The title, tags and category on YouTube are kept.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agents.narrative import load_json
from studio import archive, projects
from uploader import publications

NEEDED = ["narration.srt", "script.json", "story_brief.json", "research.json", "scene_timeline.json",
          "asset_manifest.json", "description_parts.json"]


def project_for(video_id: str) -> Path:
    record = next((item for item in publications.load_ledger().get("publications", [])
                   if item.get("video_id") == video_id), None)
    if record is None:
        raise LookupError(f"{video_id} is not in {publications.ledger_path()}")
    slug = record.get("project") or Path(record.get("run_dir", "")).name
    folder = projects.root() / slug
    if folder.is_dir():
        return folder
    run_dir = Path(record.get("run_dir", ""))
    if run_dir.is_dir():
        return run_dir
    raise LookupError(f"no project folder for {video_id} ({slug}); restore it with scripts.restore_project")


def ensure_files(folder: Path, log=print) -> None:
    """Archived project: restore only what the backfill reads."""
    missing = [name for name in ("narration.srt", "script.json") if not (folder / name).is_file()]
    if missing and (folder / archive.POINTER).is_file():
        archive.restore_project(folder.name, only=NEEDED, log=log)


def build_description(folder: Path, *, use_gemini: bool = True, generator=None) -> str:
    from agents import packaging
    from video import description
    from video.timeline import load_timeline
    script = load_json(folder / "script.json") or {}
    brief = load_json(folder / "story_brief.json") or load_json(folder / "research.json") or {}
    parts = load_json(folder / packaging.DESCRIPTION_PARTS_FILE)
    if parts is None and use_gemini:
        parts = packaging.generate_description_parts(folder, brief=brief, script=script,
                                                     timeline=load_timeline(folder / "scene_timeline.json"),
                                                     generator=generator)
    return description.build(folder, script, brief, parts)


def run(video_id: str, *, captions: bool, language: bool, describe: bool, dry_run: bool = False,
        service=None, use_gemini: bool = True, generator=None, replace: bool = False, log=print) -> dict:
    from uploader import youtube
    folder = project_for(video_id)
    ensure_files(folder, log=log)
    result = {"video_id": video_id, "project": folder.name}
    text = build_description(folder, use_gemini=use_gemini, generator=generator) if describe else None
    if dry_run:
        result.update(dry_run=True, description=text, captions=str(folder / "narration.srt") if captions else None,
                      language=language)
        return result
    service = service or youtube.get_service(interactive=True)
    if describe or language:
        youtube.update_snippet(video_id, service=service, description=text, language=language)
        result["snippet"] = {"description": describe, "language": language}
    if captions:
        result["captions"] = youtube.upload_captions(video_id, folder / "narration.srt", service=service,
                                                     replace=replace)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill captions, language and description of a published video.")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--captions", action="store_true", help="upload narration.srt as English captions")
    parser.add_argument("--language", action="store_true", help="set defaultLanguage/defaultAudioLanguage to en")
    parser.add_argument("--description", action="store_true", help="rebuild the description from the template")
    parser.add_argument("--replace-captions", action="store_true", help="replace an existing English track")
    parser.add_argument("--no-gemini", action="store_true", help="no Gemini call: logline + scene titles")
    parser.add_argument("--dry-run", action="store_true", help="print what would change; touch nothing on YouTube")
    args = parser.parse_args(argv)
    if not (args.captions or args.language or args.description):
        parser.error("choose at least one of --captions, --language, --description")
    from uploader.youtube import YouTubeAuthError
    try:
        result = run(args.video_id, captions=args.captions, language=args.language, describe=args.description,
                     dry_run=args.dry_run, use_gemini=not args.no_gemini, replace=args.replace_captions)
    except (LookupError, FileNotFoundError, YouTubeAuthError, archive.ArchiveError, ValueError) as exc:
        print(f"Not done: {exc}", file=sys.stderr)
        return 1
    if result.get("dry_run"):
        print(f"Dry run for {args.video_id} ({result['project']}):")
        if result.get("description") is not None:
            print("--- description ---\n" + result["description"])
        if result.get("captions"):
            print(f"captions: would upload {result['captions']}")
        if result.get("language"):
            print("language: would set defaultLanguage/defaultAudioLanguage = en")
        return 0
    print(f"Done for {args.video_id}: " + ", ".join(
        part for part in (("language set" if args.language else ""), ("description updated" if args.description else ""),
                          (f"captions {result['captions']['action']}" if args.captions else "")) if part))
    return 0


if __name__ == "__main__":
    sys.exit(main())
