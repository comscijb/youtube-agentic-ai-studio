"""Opt-in isolated Private Gravity long-form benchmark (uses live Gemini quota)."""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from agents import gemini_client
from agents.researcher import research_topic
from agents.scriptwriter import write_script


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate a narrative-only benchmark without overwriting production output."
    )
    parser.add_argument(
        "--confirm-live", action="store_true",
        help="Required acknowledgement that this performs live Gemini requests.",
    )
    parser.add_argument(
        "--premise", default="The Man Who Needed Everyone to Envy Him",
    )
    parser.add_argument("--minutes", type=float, default=12)
    parser.add_argument("--output-root", default="output/benchmarks")
    parser.add_argument(
        "--resume", metavar="DIR",
        help="Reuse valid cached artifacts (brief, bible, outline) in an existing benchmark directory.",
    )
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required; no API call was made")
    if not 12 <= args.minutes <= 20:
        parser.error("--minutes must be between 12 and 20")

    if args.resume:
        destination = Path(args.resume)
        if not destination.is_dir():
            parser.error(f"--resume directory does not exist: {destination}")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path(args.output_root) / f"private-gravity-{stamp}"
        destination.mkdir(parents=True, exist_ok=False)
    config.OUTPUT_DIR = str(destination)
    gemini_client.clear_call_records()
    started = time.monotonic()
    try:
        brief = research_topic(
            config.CHANNEL_DESCRIPTION,
            topic_override=args.premise,
            narrative_type="realistic_psychological_fiction",
            target_duration_minutes=args.minutes,
            reuse_cached=bool(args.resume),
        )
        write_script(
            brief, video_type="normal", output_dir=str(destination),
            target_duration_minutes=args.minutes, narrative_review=True,
            force=not args.resume,
        )
    except Exception:
        print(f"Benchmark failed; artifacts and debug output are in: {destination}")
        print("Resume with: python -m scripts.regenerate_narrative_benchmark --confirm-live "
              f"--minutes {args.minutes:g} --resume {destination.as_posix()}")
        raise
    finally:
        print_timing_table(gemini_client.call_records(), time.monotonic() - started)
    print(f"Benchmark saved in isolated directory: {destination}")
    print("No stock, TTS, music, rendering, upload, commit, or push was performed.")


def timing_rows(records: list[dict]) -> list[dict]:
    """Aggregate per-call records into one row per stage, in first-seen order."""
    rows: dict[str, dict] = {}
    for record in records:
        row = rows.setdefault(record["stage"], {
            "stage": record["stage"], "calls": 0, "seconds": 0.0,
            "prompt_chars": 0, "models": [], "outcome": "",
        })
        row["calls"] += 1
        row["seconds"] += float(record.get("elapsed_seconds", 0))
        row["prompt_chars"] = max(row["prompt_chars"], int(record.get("prompt_chars", 0)))
        if record["model"] not in row["models"]:
            row["models"].append(record["model"])
        row["outcome"] = record["outcome"]
    return list(rows.values())


def print_timing_table(records: list[dict], wall_seconds: float) -> None:
    rows = timing_rows(records)
    print("\nPer-stage Gemini timing (stages reused from cache make no calls):")
    if not rows:
        print("  (no Gemini calls)")
    else:
        width = max(len("stage"), *(len(row["stage"]) for row in rows))
        print(f"  {'stage':<{width}}  calls  seconds  max_prompt_chars  last_outcome  models")
        for row in rows:
            print(f"  {row['stage']:<{width}}  {row['calls']:>5}  {row['seconds']:>7.1f}  "
                  f"{row['prompt_chars']:>16}  {row['outcome']:<12}  {', '.join(row['models'])}")
    print(f"  total wall time: {wall_seconds:.1f}s")


if __name__ == "__main__":
    main()
