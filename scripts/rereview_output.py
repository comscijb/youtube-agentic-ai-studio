"""Run one live editorial review of the current output/ without re-promoting.

Run:  python -m scripts.rereview_output --confirm-live

Re-runs the deterministic review, asks the editor once (findings are screened
against the deterministic facts as in Phase 2.2.1), clears
``editorial_review_stale``, writes narrative_review.json atomically and
invalidates any review_approval.json. On failure nothing is changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from agents import gemini_client
from agents.narrative import (
    atomic_write_json,
    deterministic_review,
    load_json,
    merge_editorial_review,
    stable_hash,
)

KEPT_KEYS = ("editorial_edits", "addressed_editorial_findings", "semantic_revisions",
             "promoted_from_benchmark", "pre_revision_status")


def rereview(output: Path) -> dict:
    from agents.scriptwriter import _editorial_review

    names = ("script.json", "story_brief.json", "character_bible.json", "story_outline.json")
    artifacts = [load_json(output / name) for name in names]
    missing = [name for name, value in zip(names, artifacts) if not value]
    if missing:
        raise FileNotFoundError(f"missing artifacts in {output}: {', '.join(missing)}")
    script, brief, bible, outline = artifacts
    previous = load_json(output / "narrative_review.json") or {}
    target = float(script.get("target_duration_minutes") or brief.get("target_duration_minutes") or 15)
    report = deterministic_review(script, target, outline, previous.get("repair_attempts"), brief)
    review = merge_editorial_review(report, _editorial_review(script, brief, bible, outline, report), bible)
    for key in KEPT_KEYS:
        if key in previous:
            review[key] = previous[key]
    review["editorial_review_stale"] = False
    review["source_hash"] = stable_hash(script)
    return review


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Live editorial re-review of output/.")
    parser.add_argument("--confirm-live", action="store_true",
                        help="Required acknowledgement that this performs one live Gemini call.")
    parser.add_argument("--output-dir", default=config.OUTPUT_DIR)
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required; no API call was made")
    output = Path(args.output_dir)
    gemini_client.clear_call_records()
    try:
        review = rereview(output)
    except Exception as exc:
        print(f"Re-review failed; nothing was changed ({exc.__class__.__name__}: {exc})", file=sys.stderr)
        print("Retry with: python -m scripts.rereview_output --confirm-live", file=sys.stderr)
        print(gemini_client.usage_line(), file=sys.stderr)
        return 1
    atomic_write_json(output / "narrative_review.json", review)
    approval = output / "review_approval.json"
    if approval.is_file():
        approval.unlink()
        print("Previous review approval invalidated: complete the review checklist again.")
    print(f"Narrative status: {review['status']} "
          f"({len(review['major_issues'])} major, {len(review['blocking_issues'])} blocking, "
          f"{len(review.get('discarded_editorial_findings', []))} discarded findings)")
    print(gemini_client.usage_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
