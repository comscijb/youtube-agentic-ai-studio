"""Thumbnail variants for an existing or archived project (Phase 3.6-A).

Run:  python -m scripts.thumbnails --project <slug>            # 3 new variants (ComfyUI + Gemini)
      python -m scripts.thumbnails --project <slug> --restore  # archived: restore only the needed files first
      python -m scripts.thumbnails --project <slug> --select 2 # make variant 2 the uploaded thumbnail.jpg

Writes thumbnails/variant_N.jpg, a 168×94 mobile preview of each and thumbnails/thumbnails.json
(concept, checks, advisory QA hints). Pick one in the GUI (Review → Thumbnails) or with --select;
the others can be uploaded by hand in YouTube Studio's Test & compare (not available in the API).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from studio import archive, projects
from video import thumbnails

NEEDED = ["visual_bible.json", "story_brief.json", "research.json", "script.json", "shot_list.json",
          "character_refs", "location_refs"]


def missing_inputs(output: Path) -> list[str]:
    return [name for name in ("visual_bible.json", "script.json", "character_refs") if not (output / name).exists()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate 3 thumbnail variants for a project.")
    parser.add_argument("--project", required=True, metavar="SLUG")
    parser.add_argument("--restore", action="store_true",
                        help="archived project: restore only the files the thumbnails need")
    parser.add_argument("--select", type=int, help="make variant N the uploaded thumbnail.jpg")
    parser.add_argument("--new-concepts", action="store_true", help="ask Gemini for new concepts")
    args = parser.parse_args(argv)
    output = projects.root() / args.project
    if not (output / projects.PROJECT_FILE).is_file():
        print(f"No project {args.project!r} in {projects.root()}", file=sys.stderr)
        return 1
    if args.select:
        print(f"Selected: {thumbnails.select(output, args.select)}")
        return 0
    if missing_inputs(output):
        if not args.restore:
            print(f"Missing {', '.join(missing_inputs(output))}: the project is archived. Re-run with --restore "
                  "(copies back only what the thumbnails need).", file=sys.stderr)
            return 1
        archive.restore_project(args.project, only=NEEDED)
    from agents import packaging
    from agents import visual_bible as vbible
    from agents.narrative import load_json
    from video.imagegen_comfyui import ComfyClient, ComfyUnavailable, ImageGenerator
    from video.qa.layered import LayeredQA
    client = ComfyClient()
    if not client.available():
        print(f"ComfyUI is not reachable at {client.base_url}. Start it and run again.", file=sys.stderr)
        return 2
    brief = load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    concepts = packaging.generate_thumbnail_concepts(
        output, brief=brief, script=load_json(output / "script.json") or {},
        vb=load_json(output / vbible.FILE_NAME) or {}, force=args.new_concepts)
    try:
        index = thumbnails.generate_variants(output, ImageGenerator(output, client), concepts=concepts,
                                             qa=LayeredQA(comfy=client, mode="advisory"))
    except ComfyUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    for item in index["variants"]:
        checks = item["checks"]
        failed = [key for key in ("contrast_ok", "cap_height_ok", "text_clear_of_faces", "head_in_frame")
                  if checks.get(key) is False]
        hints = [issue["type"] for issue in (item.get("qa") or {}).get("issues", []) if issue.get("hint")]
        print(f"   {item['number']}. {item['concept']['composition']:<18} {item['concept']['text']!r:<26} "
              f"contrast {checks['contrast']}:1 — " + ("OK" if not failed else "check: " + ", ".join(failed))
              + (f"; QA hints: {', '.join(hints)}" if hints else ""))
    print(f"Variant {index['selected']} is thumbnail.jpg. Pick another in the GUI (Review → Thumbnails) or "
          f"with --select N; upload the others by hand in YouTube Studio → Test & compare.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
