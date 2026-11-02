"""Repair cp1252-mis-decoded UTF-8 (mojibake) in output/*.json.

Run:  python -m scripts.repair_text_encoding            # dry run: shows every change
      python -m scripts.repair_text_encoding --apply    # writes, keeping <file>.bak

Only strings containing mojibake sequences ("â€™", "Ã©", ...) are touched,
run by run; correct text is never rewritten.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import config
from studio import projects
from agents.narrative import atomic_write_json, has_mojibake, repair_mojibake


def repair_value(value, path: str = "$", changes: list | None = None):
    """Return (repaired copy, [(json path, before, after)])."""
    changes = [] if changes is None else changes
    if isinstance(value, str):
        if has_mojibake(value):
            fixed = repair_mojibake(value)
            if fixed != value:
                changes.append((path, value, fixed))
                return fixed, changes
        return value, changes
    if isinstance(value, list):
        return [repair_value(item, f"{path}[{index}]", changes)[0]
                for index, item in enumerate(value)], changes
    if isinstance(value, dict):
        return {key: repair_value(item, f"{path}.{key}", changes)[0]
                for key, item in value.items()}, changes
    return value, changes


def _excerpt(before: str, after: str) -> tuple[str, str]:
    start = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b), 0)
    lo = max(0, start - 25)
    return before[lo:start + 35], after[lo:start + 35]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Repair mojibake in output/*.json.")
    parser.add_argument("--dir", default=None, help="explicit folder (overrides the project)")
    projects.add_argument(parser)
    parser.add_argument("--apply", action="store_true", help="Write the repairs (default: dry run)")
    args = parser.parse_args(argv)
    total = 0
    folder = projects.cli_output(args, "dir")
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"skip {path.name}: {exc.__class__.__name__}")
            continue
        repaired, changes = repair_value(data)
        if not changes:
            continue
        total += len(changes)
        print(f"{path.name}: {len(changes)} string(s)")
        for json_path, before, after in changes:
            old, new = _excerpt(before, after)
            print(f"  {json_path}\n    - {ascii(old)}\n    + {new}")
        if args.apply:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
            atomic_write_json(path, repaired)
    if not total:
        print("No mojibake found.")
    elif not args.apply:
        print(f"\nDry run: {total} string(s) would change. Re-run with --apply to write them.")
    else:
        print(f"\nRepaired {total} string(s); originals kept as *.json.bak. Re-run narration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
