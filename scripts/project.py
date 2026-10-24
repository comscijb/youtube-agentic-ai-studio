"""Projects from the command line.

Run:  python -m scripts.project list
      python -m scripts.project show <slug>
      python -m scripts.project open <slug>     # make it the active project
"""
from __future__ import annotations

import argparse
import sys

from studio import projects

BADGES = {"done": "✓", "stale": "↻", "partial": "◐", "missing": "·", "skipped": "–"}


def _line(item: dict) -> str:
    return f"  {BADGES.get(item['status'], '?')} {item['label']:<13} {item['status']:<8} {item['detail']}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="List, inspect and open projects.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("show", "open"):
        sub.add_parser(name).add_argument("slug")
    args = parser.parse_args(argv)
    if args.command == "list":
        paths = projects.all_paths()
        if not paths:
            print("No projects yet. Migrate the legacy output/ with: python -m scripts.migrate_output_to_project")
            return 0
        active = projects.active_slug()
        for path in paths:
            info = projects.summary(path)
            step = info["next_step"]
            badges = " ".join(BADGES.get(item["status"], "?") for item in info["stages"])
            print(f"{'*' if info['slug'] == active else ' '} {info['slug']}\n"
                  f"    {info['title']} · updated {str(info['updated'])[:16]} · {badges} · next: "
                  + (f"{step['label']} ({step['status']})" if step else "nothing — ready"))
        return 0
    try:
        path = projects.path_for(args.slug)
    except projects.ProjectNotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.command == "open":
        projects.set_active(args.slug)
        print(f"Active project: {args.slug}")
    info = projects.summary(path)
    print(f"{info['title']}  ({info['slug']})")
    for item in info["stages"]:
        print(_line(item))
    step = info["next_step"]
    print(f"Next step: {step['label']} — {step['status']}" if step else "Next step: nothing — ready to publish")
    return 0


if __name__ == "__main__":
    sys.exit(main())
