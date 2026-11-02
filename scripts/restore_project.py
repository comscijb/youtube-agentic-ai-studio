"""Copy an archived project back to the SSD (for a re-render or a Short); the archive stays intact.

Run:  python -m scripts.restore_project --project <slug>
"""
from __future__ import annotations

import argparse
import sys

from studio import archive


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Restore an archived project to projects/<slug>.")
    parser.add_argument("--project", required=True, metavar="SLUG")
    parser.add_argument("--archive-path", help="archive folder (default: from the stub's project.json)")
    args = parser.parse_args(argv)
    try:
        archive.restore_project(args.project, archive_path=args.archive_path)
    except (archive.ArchiveError, OSError) as exc:
        print(f"Not restored: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
