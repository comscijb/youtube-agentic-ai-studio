"""New-format Shorts for an episode that is already published (Phase 4.3-H).

Run:  python -m scripts.shorts_backfill --project <slug> [--formats hook,dilemma,line] [--plan-only] [--dry-run]

* restores ONLY the files the Shorts need from the archive (screenplay, brief and outline, narration words, the shot
  list and the images it uses, the chosen thumbnail background) through ``studio.archive.restore_project(only=...)``;
  the project stays archived and the archive is untouched; files already on the SSD are kept;
* proposes the scripts (one Gemini call + the swipe simulation), renders every included Short (voice, mix, 9:16
  edit) and records the episode URL from the ledger in the plan, so the Shorts wait in the approval gate
  (Projeto → Shorts) with their own hashes. Nothing is uploaded here.

``--dry-run`` lists what would be restored and the episode it would link, without copying or calling anything.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TEXT_FILES = ("script.json", "story_brief.json", "story_outline.json", "research.json", "shot_list.json",
              "narration_words.json", "visual_bible.json", "thumbnails/thumbnails.json")


def _manifest_paths(slug: str) -> dict[str, dict]:
    """{project-relative posix path: manifest item} for the archived files of ``slug``."""
    from agents.narrative import load_json
    from studio import archive, projects
    stub = projects.root() / slug
    info = projects.load(stub) if (stub / "project.json").is_file() else {}
    target = Path(info.get("archive_path") or archive.root() / slug)
    manifest = load_json(target / archive.MANIFEST) or {}
    found = {}
    for item in manifest.get("files", []):
        if item.get("action") != "move":
            continue
        relative = Path(item["original"]).relative_to(Path(manifest["source"])).as_posix()
        found[relative] = item
    return found


def needed(slug: str, shot_list: dict | None = None, thumbnails: dict | None = None) -> list[str]:
    """The project-relative files the Shorts need: the text files, then (once the shot list is known) the shot images
    it resolves to and the selected thumbnail background. Never audio, video, history, depth or the old Shorts."""
    from agents.shot_list import resolve_image
    found = list(TEXT_FILES)
    shots = (shot_list or {}).get("shots", [])
    for shot in shots:
        image = resolve_image(shots, shot)
        if image and not Path(image).is_absolute():
            found.append(Path(str(image).replace("\\", "/")).as_posix())
    selected = (thumbnails or {}).get("selected")
    if selected:
        found.append(f"thumbnails/background_{selected}.jpg")
    return list(dict.fromkeys(found))


def restore(slug: str, *, dry_run: bool = False, log=print) -> dict:
    """Copy the needed archived files that are missing on the SSD; {"restored", "kept", "missing"}."""
    from agents.narrative import load_json
    from studio import archive, projects
    project = projects.root() / slug
    archived = _manifest_paths(slug)
    report = {"restored": [], "kept": [], "missing": []}

    def bring(names):
        wanted = [name for name in names if not (project / name).is_file()]
        report["kept"] += [name for name in names if (project / name).is_file()]
        available = [name for name in wanted if name in archived]
        report["missing"] += [name for name in wanted if name not in archived]
        if available and not dry_run:
            archive.restore_project(slug, only=available, log=lambda *_: None)
        report["restored"] += available

    bring(list(TEXT_FILES))
    shot_list = load_json(project / "shot_list.json") or {}
    thumbnails = load_json(project / "thumbnails" / "thumbnails.json") or {}
    if dry_run and not shot_list and "shot_list.json" in archived:      # read it from the archive for the listing
        shot_list = load_json(Path(archive.root()) / slug / archived["shot_list.json"]["archive"]) or {}
        thumbnails = load_json(Path(archive.root()) / slug / archived.get("thumbnails/thumbnails.json", {}).get("archive", "-")) or {}
    bring([name for name in needed(slug, shot_list, thumbnails) if name not in TEXT_FILES])
    log(f"   arquivos: {len(report['restored'])} {'a restaurar' if dry_run else 'restaurados'}, "
        f"{len(report['kept'])} já no SSD, {len(report['missing'])} fora do arquivo")
    return report


def episode_of(slug: str, ledger: dict | None = None) -> dict | None:
    """The published episode of ``slug`` in the ledger (never one of its Shorts)."""
    from uploader import publications
    ledger = ledger or publications.load_ledger()
    return next((item for item in ledger.get("publications", []) if item.get("project") == slug
                 and not item.get("parent_video_id") and item.get("kind", "episode") != "short" and item.get("video_id")), None)


def run(slug: str, formats: list[str] | None = None, *, plan_only: bool = False, dry_run: bool = False,
        generator=None, simulator=None, render=None, log=print) -> dict:
    from agents.narrative import atomic_write_json, load_json
    from studio import projects
    from video import short_formats
    episode = episode_of(slug)
    if episode is None:
        raise LookupError(f"{slug}: no published episode in the ledger")
    url = episode.get("url") or f"https://www.youtube.com/watch?v={episode['video_id']}"
    chosen = formats or short_formats.allocate()
    unknown = [name for name in chosen if name not in short_formats.FORMATS]
    if unknown:
        raise ValueError(f"unknown formats: {unknown} (use {', '.join(short_formats.ORDER)})")
    log(f"Episódio: {episode.get('title', '')} → {url}")
    report = restore(slug, dry_run=dry_run, log=log)
    if dry_run:
        return {"episode_url": url, "restore": report}
    project = projects.root() / slug
    plan = short_formats.build(project, formats=chosen, generator=generator, simulator=simulator)
    plan["backfill"] = {"episode_video_id": episode["video_id"], "episode_url": url}
    atomic_write_json(project / "shorts" / "shorts_plan.json", plan)
    for item in plan["segments"]:
        log(f"   {item['id']} {item['format']}: {'pronto' if item['status'] == 'ready' else 'revisar — ' + '; '.join(item['reasons'])}")
    rendered = []
    if not plan_only:
        if render is None:
            from video import shorts_render
            render = lambda folder: shorts_render.render_all(folder, progress=log)
        rendered = render(project)
    return {"episode_url": url, "restore": report, "plan": load_json(project / "shorts" / "shorts_plan.json"), "rendered": rendered}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="New-format Shorts for a published episode.")
    parser.add_argument("--project", required=True, metavar="SLUG")
    parser.add_argument("--formats", default="", help="comma list: hook,dilemma,line,countdown,pov (default: the weights)")
    parser.add_argument("--plan-only", action="store_true", help="scripts and checks only, no render")
    parser.add_argument("--dry-run", action="store_true", help="list what would be restored; no copy, no Gemini")
    args = parser.parse_args(argv)
    formats = [name.strip() for name in args.formats.split(",") if name.strip()] or None
    try:
        run(args.project, formats, plan_only=args.plan_only, dry_run=args.dry_run)
    except (LookupError, ValueError, FileNotFoundError) as exc:
        print(f"Backfill stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
