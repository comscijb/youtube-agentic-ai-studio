"""Opt-in isolated Private Gravity long-form benchmark (uses live Gemini quota)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.researcher import research_topic
from agents.scriptwriter import write_script


def main() -> None:
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
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required; no API call was made")
    if not 12 <= args.minutes <= 20:
        parser.error("--minutes must be between 12 and 20")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(args.output_root) / f"private-gravity-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)
    config.OUTPUT_DIR = str(destination)
    brief = research_topic(
        config.CHANNEL_DESCRIPTION,
        topic_override=args.premise,
        narrative_type="realistic_psychological_fiction",
        target_duration_minutes=args.minutes,
    )
    write_script(
        brief, video_type="normal", output_dir=str(destination),
        target_duration_minutes=args.minutes, narrative_review=True, force=True,
    )
    print(f"Benchmark saved in isolated directory: {destination}")
    print("No stock, TTS, music, rendering, upload, commit, or push was performed.")


if __name__ == "__main__":
    main()
