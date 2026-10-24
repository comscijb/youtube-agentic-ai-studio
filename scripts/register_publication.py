"""Register a manual YouTube upload in the publication ledger.

Run:  python -m scripts.register_publication --video-id <id> --project <slug>
      (or --run-dir <dir> for a folder outside projects/)
Reads title, premise, theme and angle from the run's artifacts and creates a
pending record; the next sync decides whether it is published.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from studio import projects
from uploader.publications import load_ledger, record_upload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--run-dir", help="episode folder (default: the --project folder)")
    projects.add_argument(parser)
    parser.add_argument("--video-path", help="Uploaded file (default: <run-dir>/final_video.mp4)")
    parser.add_argument("--scheduled-publish-at", help="RFC 3339 publishAt, if scheduled")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", args.video_id):
        parser.error("--video-id must be a YouTube video ID (not a URL)")
    if not args.run_dir and not args.project:
        parser.error("pass --run-dir or --project")
    run = projects.cli_output(args, "run_dir")
    if not (run / "story_brief.json").exists() and not (run / "research.json").exists():
        parser.error(f"{run} has no story_brief.json or research.json")
    if not (run / "script.json").exists():
        parser.error(f"{run} has no script.json")
    already = any(item.get("video_id") == args.video_id for item in load_ledger()["publications"])
    record = record_upload(
        args.video_id, run, video_path=args.video_path,
        scheduled_publish_at=args.scheduled_publish_at,
        extra={"registered_manually": True},
    )
    if already:
        print(f"{args.video_id} is already in the ledger (state: {record['state']}).")
    else:
        print(f"Registered {args.video_id}: {record['title']} (state: {record['state']}).")
        print("Run python -m scripts.sync_publications to check its publication state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
