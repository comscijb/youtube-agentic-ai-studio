"""Tell exactly why the YouTube data is blocked and what to do (Phase 4.1-A). Read-only; never prints a token.

Run:  python -m scripts.analytics_doctor          (--json for the raw report)
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from uploader import analytics_doctor
    report = analytics_doctor.diagnose()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if args.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        print("\n".join(analytics_doctor.lines(report)))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
