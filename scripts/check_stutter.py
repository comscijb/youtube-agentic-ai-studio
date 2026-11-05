"""Stutter check over a finished narration (Phase 3.9.5-B), to compare with what is heard.

Run:  python -m scripts.check_stutter --project <slug> [--out report.json]

Cuts narration.mp3 into sentences by narration_words.json, transcribes each and lists every hit with its timestamp.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import config
from agents.narrative import load_json
from audio import assembly
from audio.qa import stutter


def run(project: Path) -> dict:
    saved = load_json(project / "narration_words.json") or {}
    samples = assembly.decode(project / "narration.mp3", 16000)
    speed = float((saved.get("voice") or {}).get("speed", 1.0))
    return stutter.scan_narration(samples, saved.get("words", []), speed=speed)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    report = run(Path(config.PROJECTS_DIR) / args.project)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    say = lambda line: print(str(line).encode("ascii", "replace").decode("ascii"), flush=True)
    say(f"{report['sentences']} sentences, {len(report['hits'])} hits: {dict(Counter(hit['kind'] for hit in report['hits']))}")
    for hit in report["hits"]:
        say(f"{hit['at']:8.2f} s  {hit['kind']:<10} {hit['word']!r:<24} {hit['sentence'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
