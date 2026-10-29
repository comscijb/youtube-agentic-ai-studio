"""Archive published projects from the SSD to the HDD (Phase 3.4-G).

``archive_project`` moves a published project's own files to
``<ARCHIVE_ROOT>/<slug>/`` (``D:/Private Gravity/Archive`` by default), copies the
shared assets it used (music track, channel style anchors), deletes regenerable
caches, and leaves a stub on the SSD (``project.json`` with status ``archived``
and ``ARCHIVED.txt``) so the GUI still lists it.

Safety: every file is copied to a ``.part`` file, verified by SHA-256 and only
then renamed into place; sources are deleted only after the WHOLE project
verified; ``archive_manifest.json`` records every action, so an interrupted run
resumes and never deletes a source whose archived copy is missing or differs.
An archive folder holding something else is never overwritten (``<slug>__2``).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json
from studio import projects

MANIFEST = "archive_manifest.json"
POINTER = "ARCHIVED.txt"
SPACE_FACTOR = 1.2
CACHES = (".imagegen_cache", ".tts_cache", ".stock_cache", ".checkpoints")
# Explicit home for every known artifact (top-level name -> archive folder).
FILES = {
    "final_video.mp4": "video", "final_short.mp4": "video", "thumbnail.jpg": "video", "thumbnail_bg.jpg": "video",
    "narration.mp3": "audio", "narration_mixed.mp3": "audio",
    "narration.srt": "captions",
    "story_brief.json": "text", "research.json": "text", "character_bible.json": "text",
    "story_outline.json": "text", "script.json": "text", "narrative_review.json": "text",
    "visual_bible.json": "text", "shot_list.json": "text", "shot_list_report.json": "text",
    "review_approval.json": "text", "reference_candidates.json": "text", "project.json": "text",
    "asset_manifest.json": "manifests", "scene_timeline.json": "manifests",
}
FOLDERS = {"images": "images", "character_refs": "images/character_refs",
           "location_refs": "images/location_refs", "debug": "logs/debug"}
# Usage summaries are archived with the logs when a run wrote them.
LOG_FILES = ("gemini_usage.json", "comfyui_usage.json")


class ArchiveError(RuntimeError):
    pass


def root() -> Path:
    return Path(getattr(config, "ARCHIVE_ROOT", r"D:\Private Gravity\Archive"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _long(path) -> str:
    """Windows long-path form for deep or long paths (spaces are fine as-is)."""
    text = str(Path(path).resolve()) if sys.platform == "win32" else str(path)
    if sys.platform == "win32" and len(text) > 240 and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(_long(path), "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def destination_for(relative: Path) -> str:
    """Archive-relative path for a project file; unknown files go to other/."""
    parts = relative.parts
    if parts[0] in FILES and len(parts) == 1:
        return f"{FILES[parts[0]]}/{parts[0]}"
    if parts[0] in FOLDERS:
        return "/".join([FOLDERS[parts[0]], *parts[1:]])
    if parts[0] in LOG_FILES:
        return f"logs/{parts[0]}"
    return "/".join(["other", *parts])


# ── Publication gate ────────────────────────────────────────────────────────

def publication(project: Path) -> dict | None:
    from uploader import publications
    slug = project.name
    for record in publications.load_ledger().get("publications", []):
        same = record.get("project") == slug or Path(record.get("run_dir", "")).resolve() == project.resolve()
        if same and record.get("state") == publications.PUBLISHED:
            return record
    return None


# ── Planning ────────────────────────────────────────────────────────────────

def plan(project, target_root=None) -> dict:
    """Every action without performing any: move / copy / delete-cache, with sizes."""
    project = Path(project)
    actions = []
    for name in CACHES:                       # regenerable: deleted after a verified archive, never archived
        folder = project / name
        if folder.is_dir():
            actions.append({"action": "delete-cache", "source": str(folder),
                            "size": sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())})
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(project)
        if relative.parts[0] in CACHES:
            continue
        elif relative.name in (MANIFEST, POINTER) and len(relative.parts) == 1:
            continue
        elif relative.name.endswith((".part", ".qa-best.jpg")):
            continue
        else:
            actions.append({"action": "move", "source": str(path), "archive": destination_for(relative),
                            "size": path.stat().st_size})
    actions += _shared_copies(project)
    payload = sum(item["size"] for item in actions if item["action"] in ("move", "copy"))
    return {"slug": project.name, "source": str(project),
            "target": str(_target(project, Path(target_root) if target_root else root())),
            "actions": actions, "payload_bytes": payload}


def _shared_copies(project: Path) -> list[dict]:
    """Shared assets the project used: copied, never moved."""
    copies = []
    music = (load_json(project / "asset_manifest.json") or {}).get("music") or {}
    track = music.get("track")
    if track:
        path = Path(getattr(config, "MUSIC_LIBRARY_DIR", "music library")) / track
        if path.is_file():
            copies.append({"action": "copy", "source": str(path), "archive": f"audio/music/{path.name}",
                           "size": path.stat().st_size})
    from agents import style_refs
    used = {slot["id"] for shot in (load_json(project / "shot_list.json") or {}).get("shots", [])
            for slot in shot.get("reference_slots") or [] if slot.get("kind") == "style"}
    for path in map(Path, style_refs.approved_paths()):
        if path.is_file() and (not used or path.stem in used):
            copies.append({"action": "copy", "source": str(path), "archive": f"images/style_refs/{path.name}",
                           "size": path.stat().st_size})
    return copies


def _target(project: Path, archive_root: Path) -> Path:
    """<root>/<slug>, resuming our own archive; another project's folder gets <slug>__2."""
    candidate, number = archive_root / project.name, 2
    while candidate.exists():
        saved = load_json(candidate / MANIFEST) or {}
        same = bool(saved.get("source")) and Path(saved["source"]).resolve() == project.resolve()
        if same or not any(candidate.iterdir()):
            return candidate
        candidate, number = archive_root / f"{project.name}__{number}", number + 1
    return candidate


# ── Execution ───────────────────────────────────────────────────────────────

def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        raise ArchiveError(f"the archive drive for {path} is not reachable")
    return shutil.disk_usage(probe).free


def _youtube_metadata(project: Path, record: dict | None) -> dict:
    script = load_json(project / "script.json") or {}
    record = record or {}
    return {"title": record.get("title") or script.get("title", ""), "description": script.get("description", ""),
            "tags": script.get("tags", []), "video_id": record.get("video_id"), "url": record.get("url"),
            "uploaded_at": record.get("uploaded_at"), "published_at": record.get("published_at")}


def archive_project(project, *, target_root=None, force: bool = False, reason: str = "",
                    keep_cache: bool = False, dry_run: bool = False, log=print) -> dict:
    """Archive one project; returns the archive manifest (or the plan for a dry run)."""
    project = Path(project)
    if not project.is_dir():
        raise ArchiveError(f"no project folder {project}")
    info = projects.load(project)
    if info.get("status") == "archived":
        raise ArchiveError(f"{project.name} is already archived in {info.get('archive_path')}")
    record = publication(project)
    if record is None and not force:
        raise ArchiveError(f"{project.name} is not published according to the publication ledger; "
                           "use --force --reason \"...\" to archive it anyway")
    if force and record is None and not reason.strip():
        raise ArchiveError("--force needs a written --reason")
    work = plan(project, target_root)
    target = Path(work["target"])
    if dry_run:
        return work
    needed = int(work["payload_bytes"] * SPACE_FACTOR)
    free = _free_bytes(target)
    if free < needed:
        raise ArchiveError(f"not enough space on the archive drive: {free / 2**30:.1f} GB free, "
                           f"{needed / 2**30:.1f} GB needed (1.2 × project size)")
    target.mkdir(parents=True, exist_ok=True)
    manifest = load_json(target / MANIFEST) or {}
    manifest.update({"slug": project.name, "source": str(project), "target": str(target),
                     "started_at": manifest.get("started_at") or _now(),
                     "forced": bool(force and record is None), "reason": reason.strip() or None})
    entries = {item["archive"]: item for item in manifest.get("files", []) if item.get("archive")}

    # 1. Copy every file to the archive and verify it (resumable, never overwrites).
    for action in [item for item in work["actions"] if item["action"] in ("move", "copy")]:
        destination = target / action["archive"]
        source_hash = sha256(action["source"])
        if destination.exists():
            if sha256(destination) != source_hash:
                raise ArchiveError(f"{destination} already exists with different content; nothing was deleted")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + ".part")
            shutil.copy2(_long(action["source"]), _long(partial))
            if sha256(partial) != source_hash:
                Path(partial).unlink(missing_ok=True)
                raise ArchiveError(f"hash mismatch while copying {action['source']}; nothing was deleted")
            os.replace(_long(partial), _long(destination))
        entries[action["archive"]] = {"original": action["source"], "archive": action["archive"],
                                      "size": action["size"], "sha256": source_hash, "action": action["action"],
                                      "copied_at": entries.get(action["archive"], {}).get("copied_at") or _now(),
                                      "verified": True}
        manifest["files"] = list(entries.values())
        atomic_write_json(target / MANIFEST, manifest)
    metadata = target / "text" / "youtube_metadata.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(metadata, _youtube_metadata(project, record))

    # 2. The whole project verified: only now delete moved sources and caches on the SSD.
    for item in manifest["files"]:
        if item["action"] != "move" or item.get("source_deleted"):
            continue
        source, archived = Path(item["original"]), target / item["archive"]
        if not archived.is_file() or sha256(archived) != item["sha256"]:
            raise ArchiveError(f"archived copy of {source} is missing or differs; it was not deleted")
        Path(_long(source)).unlink(missing_ok=True)
        item["source_deleted"] = _now()
    caches = []
    if not keep_cache:
        for name in CACHES:
            folder = project / name
            if folder.exists():
                size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
                shutil.rmtree(_long(folder), ignore_errors=True)
                caches.append({"original": str(folder), "action": "deleted-cache", "size": size,
                               "deleted_at": _now()})
    for folder in sorted((path for path in project.rglob("*") if path.is_dir()), key=lambda p: -len(p.parts)):
        if not any(folder.iterdir()):
            folder.rmdir()
    manifest["caches"] = manifest.get("caches", []) + caches
    manifest["finished_at"] = _now()
    atomic_write_json(target / MANIFEST, manifest)

    # 3. A stub stays on the SSD so the GUI still lists the project.
    stub = dict(info, status="archived", archive_path=str(target), archived_at=manifest["finished_at"])
    atomic_write_json(project / projects.PROJECT_FILE, stub)
    (project / POINTER).write_text(
        f"This project was archived to {target} on {manifest['finished_at']}.\n"
        f"Restore it with: python -m scripts.restore_project --project {project.name}\n", encoding="utf-8")
    log(f"Archived {project.name} to {target} ({len(manifest['files'])} files, "
        f"{work['payload_bytes'] / 2**20:.0f} MB; {len(caches)} cache folder(s) deleted)")
    return manifest


def restore_project(slug: str, *, archive_path=None, log=print, only=None) -> Path:
    """Copy an archived project back to projects/<slug> (the archive stays intact).

    ``only`` (project-relative names or folder prefixes, e.g. ``["visual_bible.json",
    "character_refs"]``) restores just those files — enough to regenerate thumbnails or
    rebuild a description without copying the whole video back."""
    stub_path = projects.root() / slug
    info = projects.load(stub_path) if stub_path.exists() else {}
    target = Path(archive_path or info.get("archive_path") or root() / slug)
    manifest = load_json(target / MANIFEST)
    if not manifest:
        raise ArchiveError(f"no archive manifest in {target}")
    project = stub_path
    restored = 0
    for item in manifest.get("files", []):
        if item["action"] != "move":
            continue
        source = target / item["archive"]
        relative = Path(item["original"]).relative_to(Path(manifest["source"]))
        if only is not None and not any(relative.parts[:len(Path(name).parts)] == Path(name).parts for name in only):
            continue
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_long(source), _long(destination))
        if sha256(destination) != item["sha256"]:
            raise ArchiveError(f"restored {destination} does not match the archive")
        restored += 1
    data = projects.load(project)
    if only is not None:                       # partial: the project stays archived
        data.update(partially_restored=sorted(set(data.get("partially_restored", [])) | set(only)),
                    restored_at=_now(), archive_path=str(target))
        atomic_write_json(project / projects.PROJECT_FILE, data)
        log(f"Restored {restored} file(s) of {slug} ({', '.join(only)}) from {target}")
        return project
    data.update(status="restored", restored_at=_now(), archive_path=str(target))
    atomic_write_json(project / projects.PROJECT_FILE, data)
    (project / POINTER).unlink(missing_ok=True)
    log(f"Restored {restored} files of {slug} from {target} to {project}")
    return project


# ── Automatic archive after publication, with a retry queue ─────────────────

def queue_path() -> Path:
    return Path(getattr(config, "ARCHIVE_QUEUE_FILE", "data/archive_queue.json"))


def _queue() -> list[dict]:
    return (load_json(queue_path()) or {}).get("queue", [])


def _save_queue(items: list[dict]) -> None:
    atomic_write_json(queue_path(), {"queue": items})


def archive_after_publish(slugs: list[str], log=print) -> dict:
    """Archive newly published projects and retry queued ones; failures are queued, never raised."""
    summary = {"archived": [], "queued": []}
    if not getattr(config, "ARCHIVE_ON_PUBLISH", True):
        return summary
    pending = list(dict.fromkeys([item["slug"] for item in _queue()] + [slug for slug in slugs if slug]))
    remaining = []
    for slug in pending:
        try:
            project = projects.path_for(slug)
            if projects.load(project).get("status") == "archived":
                continue
            archive_project(project, log=log)
            summary["archived"].append(slug)
        except (ArchiveError, OSError, projects.ProjectNotFound) as exc:
            log(f"   WARNING: archiving {slug} postponed ({exc}); it will be retried on the next sync")
            remaining.append({"slug": slug, "error": str(exc)[:300], "queued_at": _now()})
            summary["queued"].append(slug)
    if remaining or queue_path().exists():
        _save_queue(remaining)
    return summary


def json_dumps(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)
