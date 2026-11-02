"""Move the legacy ``output/`` episode into its own project folder.

Run:  python -m scripts.migrate_output_to_project --dry-run
      python -m scripts.migrate_output_to_project

Everything in ``output/`` except ``benchmarks/``, archives and the channel-wide
calibration renders (``style_calibration/``, ``imagegen_calibration/``) moves to
``projects/<date>-<title-slug>/`` (artifacts, images, references, caches,
checkpoints, debug). Nothing is deleted: items are moved, JSON paths that start
with ``output/`` are rewritten to the new folder, ``project.json`` is written,
the project becomes active, publication-ledger records of this run get its
slug, and ``output/MIGRATED.txt`` points to the new location.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import config
from agents.narrative import load_json
from studio import projects

KEEP = {"benchmarks", "MIGRATED.txt", "style_calibration", "imagegen_calibration"}
POINTER = "MIGRATED.txt"


def _kept(name: str) -> bool:
    return name in KEEP or name.lower().startswith("archive")


def plan(source: Path) -> dict:
    research = load_json(source / "story_brief.json") or load_json(source / "research.json") or {}
    script = load_json(source / "script.json") or {}
    title = script.get("title") or research.get("video_title") or ""
    title = title.replace(" #Shorts", "").strip()
    dated = [path for path in (source / "story_brief.json", source / "research.json", source / "script.json")
             if path.is_file()]
    date = datetime.fromtimestamp(min(path.stat().st_mtime for path in dated)).date().isoformat() \
        if dated else None
    items = sorted(path for path in source.iterdir() if not _kept(path.name)) if source.is_dir() else []
    return {"title": title, "date": date, "slug": projects.slugify(title, date), "items": items,
            "kept": sorted(path.name for path in source.iterdir() if _kept(path.name)) if source.is_dir() else [],
            "video_type": script.get("video_type") or ("shorts" if "#Shorts" in str(script.get("title")) else "normal"),
            "target_duration_minutes": script.get("target_duration_minutes")
            or research.get("target_duration_minutes")}


def _rewrite(value, old_prefixes: tuple, new_prefix: str, counter: list):
    if isinstance(value, str):
        for prefix in old_prefixes:
            if value.startswith(prefix):
                counter[0] += 1
                return new_prefix + value[len(prefix):]
        return value
    if isinstance(value, list):
        return [_rewrite(item, old_prefixes, new_prefix, counter) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, old_prefixes, new_prefix, counter) for key, item in value.items()}
    return value


def rewrite_paths(folder: Path, source: Path) -> int:
    """Rewrite JSON string values that point into the old folder (top level and debug excluded)."""
    name = source.as_posix().rstrip("/")
    prefixes = (name + "/", name.replace("/", "\\") + "\\")
    new_prefix = folder.as_posix() + "/"
    total = 0
    for path in folder.rglob("*.json"):
        if ".imagegen_cache" in path.parts or ".stock_cache" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        counter = [0]
        updated = _rewrite(data, prefixes, new_prefix, counter)
        if counter[0]:
            path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            total += counter[0]
    return total


def tag_ledger(source: Path, slug: str, folder: Path) -> tuple[int, str | None]:
    """Give this run's publication records the project slug; returns (count, last video id)."""
    from uploader import publications
    ledger = publications.load_ledger()
    tagged, video_id = 0, None
    for record in ledger.get("publications", []):
        if Path(record.get("run_dir", "")).as_posix().rstrip("/") == source.as_posix().rstrip("/") \
                and not record.get("project"):
            record["project"] = slug
            record["run_dir"] = folder.as_posix()
            tagged, video_id = tagged + 1, record.get("video_id")
    if tagged:
        publications.save_ledger(ledger)
    return tagged, video_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Move output/ into projects/<slug>/.")
    parser.add_argument("--source", default=projects.LEGACY_DIR)
    parser.add_argument("--dry-run", action="store_true", help="show what would move; change nothing")
    parser.add_argument("--slug", help="override the project slug")
    args = parser.parse_args(argv)
    source = Path(args.source)
    if not source.is_dir():
        print(f"Nothing to migrate: {source} does not exist.")
        return 0
    if (source / POINTER).is_file() and not any(source / name for name in ("script.json", "story_brief.json")
                                                 if (source / name).exists()):
        print((source / POINTER).read_text(encoding="utf-8").strip())
        return 0
    info = plan(source)
    slug = args.slug or info["slug"]
    destination = projects.root() / slug
    print(f"Project: {info['title'] or '(untitled)'}")
    print(f"Folder:  {destination}")
    print(f"Move {len(info['items'])} item(s): " + ", ".join(path.name for path in info["items"]))
    print(f"Keep in {source}: " + (", ".join(info["kept"]) or "nothing"))
    if not info["items"]:
        print("Nothing to move.")
        return 0
    if destination.exists() and any(destination.iterdir()):
        print(f"{destination} already exists and is not empty; pass --slug to choose another name.",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print("\nDry run: nothing was moved. Run again without --dry-run to migrate.")
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    for path in info["items"]:
        shutil.move(str(path), str(destination / path.name))
    rewritten = rewrite_paths(destination, source)
    now = datetime.now().astimezone().replace(microsecond=0).isoformat()
    created = f"{info['date']}T00:00:00" if info["date"] else now
    (destination / projects.PROJECT_FILE).write_text(projects.json_dumps({
        "slug": slug, "title": info["title"], "provisional": False, "created": created, "updated": now,
        "video_type": info["video_type"], "target_duration_minutes": info["target_duration_minutes"],
        "skipped": [], "stages": {}, "published_video_id": None, "migrated_from": source.as_posix(),
    }) + "\n", encoding="utf-8")
    tagged, video_id = tag_ledger(source, slug, destination)
    projects.touch(destination, **({"published_video_id": video_id} if video_id else {}))
    projects.set_active(slug)
    (source / POINTER).write_text(
        f"This episode moved to {destination.as_posix()} on {now}.\n"
        f"Open it with: python -m scripts.project open {slug}\n"
        "Benchmarks and archives stay in this folder.\n", encoding="utf-8")
    print(f"\nMoved {len(info['items'])} item(s) to {destination}; rewrote {rewritten} path(s) in JSON; "
          f"tagged {tagged} publication record(s). Active project: {slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
