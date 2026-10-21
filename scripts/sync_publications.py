"""Sync pending uploads with YouTube and ban topics of videos that are now public.

Run:  python -m scripts.sync_publications
Opens the browser for consent when the stored token is missing or lacks the
youtube.readonly scope. Tokens are never printed.
"""
from __future__ import annotations

import argparse

from uploader.publications import load_ledger, sync_publications


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    summary = sync_publications(interactive=True)
    if not summary["checked"]:
        print("No uploads are pending publication.")
    for key in ("published", "failed", "removed", "pending"):
        if summary[key]:
            print(f"{key}: {', '.join(summary[key])}")
    states = {}
    for record in load_ledger()["publications"]:
        states[record.get("state")] = states.get(record.get("state"), 0) + 1
    print("Ledger states: " + (", ".join(f"{k}={v}" for k, v in sorted(states.items())) or "empty"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
