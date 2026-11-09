"""Projects from the command line.

Run:  python -m scripts.project list
      python -m scripts.project show <slug>
      python -m scripts.project open <slug>     # make it the active project
      python -m scripts.project new --brief-file briefs/ai-companion.md [--minutes 12]
"""
from __future__ import annotations

import argparse
import sys

from studio import projects

BADGES = {"done": "✓", "stale": "↻", "partial": "◐", "missing": "·", "skipped": "–"}


def _line(item: dict) -> str:
    return f"  {BADGES.get(item['status'], '?')} {item['label']:<13} {item['status']:<8} {item['detail']}"


def new_from_brief(path: str, minutes: float | None = None) -> int:
    """Phase 3.7-A: a new active project holding premise_brief.md/json; Story Development then follows it."""
    from agents import premise_brief
    try:
        brief = premise_brief.parse(path=path)
    except (OSError, premise_brief.BriefError) as exc:
        print(f"Brief not usable: {exc}", file=sys.stderr)
        return 1
    if brief.get("target_minutes") and minutes and float(minutes) != brief["target_minutes"]:
        print(f"The brief asks for {brief['target_minutes']:g} minutes; it wins over --minutes {minutes:g}.")
    target = float(brief.get("target_minutes") or minutes or 12)          # the brief has priority
    import config
    low, high = config.EPISODE_MINUTES
    if not low <= target <= high:
        print(f"Episodes target {low:g}-{high:g} minutes (Target minutes in the brief, or --minutes).", file=sys.stderr)
        return 1
    brief["target_minutes"] = target
    folder = projects.create(brief.get("working_title", ""), video_type="normal", target_duration=target)
    premise_brief.save(folder, brief)
    projects.set_active(folder.name)
    provided = [key for key in premise_brief.FIELDS if key in brief]
    print(f"Project {folder.name} created from {path} ({len(brief.get('raw', ''))} characters).")
    print(f"Brief fields: {', '.join(provided) or 'free text only'}")
    print("Next: python gui.py (Generate → the brief is used automatically) or python pipeline.py --resume")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="List, inspect and open projects.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("show", "open"):
        sub.add_parser(name).add_argument("slug")
    new = sub.add_parser("new", help="create a project from a premise brief (.md / .txt / .json)")
    new.add_argument("--brief-file", required=True)
    new.add_argument("--minutes", type=float, help="target duration (default: the brief's, else 12)")
    args = parser.parse_args(argv)
    if args.command == "new":
        return new_from_brief(args.brief_file, args.minutes)
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
