"""Archive a published project from the SSD to the HDD archive.

Run:  python -m scripts.archive_project --project <slug> --dry-run
      python -m scripts.archive_project --project <slug>
      python -m scripts.archive_project --project <slug> --force --reason "..."   # not published

Moves the project's own files to ARCHIVE_ROOT/<slug>/ (video, images, audio,
captions, text, manifests, logs; unknown files in other/), copies the shared
music track and style anchors it used, deletes the regenerable caches
(--keep-cache to keep them) and leaves a stub with project.json and ARCHIVED.txt.
"""
from __future__ import annotations

import argparse
import sys

from studio import archive, projects


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Archive a published project to the HDD.")
    parser.add_argument("--project", required=True, metavar="SLUG")
    parser.add_argument("--dry-run", action="store_true", help="list every action; change nothing")
    parser.add_argument("--force", action="store_true", help="archive although the ledger says it is not published")
    parser.add_argument("--reason", default="", help="required with --force; recorded in the archive manifest")
    parser.add_argument("--keep-cache", action="store_true", help="keep .imagegen_cache, .tts_cache, ... on the SSD")
    parser.add_argument("--target-root", help=f"archive root (default: {archive.root()})")
    args = parser.parse_args(argv)
    try:
        project = projects.path_for(args.project)
        result = archive.archive_project(project, target_root=args.target_root, force=args.force,
                                         reason=args.reason, keep_cache=args.keep_cache, dry_run=args.dry_run)
    except (archive.ArchiveError, projects.ProjectNotFound) as exc:
        print(f"Not archived: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        counts = {}
        for action in result["actions"]:
            counts[action["action"]] = counts.get(action["action"], 0) + 1
        for action in result["actions"]:
            where = action.get("archive", "(deleted)")
            print(f"  {action['action']:<12} {action['size'] / 2**20:8.1f} MB  {action['source']}  ->  {where}")
        print(f"\nTarget: {result['target']}")
        print("Dry run: " + ", ".join(f"{count} {name}" for name, count in counts.items())
              + f"; {result['payload_bytes'] / 2**20:.0f} MB to archive. Nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
