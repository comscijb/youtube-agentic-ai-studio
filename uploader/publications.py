"""Publication ledger and YouTube publication-state sync.

Every upload creates a record in ``data/publications.json`` immediately. A
topic is banned only when sync observes the video as actually public
(``privacyStatus == "public"`` and ``uploadStatus == "processed"``); scheduled
videos therefore stay pending until YouTube publishes them at ``publishAt``.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json, stable_hash

LEDGER_SCHEMA_VERSION = 1
PENDING = "uploaded_pending_publication"
PUBLISHED = "published"
FAILED = "failed"
REMOVED = "removed"
_BATCH = 50
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ledger_path() -> Path:
    return Path(getattr(config, "PUBLICATIONS_FILE", "data/publications.json"))


def load_ledger(path=None) -> dict:
    value = load_json(path or ledger_path()) or {}
    records = value.get("publications")
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "publications": records if isinstance(records, list) else [],
    }


def save_ledger(ledger: dict, path=None) -> None:
    atomic_write_json(path or ledger_path(), {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "publications": ledger["publications"],
    })


def _file_sha256(path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def selected_premise(brief: dict) -> str:
    """The exact premise: the user's premise, else the selected candidate's."""
    if brief.get("requested_premise"):
        return str(brief["requested_premise"])
    candidates = brief.get("premise_candidates") or []
    index = brief.get("selected_candidate_index")
    if isinstance(index, int) and 0 <= index < len(candidates) and isinstance(candidates[index], dict):
        return str(candidates[index].get("premise", ""))
    return str(brief.get("topic") or brief.get("logline") or "")


def build_record(video_id: str, run_dir, *, title: str = "", video_path: str | None = None,
                 scheduled_publish_at: str | None = None, extra: dict | None = None) -> dict:
    """Build a ledger record from a run directory's artifacts."""
    run = Path(run_dir)
    brief = load_json(run / "story_brief.json") or load_json(run / "research.json") or {}
    script = load_json(run / "script.json") or {}
    video = video_path or str(run / "final_video.mp4")
    record = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "run_dir": str(run),
        "project": (load_json(run / "project.json") or {}).get("slug"),
        "uploaded_at": _now(),
        "scheduled_publish_at": scheduled_publish_at,
        "title": title or script.get("title") or brief.get("video_title", ""),
        "thumbnail_text": script.get("thumbnail_text") or brief.get("thumbnail_text", ""),
        "premise": selected_premise(brief),
        "logline": brief.get("logline", ""),
        "psychological_theme": brief.get("psychological_theme", ""),
        "psychological_angle": brief.get("psychological_angle", ""),
        "narrative_type": brief.get("narrative_type", ""),
        "central_contradiction": brief.get("central_contradiction", ""),
        "source_hashes": {
            "script": stable_hash(script) if script else None,
            "video": _file_sha256(video),
        },
        "state": PENDING,
        "state_checked_at": None,
        "published_at": None,
    }
    record.update(extra or {})
    return record


def record_upload(video_id: str, run_dir, *, path=None, **kwargs) -> dict:
    """Write the pending record right after a successful upload (idempotent per video)."""
    with _lock:
        ledger = load_ledger(path)
        existing = next((item for item in ledger["publications"]
                         if item.get("video_id") == video_id), None)
        if existing:
            return existing
        record = build_record(video_id, run_dir, **kwargs)
        ledger["publications"].append(record)
        save_ledger(ledger, path)
    from studio import projects
    projects.touch(run_dir, published_video_id=video_id)
    return record


def banned_entry(record: dict) -> dict:
    return {
        "theme": record.get("psychological_theme", ""),
        "angle": record.get("psychological_angle", ""),
        "premise": record.get("premise", ""),
        "title": record.get("title", ""),
        "video": record.get("video_id", ""),
        "published": (record.get("published_at") or _now())[:10],
    }


def _classify(item: dict | None) -> str:
    if item is None:
        return REMOVED
    status = item.get("status") or {}
    upload_status = str(status.get("uploadStatus", "")).lower()
    if upload_status == "deleted":
        return REMOVED
    if upload_status in {"failed", "rejected"}:
        return FAILED
    if status.get("privacyStatus") == "public" and upload_status == "processed":
        return PUBLISHED
    return PENDING


def sync_publications(service=None, *, path=None, interactive: bool = False) -> dict:
    """Update pending records from YouTube and ban topics of published videos.

    Idempotent and keyed by video_id. Returns a summary of state changes.
    """
    from agents.researcher import append_banned_entry

    summary = {"checked": 0, "published": [], "failed": [], "removed": [], "pending": []}
    with _lock:
        ledger = load_ledger(path)
        pending = [item for item in ledger["publications"] if item.get("state") == PENDING]
        if not pending:
            _archive_published([])               # still retry archives queued by an earlier sync
            return summary
        if service is None:
            from uploader.youtube import get_service
            service = get_service(interactive=interactive)
        found = {}
        ids = [item["video_id"] for item in pending]
        for offset in range(0, len(ids), _BATCH):
            response = service.videos().list(
                part="status,snippet", id=",".join(ids[offset:offset + _BATCH]),
                maxResults=_BATCH,
            ).execute()
            for item in response.get("items", []):
                found[item.get("id")] = item
        checked_at = _now()
        for record in pending:
            state = _classify(found.get(record["video_id"]))
            record["state_checked_at"] = checked_at
            summary["checked"] += 1
            if state == PUBLISHED:
                record["state"] = PUBLISHED
                record["published_at"] = checked_at
                append_banned_entry(banned_entry(record))
                summary["published"].append(record["video_id"])
            elif state in (FAILED, REMOVED):
                record["state"] = state
                summary[state].append(record["video_id"])
            else:
                summary["pending"].append(record["video_id"])
        save_ledger(ledger, path)
    published = [record.get("project") for record in pending if record.get("state") == PUBLISHED]
    summary["archive"] = _archive_published(published)
    return summary


def _archive_published(slugs: list) -> dict | None:
    """Archive newly published projects to the HDD (ARCHIVE_ON_PUBLISH); never raises."""
    try:
        from studio import archive
        return archive.archive_after_publish([slug for slug in slugs if slug])
    except Exception as exc:
        print(f"   WARNING: archive after publication skipped ({exc.__class__.__name__}: {exc})")
        return None


def sync_publications_safely(**kwargs) -> dict | None:
    """Run sync without ever raising into generation; failures log a warning."""
    try:
        summary = sync_publications(**kwargs)
    except Exception as exc:
        print(f"   WARNING: publication sync skipped ({exc.__class__.__name__}: {exc})")
        return None
    if summary["published"]:
        print(f"   -> Publication sync: {len(summary['published'])} video(s) now public; "
              "topics added to banned_topics.txt")
    return summary


def start_background_sync(interval_minutes: float | None = None) -> threading.Thread:
    """Sync now and then every PUBLICATION_SYNC_INTERVAL_MINUTES (daemon thread)."""
    minutes = float(interval_minutes if interval_minutes is not None
                    else getattr(config, "PUBLICATION_SYNC_INTERVAL_MINUTES", 30))
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            sync_publications_safely(interactive=False)
            stop.wait(max(60.0, minutes * 60))

    thread = threading.Thread(target=loop, name="publication-sync", daemon=True)
    thread.stop_event = stop
    thread.start()
    return thread
