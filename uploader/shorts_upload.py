"""Shorts in the approval gate and the upload, after their episode (Phase 3.7-B.3).

* ``included`` — the plan's segments that are included and rendered; their files
  and texts are part of the approval bindings (``binding``), so changing a Short
  invalidates the approval like changing the episode.
* ``schedule`` — ``SHORTS_SCHEDULE``: by default the first Short goes public with
  the episode, each other one ``spacing_hours`` later at ``time`` (local), via
  ``publishAt`` (uploaded private + scheduled). ``plan_lines`` is what the
  confirmation dialog shows.
* ``upload_shorts`` — after the episode (its URL goes into every Short's
  description): title with ``#Shorts``, same English language fields, the
  Short's own SRT as the English caption track. Each Short is recorded in the
  ledger with ``parent_video_id``; only the parent episode bans its topic.
* The "related video" link of a Short can only be set in YouTube Studio (not in
  the API): ``reminder`` says so, with the episode URL ready to copy.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import config
from agents.narrative import load_json

DEFAULT_SCHEDULE = {"first": "with_episode", "spacing_hours": 24, "time": "18:00"}
PRIVACY_PT = {"public": "PÚBLICO", "unlisted": "NÃO LISTADO", "private": "PRIVADO"}
WEEKDAYS_PT = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def included(run_dir) -> list[dict]:
    from video import shorts
    plan = shorts.load(run_dir) or {}
    found = []
    for segment in plan.get("segments", []):
        video = Path(run_dir) / shorts.FOLDER / f"{segment.get('id')}.mp4"
        if segment.get("include", True) and video.is_file():
            found.append({**segment, "video_path": str(video),
                          "srt_path": str(video.with_suffix(".srt"))})
    return found


def _sha(path) -> str | None:
    if not Path(path).is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def binding(run_dir) -> str:
    """One hash over every included Short: video file, title, description."""
    items = [{"id": item["id"], "video": _sha(item["video_path"]), "title": item.get("title", ""),
              "description": item.get("description", "")} for item in included(run_dir)]
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()


def schedule(count: int, *, now: datetime | None = None, episode_publish_at: str = "",
             settings: dict | None = None) -> list[datetime | None]:
    """When each Short goes public: ``None`` = immediately (with a public episode); datetimes are aware.
    With a scheduled episode the first Short goes public at the episode's publishAt."""
    settings = {**DEFAULT_SCHEDULE, **(settings or getattr(config, "SHORTS_SCHEDULE", {}) or {})}
    now = (now or datetime.now().astimezone())
    hour, minute = (int(part) for part in str(settings["time"]).split(":"))
    episode = datetime.fromisoformat(episode_publish_at.replace("Z", "+00:00")) if episode_publish_at else None
    times: list[datetime | None] = []
    base = episode or now
    for index in range(count):
        if index == 0 and settings["first"] == "with_episode":
            times.append(episode)                                  # None when the episode is public now
            continue
        day = (base + timedelta(hours=float(settings["spacing_hours"]) * index)).astimezone(now.tzinfo)
        times.append(day.replace(hour=hour, minute=minute, second=0, microsecond=0))
    return times


def label(when: datetime | None, now: datetime | None = None) -> str:
    """"agora", "amanhã 18:00", "depois de amanhã 18:00", "sexta 18:00"."""
    if when is None:
        return "agora"
    now = now or datetime.now().astimezone()
    local = when.astimezone(now.tzinfo)
    days = (local.date() - now.date()).days
    prefix = {0: "hoje", 1: "amanhã", 2: "depois de amanhã"}.get(days, WEEKDAYS_PT[local.weekday()] + f" {local:%d/%m}")
    return f"{prefix} {local:%H:%M}"


def rfc3339(when: datetime) -> str:
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plan_lines(run_dir, *, privacy: str, publish_at: str = "", now: datetime | None = None) -> dict:
    """What the confirmation dialog shows: the episode's state and each Short's planned time."""
    now = now or datetime.now().astimezone()
    items = included(run_dir)
    times = schedule(len(items), now=now, episode_publish_at=publish_at)
    state = PRIVACY_PT.get(privacy, privacy.upper())
    episode = (f"o episódio será {state} agora" if not publish_at else
               f"o episódio será {state} em {label(datetime.fromisoformat(publish_at.replace('Z', '+00:00')), now)}")
    shorts = [{"id": item["id"], "title": item.get("title", ""), "when": label(when, now),
               "publish_at": rfc3339(when) if when else ""} for item, when in zip(items, times)]
    text = episode + (("; Shorts: " + ", ".join(item["when"] for item in shorts)) if shorts else "")
    return {"episode": episode, "shorts": shorts, "text": text}


def description(segment: dict, episode_url: str, run_dir) -> str:
    from agents import packaging
    from video import description as episode_description
    parts = load_json(Path(run_dir) / packaging.DESCRIPTION_PARTS_FILE) or {}
    return "\n\n".join([
        str(segment.get("description", "")).strip(),
        f"▶ Full episode: {episode_url}",
        episode_description.cta_block(parts.get("comment_question", "")),
        "#Shorts #PrivateGravity",
    ]).strip() + "\n"


def reminder(episode_url: str, count: int) -> str:
    return (f"{count} Short(s) enviado(s). No YouTube Studio, abra cada Short → Detalhes → \"Vídeo relacionado\" "
            f"e escolha o episódio (a API não faz isso): {episode_url}")


def short_localizations(title: str, text: str, run_dir) -> dict:
    """Phase 4.0-B: a Short's translated title (#Shorts kept) and description; never stops the upload."""
    from uploader import localize
    try:
        return localize.payload(localize.translate(title, text, is_short=True, run_dir=run_dir))
    except Exception as exc:                             # noqa: BLE001
        print(f"   WARNING: Short translations skipped ({exc.__class__.__name__})")
        return {}


def upload_shorts(run_dir, episode_video_id: str, *, service=None, privacy: str | None = None,
                  publish_at: str = "", now: datetime | None = None,
                  progress: Callable[[str], None] = print, disclosure: dict | None = None) -> list[dict]:
    """Upload every included Short after its episode; returns their ledger records. Each Short carries
    the episode's synthetic-content decision (Phase 3.9-F)."""
    from uploader import publications, youtube
    from uploader.approval import episode_disclosure
    disclosure = disclosure or episode_disclosure(run_dir)
    script = load_json(Path(run_dir) / "script.json") or {}
    episode_url = f"https://www.youtube.com/watch?v={episode_video_id}"
    items = included(run_dir)
    times = schedule(len(items), now=now, episode_publish_at=publish_at)
    records = []
    for item, when in zip(items, times):
        scheduled = rfc3339(when) if when else ""
        text = description(item, episode_url, run_dir)
        video_id = youtube.upload_video(
            item["video_path"], title=item["title"], description=text,
            tags=list(script.get("tags", []))[:10] + ["Shorts"], privacy=privacy, publish_at=scheduled,
            service=service, contains_synthetic_media=disclosure["value"],
            localizations=short_localizations(item["title"], text, run_dir))
        captions = {"action": "skipped"}
        if Path(item["srt_path"]).is_file():
            try:
                captions = youtube.upload_captions(video_id, item["srt_path"], service=service)
            except Exception as exc:                          # noqa: BLE001 — never fails the upload
                captions = {"action": "failed", "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}
        record = publications.record_upload(
            video_id, run_dir, title=item["title"], video_path=item["video_path"],
            scheduled_publish_at=scheduled or None, touch_project=False,
            extra={"kind": "short", "parent_video_id": episode_video_id, "short_id": item["id"],
                   "captions": captions, "language": getattr(config, "VIDEO_LANGUAGE", "en"),
                   "contains_synthetic_media": disclosure["value"],
                   "synthetic_disclosure_reason": disclosure["reason"]})
        progress(f"   → Short {item['id']}: {record['url']} ({label(when, now)})")
        records.append(record)
    return records
