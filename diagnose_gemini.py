#!/usr/bin/env python3
"""Explicit, minimal Gemini connectivity diagnostic."""
import json
import sys

from agents.gemini_client import diagnostic_snapshot


def main() -> int:
    print("Gemini connectivity diagnostic (one model-list call + one minimal generation)")
    result = diagnostic_snapshot(run_generation=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("generation") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
