"""The retention learning loop (Phase 4.0-D).

For each long-form video the YouTube Analytics API gives the audience retention curve
(``audienceWatchRatio`` and ``relativeRetentionPerformance`` by ``elapsedVideoTimeRatio``, cached with the rest in
``data/analytics/<id>.json``). Here:

* ``drops`` — the biggest falls of the curve (slope over a short window), with their time in the video;
* ``locate`` — each time mapped to the scene (``scene_timeline.json``), its section type (opening, setup…, from
  ``script.json``) and the sentence being narrated (``narration_words.json``);
* ``lessons_for`` — a short "Lições de retenção" per video ("queda de 18 % aos 0:42, no fim da abertura…");
* ``digest`` — ``data/analytics/retention_lessons.json`` over the last 10 videos; ``guidance`` turns its 3 most
  consistent lessons into soft guidance for the outline and screenplay prompts, never a hard rule.

The very start of every curve falls (people sample the first seconds); the first 3 % of the video is left out.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agents.narrative import atomic_write_json, load_json

DIGEST_FILE = "retention_lessons.json"
SKIP_START = 0.03            # the first 3 % always falls
WINDOW = 3                   # points of the curve per slope
MIN_DROP = 0.04              # 4 points of audience lost in the window
TOP = 3
MAX_VIDEOS = 10
LABELS_PT = {"opening": "abertura", "setup": "apresentação", "development": "desenvolvimento", "reversal": "virada",
             "climax": "clímax", "resolution": "resolução", "aftermath": "desfecho", "cold_open": "cold open"}
LABELS_EN = {"opening": "the opening", "setup": "the setup", "development": "a development scene", "reversal": "the reversal",
             "climax": "the climax", "resolution": "the resolution", "aftermath": "the aftermath", "cold_open": "the cold open"}
WHERE_PT = {"start": "no começo", "middle": "no meio", "end": "no fim"}
WHERE_EN = {"start": "at the start of", "middle": "in the middle of", "end": "at the end of"}
ADVICE = {"start": "make its first sentence carry the story forward",
          "middle": "cut what repeats there and keep each sentence changing the situation",
          "end": "land the turn into what comes next in one sentence"}


def fetch_curve(analytics, video_id: str, start: str, end: str) -> list[dict]:
    """[{"ratio", "watch", "relative"}] sorted by the elapsed-time ratio (YouTube Analytics API v2)."""
    response = analytics.reports().query(
        ids="channel==MINE", startDate=start, endDate=end, metrics="audienceWatchRatio,relativeRetentionPerformance",
        dimensions="elapsedVideoTimeRatio", filters=f"video=={video_id}", sort="elapsedVideoTimeRatio").execute()
    names = [header["name"] for header in response.get("columnHeaders", [])]
    rows = [dict(zip(names, row)) for row in response.get("rows") or []]
    return [{"ratio": float(row["elapsedVideoTimeRatio"]), "watch": float(row.get("audienceWatchRatio") or 0),
             "relative": float(row["relativeRetentionPerformance"]) if row.get("relativeRetentionPerformance") is not None else None}
            for row in sorted(rows, key=lambda item: float(item["elapsedVideoTimeRatio"]))]


def drops(curve: list[dict], duration: float, *, top: int = TOP) -> list[dict]:
    """The biggest falls (no overlap), largest first: {"ratio", "seconds", "drop", "from", "to"}."""
    points = [point for point in curve if point["ratio"] >= SKIP_START]
    found = []
    for index in range(len(points) - WINDOW):
        before, after = points[index], points[index + WINDOW]
        fall = before["watch"] - after["watch"]
        if fall >= MIN_DROP:
            found.append({"ratio": points[index + 1]["ratio"], "start_ratio": before["ratio"], "end_ratio": after["ratio"],
                          "drop": round(fall, 3),
                          "from": round(before["watch"], 3), "to": round(after["watch"], 3)})
    chosen = []
    for item in sorted(found, key=lambda value: -value["drop"]):
        if all(item["end_ratio"] <= other["start_ratio"] or item["start_ratio"] >= other["end_ratio"] for other in chosen):
            chosen.append(item)
        if len(chosen) == top:
            break
    for item in chosen:
        item["seconds"] = round(item["ratio"] * duration, 1)
    return chosen


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def sentences(words: list[dict]) -> list[dict]:
    """[{"start", "end", "text", "scene_id"}] from narration_words.json."""
    found, current = [], []
    for word in words:
        current.append(word)
        if word.get("sentence_end"):
            found.append(current)
            current = []
    if current:
        found.append(current)
    return [{"start": float(group[0]["start"]), "end": float(group[-1]["end"]), "scene_id": group[0].get("scene_id"),
             "text": " ".join(str(word["text"]) for word in group)} for group in found]


def locate(seconds: float, timeline: dict, script: dict, words: list[dict]) -> dict:
    """Scene, section type, where in the scene (start/middle/end), the sentence and how many came before it."""
    scenes = (timeline or {}).get("scenes") or []
    scene = next((item for item in scenes if float(item["start"]) <= seconds < float(item["end"])), scenes[-1] if scenes else {})
    sections = [item for item in (script or {}).get("sections", []) if str(item.get("narration", "")).strip()] or \
        (script or {}).get("sections", [])
    number = int(scene.get("scene_id", 0) or 0)
    section = sections[number - 1] if 0 < number <= len(sections) else {}
    kind = section.get("section_type", "")
    if number == 1 and section.get("section_type") == "cold_open":
        kind = "cold_open"
    start, end = float(scene.get("start", 0)), float(scene.get("end", seconds or 1))
    share = (seconds - start) / max(1e-6, end - start)
    where = "start" if share < 0.25 else "end" if share > 0.75 else "middle"
    lines = [item for item in sentences(words) if item["scene_id"] in (None, number)]
    current = next((item for item in lines if item["start"] <= seconds <= item["end"] + 0.4), None) or \
        next((item for item in reversed(lines) if item["start"] <= seconds), None)
    before = sum(1 for item in lines if item["end"] <= seconds)
    return {"scene": number, "scene_title": scene.get("title", ""), "section_type": kind, "where": where,
            "sentence": (current or {}).get("text", ""), "sentences_before": before}


def lessons_for(stats: dict, timeline: dict, script: dict, words: list[dict]) -> list[dict]:
    """The video's "Lições de retenção": one per big drop, located in the script."""
    curve = stats.get("retention") or []
    duration = float((timeline or {}).get("total_duration") or 0)
    if not curve or not duration:
        return []
    found = []
    for item in drops(curve, duration):
        place = locate(item["seconds"], timeline, script, words)
        label = LABELS_PT.get(place["section_type"], place["section_type"] or "cena")
        text = (f"queda de {item['drop'] * 100:.0f} % aos {clock(item['seconds'])}, {WHERE_PT[place['where']]} da {label} "
                f"(cena {place['scene']}, \"{place['scene_title']}\")")
        if place["sentence"]:
            text += f"; frase: «{place['sentence'][:120]}»"
        if place["where"] != "start":
            text += f"; {place['sentences_before']} frase(s) da cena antes da queda"
        found.append({**item, **place, "key": f"{place['section_type'] or 'scene'}:{place['where']}", "text": text})
    return found


# ── Channel digest and soft guidance ─────────────────────────────────────────

def digest_path() -> Path:
    from uploader import analytics
    return analytics.folder() / DIGEST_FILE


def update_digest(video_id: str, title: str, lessons: list[dict], published_at: str = "") -> dict:
    """Keep the last 10 videos' lessons and the keys that repeat across them."""
    data = load_json(digest_path()) or {"videos": []}
    videos = [item for item in data.get("videos", []) if item.get("video_id") != video_id]
    videos.append({"video_id": video_id, "title": title, "published_at": published_at,
                   "lessons": [{key: item[key] for key in ("key", "text", "drop", "seconds", "section_type", "where")} for item in lessons]})
    videos = sorted(videos, key=lambda item: str(item.get("published_at", "")))[-MAX_VIDEOS:]
    counts = Counter(key for item in videos for key in {lesson["key"] for lesson in item["lessons"]})
    weights = Counter()
    for item in videos:
        for lesson in item["lessons"]:
            weights[lesson["key"]] += lesson["drop"]
    consistent = [{"key": key, "videos": count, "total_drop": round(weights[key], 3), "guidance": guidance_line(key)}
                  for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], -weights[pair[0]]))]
    value = {"videos": videos, "consistent": consistent, "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    atomic_write_json(digest_path(), value)
    return value


def guidance_line(key: str) -> str:
    kind, _, where = key.partition(":")
    label = LABELS_EN.get(kind, "a scene")
    return f"Viewers of this channel tend to leave {WHERE_EN.get(where, 'during')} {label}: {ADVICE.get(where, 'keep it tight')}."


def guidance(limit: int = 3) -> list[str]:
    """The most consistent lessons (seen in at least two videos), at most ``limit``."""
    data = load_json(digest_path()) or {}
    return [item["guidance"] for item in data.get("consistent", []) if item.get("videos", 0) >= 2][:limit]


def prompt_rule(limit: int = 3) -> str:
    """Soft guidance for the outline and screenplay prompts (empty until the data exists)."""
    lines = guidance(limit)
    if not lines:
        return ""
    return ("\nAUDIENCE RETENTION (soft guidance from this channel's published videos, never a rule that overrides the "
            "story):\n" + "\n".join(f"- {line}" for line in lines))


def learn(record: dict, stats: dict) -> list[dict]:
    """Lessons for one published long-form video from its cached stats and its (possibly archived) project files."""
    from uploader.packaging_swap import project_file
    slug = record.get("project") or ""
    timeline = load_json(project_file(slug, "scene_timeline.json")) if project_file(slug, "scene_timeline.json") else {}
    script = load_json(project_file(slug, "script.json")) if project_file(slug, "script.json") else {}
    words_file = project_file(slug, "narration_words.json")
    words = (load_json(words_file) or {}).get("words", []) if words_file else []
    found = lessons_for(stats, timeline or {}, script or {}, words)
    if found or stats.get("retention"):
        update_digest(record["video_id"], record.get("title", ""), found, str(record.get("published_at", "")))
    return found
