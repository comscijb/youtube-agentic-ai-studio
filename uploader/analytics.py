"""Per-video and channel performance (Phase 3.6-E).

Two Google APIs, one OAuth scope (``yt-analytics.readonly``, same re-consent
flow as the upload scopes):

* **YouTube Analytics API v2** (``reports.query``): views, average view
  duration, average percentage viewed, subscribers gained, views by country
  (top 10 + the US share), traffic sources and a daily series since publication;
* **YouTube Reporting API v1**: thumbnail **impressions and impressions CTR**
  are not in the Analytics API — they come from the daily bulk report
  ``channel_reach_basic_a1`` (``video_thumbnail_impressions``,
  ``video_thumbnail_impressions_ctr``). A reporting job is created once
  (``ensure_reach_job``); YouTube then publishes one CSV per day, downloaded and
  cached under ``data/analytics/reach/``. Until the first reports exist (a day or
  two after the job is created) impressions show as unavailable.

Results are cached in ``data/analytics/<video_id>.json`` and refreshed on demand
or by the publication sync, at most every ``ANALYTICS_REFRESH_HOURS`` (6). Studio
data lags 24–48 h and small numbers are bucketed (thresholded) by YouTube.

Flags: CTR < 2 % after ≥ 1 000 impressions → try a new thumbnail/title;
average percentage viewed < 30 % → look at the opening.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json

SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
REACH_REPORT = "channel_reach_basic_a1"
TOTAL_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained"
LOW_CTR = 0.02
LOW_CTR_MIN_IMPRESSIONS = 1000
LOW_RETENTION = 30.0
LAG_NOTE = "Os dados do YouTube Studio chegam com 24–48 h de atraso e números pequenos são arredondados."


def folder() -> Path:
    return Path(getattr(config, "ANALYTICS_DIR", Path(config.PUBLICATIONS_FILE).parent / "analytics"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def services(interactive: bool = False):
    """(analytics v2, reporting v1) authorized with the stored credentials."""
    from googleapiclient.discovery import build
    from uploader.youtube import load_credentials
    credentials = load_credentials(interactive)
    return (build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False),
            build("youtubereporting", "v1", credentials=credentials, cache_discovery=False))


def rows(response: dict) -> list[dict]:
    """reports.query response → [{column: value}]."""
    names = [header["name"] for header in response.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in response.get("rows") or []]


def _query(analytics, video_id: str, start: str, end: str, metrics: str, **extra) -> list[dict]:
    extra.setdefault("filters", f"video=={video_id}")
    return rows(analytics.reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics=metrics,
                                          **extra).execute())


def video_stats(analytics, video_id: str, start: str, end: str) -> dict:
    """Totals, countries (top 10 + US share), traffic sources and the daily series."""
    totals = (_query(analytics, video_id, start, end, TOTAL_METRICS) or [{}])[0]
    countries = _query(analytics, video_id, start, end, "views", dimensions="country", sort="-views", maxResults=10)
    us = _query(analytics, video_id, start, end, "views", dimensions="country", filters=f"video=={video_id};country==US")
    traffic = _query(analytics, video_id, start, end, "views,estimatedMinutesWatched",
                     dimensions="insightTrafficSourceType", sort="-views")
    daily = _query(analytics, video_id, start, end, "views,averageViewPercentage,subscribersGained",
                   dimensions="day", sort="day")
    views = float(totals.get("views") or 0)
    us_views = float((us[0] if us else {}).get("views") or 0)
    return {"totals": totals, "countries": countries, "us_share": round(us_views / views, 3) if views else None,
            "traffic_sources": traffic, "daily": daily}


# ── Reach (impressions, CTR) from the Reporting API ─────────────────────────

def ensure_reach_job(reporting, store: Path | None = None) -> str:
    """The id of our channel_reach_basic_a1 job, created once and remembered."""
    store = store or folder() / "reporting_job.json"
    saved = load_json(store) or {}
    if saved.get("report_type") == REACH_REPORT and saved.get("job_id"):
        return saved["job_id"]
    jobs = reporting.jobs().list().execute().get("jobs", [])
    job = next((item for item in jobs if item.get("reportTypeId") == REACH_REPORT), None)
    if job is None:
        job = reporting.jobs().create(body={"reportTypeId": REACH_REPORT, "name": "Private Gravity reach"}).execute()
    atomic_write_json(store, {"report_type": REACH_REPORT, "job_id": job["id"], "created": job.get("createTime")})
    return job["id"]


def _download(reporting, url: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    buffer = io.BytesIO()
    request = reporting.media().download_media(resourceName=" ")      # as in Google's reporting sample
    request.uri = url
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def sync_reach_reports(reporting, job_id: str, *, download=None, cache: Path | None = None) -> list[Path]:
    """Download every reach report not cached yet; returns the cached CSV paths."""
    cache = cache or folder() / "reach"
    cache.mkdir(parents=True, exist_ok=True)
    download = download or (lambda url: _download(reporting, url))
    for report in reporting.jobs().reports().list(jobId=job_id).execute().get("reports", []):
        # <day>__<created>__<id>.csv: a day YouTube re-issued keeps only its latest report (reach_for)
        name = f"{str(report.get('startTime', ''))[:10]}__{str(report.get('createTime', '')).replace(':', '')}__{report['id']}.csv"
        path = cache / name
        if not path.is_file():
            path.write_bytes(download(report["downloadUrl"]))
    return sorted(cache.glob("*.csv"))


def latest_per_day(reports: list[Path]) -> list[Path]:
    best = {}
    for path in reports:
        day = path.name.split("__")[0] if "__" in path.name else path.stem
        if day not in best or path.name > best[day].name:
            best[day] = path
    return [best[day] for day in sorted(best)]


def reach_for(video_id: str, reports: list[Path]) -> dict:
    """Impressions and the impressions-weighted CTR of one video, total and per day."""
    by_day = {}
    for path in latest_per_day(reports):
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))):
            if row.get("video_id") != video_id:
                continue
            impressions = float(row.get("video_thumbnail_impressions") or 0)
            ctr = float(row.get("video_thumbnail_impressions_ctr") or 0)
            ctr = ctr / 100 if ctr > 1 else ctr                     # a percentage would be > 1
            day = str(row.get("date", ""))
            day = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else day
            entry = by_day.setdefault(day, {"impressions": 0.0, "clicks": 0.0})
            entry["impressions"] += impressions
            entry["clicks"] += impressions * ctr
    impressions = sum(entry["impressions"] for entry in by_day.values())
    clicks = sum(entry["clicks"] for entry in by_day.values())
    return {"available": bool(by_day), "impressions": round(impressions),
            "ctr": round(clicks / impressions, 4) if impressions else None,
            "daily": [{"day": day, "impressions": round(entry["impressions"]),
                       "ctr": round(entry["clicks"] / entry["impressions"], 4) if entry["impressions"] else None}
                      for day, entry in sorted(by_day.items())]}


# ── Flags, cache, refresh ───────────────────────────────────────────────────

def flags(stats: dict) -> list[str]:
    found = []
    reach = stats.get("reach") or {}
    if reach.get("impressions", 0) >= LOW_CTR_MIN_IMPRESSIONS and reach.get("ctr") is not None \
            and reach["ctr"] < LOW_CTR:
        found.append(f"CTR {reach['ctr']:.1%} após {reach['impressions']} impressões (< 2 %): teste outra thumbnail "
                     "ou título (YouTube Studio → Test & compare).")
    retention = (stats.get("totals") or {}).get("averageViewPercentage")
    if retention is not None and float(retention) < LOW_RETENTION:
        found.append(f"Em média só {float(retention):.0f} % do vídeo é assistido (< 30 %): revise a abertura.")
    return found


def cached(video_id: str) -> dict | None:
    return load_json(folder() / f"{video_id}.json")


def is_fresh(record: dict | None, hours: float | None = None) -> bool:
    hours = float(hours if hours is not None else getattr(config, "ANALYTICS_REFRESH_HOURS", 6))
    if not record or not record.get("fetched_at"):
        return False
    fetched = datetime.fromisoformat(record["fetched_at"])
    return _now() - fetched < timedelta(hours=hours)


def refresh_video(record: dict, *, analytics, reporting=None, force: bool = False, download=None) -> dict:
    """Fetch (or reuse the cache of) one published video; ``record`` is its ledger entry."""
    video_id = record["video_id"]
    previous = cached(video_id)
    if is_fresh(previous) and not force:
        return previous
    published = str(record.get("published_at") or record.get("uploaded_at") or _now().isoformat())[:10]
    end = date.today().isoformat()
    stats = video_stats(analytics, video_id, published, end)
    stats["reach"] = {"available": False, "impressions": 0, "ctr": None, "daily": []}
    if reporting is not None:
        try:
            job = ensure_reach_job(reporting)
            stats["reach"] = reach_for(video_id, sync_reach_reports(reporting, job, download=download))
        except Exception as exc:                          # noqa: BLE001 — reach is optional; the rest stays
            stats["reach"]["error"] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
    stats.update(video_id=video_id, title=record.get("title", ""), project=record.get("project"),
                 published_at=published, fetched_at=_now().replace(microsecond=0).isoformat(), note=LAG_NOTE)
    stats["flags"] = flags(stats)
    if not record.get("parent_video_id") and record.get("kind", "episode") != "short":
        from uploader import retention                  # Phase 4.0-D: the retention curve and its lessons
        try:
            stats["retention"] = retention.fetch_curve(analytics, video_id, published, end)
            stats["retention_lessons"] = retention.learn(record, stats)
        except Exception as exc:                          # noqa: BLE001 — retention is optional; the rest stays
            stats["retention_error"] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
    atomic_write_json(folder() / f"{video_id}.json", stats)
    return stats


def published_records() -> list[dict]:
    from uploader import publications
    return [item for item in publications.load_ledger().get("publications", [])
            if item.get("state") == publications.PUBLISHED and item.get("video_id")]


def refresh_all(*, force: bool = False, interactive: bool = False, analytics=None, reporting=None) -> list[dict]:
    records = published_records()
    if not records:
        return []
    if not force and all(is_fresh(cached(item["video_id"])) for item in records):
        return [cached(item["video_id"]) for item in records]
    if analytics is None:
        analytics, reporting = services(interactive)
    return [refresh_video(item, analytics=analytics, reporting=reporting, force=force) for item in records]


STATUS_FILE = "status.json"
AUTH_HELP = ("A autorização do YouTube expirou ou falta o escopo de Analytics. Com o app OAuth em modo Testing o Google "
             "expira o token a cada 7 dias: clique em \"Autorizar de novo\" (abre o navegador) ou rode "
             "python -m scripts.sync_publications. Para não expirar mais, publique o app OAuth (Google Cloud → "
             "OAuth consent screen → Publish app).")


def status() -> dict:
    """The last refresh attempt: {last_attempt, last_success, videos, error, auth_required} (Phase 4.0-C)."""
    return load_json(folder() / STATUS_FILE) or {"last_attempt": None, "last_success": None, "videos": 0, "error": ""}


def _record_status(*, ok: bool, videos: int = 0, error: str = "", auth_required: bool = False) -> dict:
    previous = status()
    now = _now().replace(microsecond=0).isoformat()
    value = {"last_attempt": now, "last_success": now if ok else previous.get("last_success"),
             "videos": videos if ok else previous.get("videos", 0), "error": "" if ok else error,
             "auth_required": auth_required}
    folder().mkdir(parents=True, exist_ok=True)
    atomic_write_json(folder() / STATUS_FILE, value)
    return value


def refresh_with_status(*, force: bool = False, interactive: bool = False, **services_) -> dict:
    """refresh_all that always leaves a trace in data/analytics/status.json. Before Phase 4.0 a failure only printed a
    console warning, so a dead token left data/analytics/ missing with nothing on screen."""
    from uploader.youtube import YouTubeAuthError
    try:
        done = refresh_all(force=force, interactive=interactive, **services_)
    except YouTubeAuthError as exc:
        _record_status(ok=False, error=f"{AUTH_HELP} ({str(exc)[:120]})", auth_required=True)
        raise
    except Exception as exc:                             # noqa: BLE001
        _record_status(ok=False, error=f"{exc.__class__.__name__}: {str(exc)[:300]}")
        raise
    _record_status(ok=True, videos=len(done))
    return {"videos": done, "status": status()}


def refresh_due_safely() -> None:
    """Called by the publication sync: refresh stale caches, never raise, never open a browser; the outcome is
    written to status.json and shown in Desempenho."""
    try:
        refresh_with_status(interactive=False)
    except Exception as exc:                              # noqa: BLE001
        print(f"   WARNING: analytics refresh skipped ({exc.__class__.__name__}: {str(exc)[:160]})")


def overview() -> dict:
    """The channel at a glance, from the cache only (no API call)."""
    videos = []
    records = published_records()
    shorts_of = {}
    for record in records:
        if record.get("parent_video_id"):
            shorts_of.setdefault(record["parent_video_id"], []).append(record)
    for record in [item for item in records if not item.get("parent_video_id")] + \
            [item for item in records if item.get("parent_video_id") and
             item["parent_video_id"] not in {other["video_id"] for other in records}]:
        stats = cached(record["video_id"]) or {}
        totals, reach = stats.get("totals") or {}, stats.get("reach") or {}
        videos.append({"video_id": record["video_id"], "title": record.get("title", ""), "project": record.get("project"),
                       "published_at": stats.get("published_at") or str(record.get("published_at") or "")[:10],
                       "views": totals.get("views"), "average_view_percentage": totals.get("averageViewPercentage"),
                       "subscribers_gained": totals.get("subscribersGained"), "impressions": reach.get("impressions"),
                       "ctr": reach.get("ctr"), "us_share": stats.get("us_share"), "flags": stats.get("flags", []),
                       "fetched_at": stats.get("fetched_at"),
                       "retention_lessons": [item.get("text", "") for item in stats.get("retention_lessons") or []],
                       "shorts": [{"video_id": short["video_id"], "title": short.get("title", ""),
                                   "views": ((cached(short["video_id"]) or {}).get("totals") or {}).get("views")}
                                  for short in shorts_of.get(record["video_id"], [])]})
    total = lambda key: sum(float(item[key] or 0) for item in videos)
    from uploader import retention
    return {"videos": videos, "views": total("views"), "subscribers_gained": total("subscribers_gained"),
            "impressions": total("impressions"), "note": LAG_NOTE, "retention_guidance": retention.guidance()}


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
