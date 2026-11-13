"""The packaging-swap loop (Phase 4.0-C): propose a new thumbnail, then a new title, for a long-form video that
gets impressions but few clicks. Never automatic: the GUI shows "Trocar miniatura?" with before/after and the
numbers, and one click applies it (``thumbnails.set`` / ``videos.update``).

The rule (``PACKAGING_SWAP_RULE``, editable in Settings): ``hours`` (72) after publication — or after the previous
swap — with at least ``min_impressions`` (300) since then and a CTR below the channel median (the median of the
other long-form videos with enough impressions; ``fallback_ctr``, 3 %, while there are fewer than two of them):

1. the next thumbnail variant from the 3.8 gallery, keeping the title;
2. another ``hours`` later, still low: the next title from the scored list (titles.json), passing ones only.

Every swap is logged in the ledger record (``packaging_swaps``) with its time and the numbers before it, so the
windows before and after can be compared. Test & Compare (decided by watch time per impression) has no API:
``export_test_compare`` writes the three combinations ready to upload in Studio.
"""
from __future__ import annotations

import shutil
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from agents.narrative import load_json


def rule() -> dict:
    from uploader import discovery_settings
    return {"hours": 72.0, "min_impressions": 300.0, "fallback_ctr": 0.03, **discovery_settings.get("swap_rule")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def window(stats: dict, since: datetime | None) -> dict:
    """Impressions and the weighted CTR in the reach days from ``since`` (inclusive) on."""
    days = (stats.get("reach") or {}).get("daily") or []
    start = since.date().isoformat() if since else ""
    chosen = [day for day in days if str(day.get("day", "")) >= start and day.get("impressions")]
    impressions = sum(float(day["impressions"]) for day in chosen)
    clicks = sum(float(day["impressions"]) * float(day.get("ctr") or 0) for day in chosen)
    return {"impressions": round(impressions), "ctr": round(clicks / impressions, 4) if impressions else None}


def channel_median(all_stats: dict, exclude: str, min_impressions: float) -> float | None:
    """Median CTR of the other long-form videos with enough impressions; None with fewer than two."""
    values = [float((stats.get("reach") or {}).get("ctr")) for video_id, stats in all_stats.items()
              if video_id != exclude and (stats.get("reach") or {}).get("ctr") is not None
              and float((stats.get("reach") or {}).get("impressions") or 0) >= min_impressions]
    return round(statistics.median(values), 4) if len(values) >= 2 else None


# ── The project's packaging (live project or archive) ────────────────────────

def project_file(slug: str, relative: str) -> Path | None:
    from studio import archive, projects
    relative = str(relative).replace("\\", "/")
    candidates = [projects.root() / slug / relative]
    try:
        root = archive.root()
        candidates += [root / slug / "other" / relative, root / slug / "text" / relative]
    except Exception:                                    # noqa: BLE001 — no archive configured
        pass
    return next((path for path in candidates if path.is_file()), None)


def variants(slug: str) -> tuple[list[dict], int | None]:
    index = project_file(slug, "thumbnails/thumbnails.json")
    data = load_json(index) if index else {}
    return [{"number": item.get("number"), "path": str(item.get("path", "")).replace("\\", "/"),
             "text": (item.get("concept") or {}).get("text", "")}
            for item in (data or {}).get("variants", []) if not item.get("archived")], (data or {}).get("selected")


def variant_path(slug: str, relative: str) -> Path | None:
    if not slug or not relative or ".." in relative:
        return None
    return project_file(slug, relative)


def titles(slug: str) -> tuple[list[str], str]:
    found = project_file(slug, "titles.json")
    data = load_json(found) if found else {}
    passing = [item["title"] for item in (data or {}).get("candidates", []) if item.get("passes", True)]
    return passing, (data or {}).get("selected", "")


# ── The rule ────────────────────────────────────────────────────────────────

def next_step(record: dict, *, slug: str) -> dict | None:
    """The next swap the loop can offer: a thumbnail variant first, then a title."""
    swaps = record.get("packaging_swaps") or []
    used_thumbs = {item.get("to") for item in swaps if item.get("kind") == "thumbnail"}
    used_titles = {item.get("from") for item in swaps if item.get("kind") == "title"} | \
        {item.get("to") for item in swaps if item.get("kind") == "title"}
    found, selected = variants(slug)
    current = next((item for item in found if item["number"] == selected), None)
    if current:
        used_thumbs.add(current["path"])
    if not any(item.get("kind") == "thumbnail" for item in swaps):
        after = [item for item in found if item["path"] not in used_thumbs]
        ordered = sorted(after, key=lambda item: ((item["number"] or 0) <= (selected or 0), item["number"] or 0))
        if ordered:
            last = next((item for item in reversed(swaps) if item.get("kind") == "thumbnail"), None)
            return {"kind": "thumbnail", "from": (last or {}).get("to") or (current or {}).get("path", ""),
                    "to": ordered[0]["path"], "text": ordered[0]["text"]}
    if not any(item.get("kind") == "title" for item in swaps):
        options, chosen = titles(slug)
        current_title = record.get("title") or chosen
        rest = [title for title in options if title != current_title and title not in used_titles]
        if rest:
            return {"kind": "title", "from": current_title, "to": rest[0]}
    return None


def evaluate(record: dict, stats: dict, all_stats: dict, *, now: datetime | None = None, settings: dict | None = None) -> dict:
    """{"due": bool, "reason", "impressions", "ctr", "threshold", "median"} for one long-form video."""
    settings = settings or rule()
    now = now or _now()
    swaps = record.get("packaging_swaps") or []
    since = _parse(swaps[-1]["at"]) if swaps else _parse(record.get("published_at") or record.get("uploaded_at"))
    numbers = window(stats, since if swaps else None)
    median = channel_median(all_stats, record.get("video_id", ""), settings["min_impressions"])
    threshold = median if median is not None else settings["fallback_ctr"]
    result = {"due": False, "impressions": numbers["impressions"], "ctr": numbers["ctr"], "threshold": threshold,
              "median": median, "since": since.isoformat() if since else None}
    if since is None or now - since < timedelta(hours=settings["hours"]):
        result["reason"] = f"menos de {settings['hours']:.0f} h desde {'a última troca' if swaps else 'a publicação'}"
    elif numbers["impressions"] < settings["min_impressions"]:
        result["reason"] = f"{numbers['impressions']} impressões (mínimo {settings['min_impressions']:.0f})"
    elif numbers["ctr"] is None or numbers["ctr"] >= threshold:
        result["reason"] = f"CTR {numbers['ctr']:.1%} não está abaixo de {threshold:.1%}" if numbers["ctr"] is not None else "sem CTR"
    else:
        result.update(due=True, reason=f"CTR {numbers['ctr']:.1%} abaixo de {threshold:.1%} "
                                         f"({'mediana do canal' if median is not None else 'referência de 3 %'}) "
                                         f"com {numbers['impressions']} impressões")
    return result


def proposals(*, now: datetime | None = None) -> dict:
    """Every long-form video with a due swap and a next step, from the analytics cache only (no API call)."""
    from uploader import analytics
    records = [item for item in analytics.published_records() if not item.get("parent_video_id")
               and item.get("kind", "episode") != "short"]
    all_stats = {item["video_id"]: analytics.cached(item["video_id"]) or {} for item in records}
    found, waiting = [], []
    for record in records:
        stats = all_stats[record["video_id"]]
        if not stats:
            continue
        verdict = evaluate(record, stats, all_stats, now=now)
        step = next_step(record, slug=record.get("project") or "") if verdict["due"] else None
        row = {"video_id": record["video_id"], "title": record.get("title", ""), "project": record.get("project"), **verdict}
        if verdict["due"] and step:
            found.append({**row, "step": step})
        else:
            waiting.append(row)
    return {"proposals": found, "waiting": waiting, "rule": rule(),
            "test_compare_hint": "Se o canal tiver Test & Compare no Studio, prefira testar as 3 combinações lá "
                                 "(decide por tempo assistido por impressão): use \"Exportar para Test & Compare\"."}


# ── Applying a swap (after the click) ────────────────────────────────────────

def apply(video_id: str, kind: str, *, service=None, now: datetime | None = None) -> dict:
    """Apply the proposed swap of ``kind`` ("thumbnail" | "title") and log it in the ledger."""
    from uploader import analytics, publications
    record = next((item for item in analytics.published_records() if item["video_id"] == video_id), None)
    if record is None:
        raise LookupError(f"{video_id} não está publicado no registro")
    step = next_step(record, slug=record.get("project") or "")
    if not step or step["kind"] != kind:
        raise ValueError("não há troca desse tipo para este vídeo")
    stats = analytics.cached(video_id) or {}
    swaps = record.get("packaging_swaps") or []
    before = window(stats, _parse(swaps[-1]["at"]) if swaps else None)
    if service is None:
        from uploader import youtube
        service = youtube.get_service(interactive=False)
    if kind == "thumbnail":
        path = project_file(record.get("project") or "", step["to"])
        if path is None:
            raise LookupError(f"miniatura {step['to']} não encontrada (projeto ou arquivo)")
        set_thumbnail(service, video_id, path)
    else:
        set_title(service, video_id, step["to"])
    entry = {"kind": kind, "from": step["from"], "to": step["to"],
             "at": (now or _now()).replace(microsecond=0).isoformat(), "before": before}
    publications.log_swap(video_id, entry)
    return entry


def set_thumbnail(service, video_id: str, path: Path) -> None:
    from googleapiclient.http import MediaIoBaseUpload
    with open(path, "rb") as handle:
        service.thumbnails().set(videoId=video_id, media_body=MediaIoBaseUpload(handle, mimetype="image/jpeg")).execute()


def set_title(service, video_id: str, title: str) -> dict:
    """videos.update with the new title; the rest of the snippet goes back unchanged (the API requires it)."""
    items = service.videos().list(part="snippet", id=video_id).execute().get("items", [])
    if not items:
        raise LookupError(f"video {video_id} not found on this channel")
    current = items[0]["snippet"]
    snippet = {"title": title, "description": current.get("description", ""), "tags": current.get("tags", []),
               "categoryId": current.get("categoryId", config.VIDEO_CATEGORY_ID)}
    for key in ("defaultLanguage", "defaultAudioLanguage"):
        if current.get(key):
            snippet[key] = current[key]
    service.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
    return snippet


def export_test_compare(video_id: str, out_root=None) -> Path:
    """exports/test_compare/<slug>/: the three thumbnails (1280x720 JPEGs) and a titles.txt with the scored titles."""
    from uploader import analytics
    record = next((item for item in analytics.published_records() if item["video_id"] == video_id), None)
    if record is None or not record.get("project"):
        raise LookupError(f"{video_id} não tem projeto no registro")
    slug = record["project"]
    found, _ = variants(slug)
    if not found:
        raise LookupError(f"{slug} não tem variantes de miniatura")
    folder = Path(out_root or Path(config.PUBLICATIONS_FILE).parent.parent / "exports" / "test_compare") / slug
    folder.mkdir(parents=True, exist_ok=True)
    for item in found[:3]:
        source = project_file(slug, item["path"])
        if source:
            shutil.copyfile(source, folder / f"thumbnail_{item['number']}.jpg")
    options, chosen = titles(slug)
    lines = [f"Vídeo: https://www.youtube.com/watch?v={video_id}", f"Título atual: {record.get('title') or chosen}", "",
             "Títulos para testar (Test & Compare decide por tempo assistido por impressão):"]
    lines += [f"- {title}" for title in options[:3]]
    lines += ["", "Miniaturas: " + ", ".join(f"thumbnail_{item['number']}.jpg ({item['text']})" for item in found[:3])]
    (folder / "titles.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder
