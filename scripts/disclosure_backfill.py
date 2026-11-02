"""Backfill ``contains_synthetic_media`` and its reason into the publication ledger (Phase 3.9-F).

    python -m scripts.disclosure_backfill [--dry-run]
"""
from __future__ import annotations

import argparse

from uploader import publications


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    args = parser.parse_args(argv)
    rows = publications.backfill_disclosure(dry_run=args.dry_run)
    for row in rows:
        value = "" if "value" not in row else ("true" if row["value"] else "false")
        print(f"{row['video_id']:<12} {row['kind']:<8} {row['action']:<10} {value:<5} {row['reason']}"
              .encode("ascii", "replace").decode("ascii"))
    return 1 if any(row["action"] == "missing" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
