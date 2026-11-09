"""Crop thin paper margins in an existing project (Phase 3.9.3-E.5).

Run:  python -m scripts.fix_borders --project <slug> --dry-run
      python -m scripts.fix_borders --project <slug>
      python -m scripts.fix_borders --project <slug> --references-only

Looks at the shots, the approved references and the thumbnail background. A margin of at most 5 % a
side is cropped (the original stays in history/ next to the image, and the manifest records the pixels
removed); a deeper one is listed for regeneration. A rendered video goes stale because the images are
newer. Also gives the visual bible's locations their interior flag.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from studio import projects
from video.qa import borders


def targets(project: Path, references_only: bool = False) -> list[Path]:
    found = [path for folder in ("location_refs", "character_refs") for path in sorted((project / folder).glob("*.png"))]
    if references_only:
        return found
    shots = (load_json(project / "shot_list.json") or {}).get("shots", [])
    found += [project / str(shot["image_path"]).replace("\\", "/") for shot in shots if shot.get("image_path")]
    found.append(project / "thumbnail_bg.jpg")
    return [path for path in dict.fromkeys(found) if path.is_file()]


def _record(project: Path, stem: str, removed: dict) -> None:
    path = project / "asset_manifest.json"
    manifest = load_json(path)
    if not manifest:
        return
    for item in manifest.get("generated_assets", []):
        if item.get("job_id") == stem or Path(str(item.get("local_path", ""))).stem == stem:
            item["border_cropped"] = removed
    atomic_write_json(path, manifest)


def fix_image(project: Path, path: Path, *, dry_run: bool = False, stamp: str = "") -> dict | None:
    """None when clean; else {"image", "action": "cropped"|"regenerate", "sides", "max_share", "removed"}."""
    with Image.open(path) as source:
        image = source.convert("RGB")
    report = borders.detect(image)
    verdict = borders.verdict(report)
    if verdict == "clean":
        return None
    row = {"image": str(path.relative_to(project)), "sides": report["marked"], "max_share": report["max_share"]}
    if verdict == "regenerate":
        return {**row, "action": "regenerate"}
    cleaned, removed = borders.crop(image, report)
    if not dry_run:
        history = path.parent / "history"
        history.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, history / f"{path.stem}.border-{stamp or 'original'}{path.suffix}")
        cleaned.save(path, **({"quality": 93} if path.suffix.lower() in (".jpg", ".jpeg") else {}))
        _record(project, path.stem, removed)
    return {**row, "action": "cropped", "removed": removed}


def fix_project(project: Path, *, dry_run: bool = False, references_only: bool = False) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = [row for row in (fix_image(project, path, dry_run=dry_run, stamp=stamp)
                            for path in targets(project, references_only)) if row]
    vb = load_json(project / vbible.FILE_NAME)
    if vb and not dry_run:
        atomic_write_json(project / vbible.FILE_NAME, vbible.backfill_interior(vb))
    video = next((name for name in ("final_video.mp4", "final_short.mp4") if (project / name).is_file()), None)
    cropped = [row for row in rows if row["action"] == "cropped"]
    return {"cropped": cropped, "regenerate": [row for row in rows if row["action"] == "regenerate"],
            "video_stale": bool(video and cropped and not dry_run), "dry_run": dry_run}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dry-run", action="store_true", help="list what would change; write nothing")
    parser.add_argument("--references-only", action="store_true", help="only the approved references")
    args = parser.parse_args(argv)
    try:
        project = projects.path_for(args.project)
    except projects.ProjectNotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    report = fix_project(project, dry_run=args.dry_run, references_only=args.references_only)
    verb = "would crop" if args.dry_run else "cropped"
    for row in report["cropped"]:
        print(f"{verb}: {row['image']} ({', '.join(row['sides'])}; {row['removed']['left']}/{row['removed']['top']}/"
              f"{row['removed']['right']}/{row['removed']['bottom']} px)")
    for row in report["regenerate"]:
        print(f"needs regeneration: {row['image']} ({row['max_share']:.1%} margin)")
    print(f"{len(report['cropped'])} {verb}, {len(report['regenerate'])} to regenerate"
          + ("; the rendered video is now stale" if report["video_stale"] else ""))
    (project / "debug").mkdir(exist_ok=True)
    if not args.dry_run:
        (project / "debug" / "fix_borders.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
