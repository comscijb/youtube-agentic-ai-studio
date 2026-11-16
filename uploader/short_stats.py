"""Which Short formats work (Phase 4.3-F).

Every Short in the ledger records its ``format`` (the Phase 3.7 cut-downs are "recorte"). From the analytics cache
(no API call) ``aggregate`` ranks the formats over the last 30 days: Shorts, views, average viewed %, average view
duration, subscribers, and clicks to the episode where available — the episode's "SHORTS" traffic source, shared among
its Shorts by their views. ``suggest`` proposes new ``SHORTS_FORMATS`` weights once a format has ≥ 3 Shorts: more of
what works, never below one trial per two episodes for any format. The user applies them with one click
(``video.shorts_settings.save``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config

WINDOW_DAYS = 30
MIN_SHORTS = 3
CUT = "recorte"
SHORTS_SOURCE = "SHORTS"


def _date(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def aggregate(records: list[dict], cached, *, now: datetime | None = None, days: int = WINDOW_DAYS) -> list[dict]:
    """[{"format", "shorts", "views", "avg_view_percentage", "avg_view_duration", "subscribers", "episode_clicks",
    "views_per_short"}], best first (views per Short x viewed %)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    shorts = [record for record in records if record.get("parent_video_id")]
    stats = {record["video_id"]: (cached(record["video_id"]) or {}) for record in shorts}
    in_window = [record for record in shorts
                 if (_date(stats[record["video_id"]].get("published_at") or record.get("published_at") or record.get("uploaded_at"))
                     or now) >= since]
    clicks_by_episode = {}
    for parent in {record["parent_video_id"] for record in in_window}:
        sources = (cached(parent) or {}).get("traffic_sources") or []
        clicks_by_episode[parent] = sum(float(row.get("views") or 0) for row in sources
                                        if str(row.get("insightTrafficSourceType", "")).upper() == SHORTS_SOURCE)
    views_by_episode = {}
    for record in in_window:
        views = float((stats[record["video_id"]].get("totals") or {}).get("views") or 0)
        views_by_episode[record["parent_video_id"]] = views_by_episode.get(record["parent_video_id"], 0.0) + views
    rows = {}
    for record in in_window:
        name = record.get("format") or CUT
        totals = stats[record["video_id"]].get("totals") or {}
        views = float(totals.get("views") or 0)
        row = rows.setdefault(name, {"format": name, "shorts": 0, "views": 0.0, "_pct": 0.0, "_dur": 0.0, "subscribers": 0.0,
                                     "episode_clicks": 0.0, "with_data": 0})
        row["shorts"] += 1
        if totals:
            row["with_data"] += 1
        row["views"] += views
        row["_pct"] += views * float(totals.get("averageViewPercentage") or 0)
        row["_dur"] += views * float(totals.get("averageViewDuration") or 0)
        row["subscribers"] += float(totals.get("subscribersGained") or 0)
        episode_views = views_by_episode.get(record["parent_video_id"]) or 0
        if episode_views:
            row["episode_clicks"] += clicks_by_episode.get(record["parent_video_id"], 0.0) * views / episode_views
    found = []
    for row in rows.values():
        views = row["views"]
        found.append({"format": row["format"], "shorts": row["shorts"], "with_data": row["with_data"], "views": int(views),
                      "views_per_short": round(views / row["shorts"], 1),
                      "avg_view_percentage": round(row["_pct"] / views, 1) if views else None,
                      "avg_view_duration": round(row["_dur"] / views, 1) if views else None,
                      "subscribers": int(row["subscribers"]), "episode_clicks": round(row["episode_clicks"], 1)})
    return sorted(found, key=score, reverse=True)


def score(row: dict) -> float:
    return float(row.get("views_per_short") or 0) * (float(row.get("avg_view_percentage") or 0) / 100.0 + 0.1)


def suggest(rows: list[dict], current: dict | None = None, per_episode: int | None = None) -> dict:
    """{"ready", "weights", "notes"}: new weights for the formats with ≥ 3 Shorts (others keep theirs); every enabled
    format keeps at least one Short per two episodes (share ≥ 0.5 / per_episode)."""
    from video import short_formats
    current = {name: float(value) for name, value in (current if current is not None else
                                                      getattr(config, "SHORTS_FORMATS", {}) or {}).items() if name in short_formats.FORMATS}
    per_episode = per_episode or short_formats.per_episode()
    measured = {row["format"]: row for row in rows if row["format"] in short_formats.FORMATS and row["shorts"] >= MIN_SHORTS}
    if not measured:
        return {"ready": False, "weights": current,
                "notes": [f"É preciso ≥ {MIN_SHORTS} Shorts de um formato nos últimos {WINDOW_DAYS} dias para sugerir pesos."]}
    best = max(score(row) for row in measured.values()) or 1.0
    weights = dict(current)
    for name, row in measured.items():
        weights[name] = round(max(0.05, score(row) / best) * max(current.values() or [1.0]), 3)
    enabled = [name for name, value in weights.items() if value > 0]
    floor = 0.5 / per_episode                            # one Short per two episodes
    for _ in range(10):
        total = sum(weights[name] for name in enabled)
        low = [name for name in enabled if weights[name] / total < floor - 1e-9]
        if not low:
            break
        others = sum(weights[name] for name in enabled if name not in low)
        share_left = 1.0 - floor * len(low)
        for name in low:
            weights[name] = round(floor * others / max(share_left, 1e-6), 3)
    notes = [f"{short_formats.FORMATS[name]['label']}: {row['views_per_short']:.0f} views por Short, "
             f"{row['avg_view_percentage'] or 0:.0f} % assistido" for name, row in measured.items()]
    return {"ready": True, "weights": {name: weights.get(name, 0.0) for name in short_formats.ORDER}, "notes": notes}


def panel(*, now: datetime | None = None) -> dict:
    """What the "Formatos de Shorts" panel shows (cache only)."""
    from uploader import analytics
    rows = aggregate(analytics.published_records(), analytics.cached, now=now)
    return {"rows": rows, "suggestion": suggest(rows), "window_days": WINDOW_DAYS}
