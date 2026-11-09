"""Variation guard across the channel (Phase 3.9-C): warnings only, never a block.

YouTube's "inauthentic content" policy is about minimal variation across videos. Each episode gets a
profile — ``theme_tags`` (the first is the primary theme), the narrative type and the opening device
— and a new episode is compared with the published ones:

* **theme rotation:** its primary tag equals the primary tag of either of the last 2 published
  episodes; "loss/death of a loved one" central in 2 of the last 3 episodes (the new one included);
* **structure rotation:** the last 3 episodes (the new one included) share the narrative type *and*
  the opening device — the outline stage is then asked for another opening device;
* **title shape:** same first word, same frame ("He … to …"), or a close character match with a
  published title ("parecido com: …"); the 3.6 title scoring penalises those;
* **thumbnail composition:** the same template as the previous episode;
* **cadence:** more than 2 episodes a week or 1 Short a day (shown in the publication dialog).

Source of truth: the publication ledger plus each episode's archived ``story_brief.json`` and
``script.json``. Published episodes without a profile are backfilled with one Gemini call each,
cached in ``data/episode_profiles.json`` and editable in the GUI.
"""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json

TAGS = ("loss/grief", "loneliness", "status/envy", "self-erasure", "control", "technology-intimacy",
        "identity", "ambition", "guilt/shame", "family", "love/intimacy", "fear/paranoia", "freedom")
LOSS = "loss/grief"
DEVICES = ("cold-open line", "object", "dialogue", "question")
KEYWORDS = {
    "loss/grief": r"\b(?:grief|griev\w*|mourn\w*|widow\w*|death|dead|loss|lost (?:his|her) (?:wife|husband|son|daughter|mother|father)|funeral|grave)\b",
    "loneliness": r"\b(?:lonel\w*|alone|isolat\w*|belonging)\b",
    "status/envy": r"\b(?:envy|envied|status|prestige|admir\w*|social comparison)\b",
    "self-erasure": r"\b(?:people.pleas\w*|self.erasure|never says no|says yes|approval)\b",
    "control": r"\b(?:control\w*|perfection\w*|order)\b",
    "technology-intimacy": r"\b(?:\bai\b|app|phone|chatbot|companion app|algorithm|screen)\b",
    "identity": r"\b(?:identity|authentic\w*|who (?:he|she) is)\b",
    "ambition": r"\b(?:ambition|ambitious|career|success)\b",
    "guilt/shame": r"\b(?:guilt\w*|shame\w*|regret\w*)\b",
    "family": r"\b(?:daughter|son|sister|brother|mother|father|family)\b",
    "love/intimacy": r"\b(?:love|intima\w*|marriage|vulnerab\w*)\b",
    "fear/paranoia": r"\b(?:fear\w*|paranoi\w*|dread)\b",
    "freedom": r"\b(?:freedom|free|escape)\b",
}
PROFILES_FILE = Path(__file__).resolve().parent.parent / "data" / "episode_profiles.json"
MAX_EPISODES_PER_WEEK = 2
MAX_SHORTS_PER_DAY = 1


# ── Profiles ─────────────────────────────────────────────────────────────────

def normalise_tags(value, text: str = "") -> list[str]:
    """Known tags from ``value`` (a list or a string), else guessed from ``text`` by keywords."""
    items = value if isinstance(value, list) else re.split(r"[,;]", str(value or ""))
    tags = []
    for item in items:
        item = str(item).strip().lower()
        match = next((tag for tag in TAGS if item == tag or item == tag.split("/")[0]), None)
        if match and match not in tags:
            tags.append(match)
    if not tags and text:
        scored = [(len(re.findall(KEYWORDS[tag], text, re.I)), tag) for tag in TAGS]
        tags = [tag for count, tag in sorted(scored, key=lambda item: -item[0]) if count][:3]
    return tags[:3]


def opening_device(script: dict) -> str:
    """How the first scene opens: dialogue, a question, an object in close focus, or a cold-open line."""
    sections = (script or {}).get("sections") or []
    text = str(sections[0].get("narration", "")).strip() if sections else ""
    first = re.split(r"(?<=[.!?…])\s+", text, maxsplit=1)[0] if text else ""
    if first[:1] in ('"', "“"):
        return "dialogue"
    if first.endswith("?"):
        return "question"
    if re.match(r"^(?:The|A|An)\s+\w+(?:\s+\w+)?\s+(?:sat|lay|stood|hung|rested|waited|glowed|ticked|rang)\b", first):
        return "object"
    return "cold-open line"


def profile(story_brief: dict, script: dict | None = None, thumbnails: dict | None = None, outline: dict | None = None) -> dict:
    """The episode's variation profile; ``cold_open`` is its mode and the kind of moment it shows (Phase 3.9.7)."""
    from agents import cold_open
    brief = story_brief or {}
    text = " ".join(str(brief.get(key, "")) for key in ("psychological_theme", "psychological_angle", "logline", "topic"))
    tags = normalise_tags(brief.get("theme_tags"), text)
    selected = next((item for item in (thumbnails or {}).get("variants", [])
                     if item.get("number") == (thumbnails or {}).get("selected")), None)
    return {"theme_tags": tags, "primary": tags[0] if tags else "", "narrative_type": brief.get("narrative_type", ""),
            "opening_device": opening_device(script) if script else brief.get("opening_device", ""),
            "title": (script or {}).get("title") or brief.get("video_title", ""),
            "thumbnail_composition": (selected or {}).get("concept", {}).get("composition", ""),
            "cold_open": cold_open.moment(outline) if outline else brief.get("cold_open", {"mode": "off", "moment": ""})}


def load_profiles(path=None) -> dict:
    data = load_json(Path(path or getattr(config, "EPISODE_PROFILES_FILE", None) or PROFILES_FILE)) or {}
    return data.get("episodes", {}) if isinstance(data, dict) else {}


def save_profile(video_id: str, value: dict, path=None) -> dict:
    target = Path(path or getattr(config, "EPISODE_PROFILES_FILE", None) or PROFILES_FILE)
    data = {"episodes": load_profiles(target)}
    data["episodes"][video_id] = value
    atomic_write_json(target, data)
    return value


def episodes(ledger: list[dict]) -> list[dict]:
    """Published (or scheduled/uploaded) episodes, oldest first; Shorts excluded."""
    items = [item for item in ledger if not item.get("parent_video_id") and item.get("kind") != "short"
             and item.get("state") in ("published", "uploaded_pending_publication", "scheduled")]
    return sorted(items, key=lambda item: str(item.get("published_at") or item.get("uploaded_at") or ""))


def episode_folder(record: dict) -> Path | None:
    """Where the episode's story_brief.json/script.json live: the project, else its archive."""
    from studio import projects
    slug = record.get("project")
    candidates = []
    if slug:
        folder = projects.root() / slug
        candidates.append(folder)
        archived = projects.load(folder).get("archive_path") if projects.is_project(folder) else None
        if archived:
            candidates += [Path(archived) / "text", Path(archived)]
    return next((folder for folder in candidates if (folder / "story_brief.json").is_file()), None)


def backfill_prompt(record: dict, brief: dict, script: dict) -> str:
    first = ((script or {}).get("sections") or [{}])[0].get("narration", "")[:600]
    return (f"Classify this published episode. Return ONLY JSON {{\"theme_tags\": [1-3 of {list(TAGS)}, primary "
            f"first], \"opening_device\": one of {list(DEVICES)}}}.\nTITLE: {record.get('title')}\n"
            f"THEME: {brief.get('psychological_theme')} — {brief.get('psychological_angle')}\nLOGLINE: "
            f"{brief.get('logline')}\nFIRST SCENE OPENING: {first}")


def backfill(ledger: list[dict], *, generator=None, path=None) -> dict:
    """Profiles for published episodes that have none: one Gemini call each, cached (edits kept)."""
    profiles = load_profiles(path)
    for record in episodes(ledger):
        if record["video_id"] in profiles:
            continue
        folder = episode_folder(record)
        brief = load_json(folder / "story_brief.json") if folder else {}
        brief = brief or {key: record.get(key, "") for key in ("psychological_theme", "psychological_angle",
                                                              "logline", "narrative_type")}
        script = (load_json(folder / "script.json") if folder else None) or {"title": record.get("title")}
        value = profile(brief, script)
        if generator is not None:
            from agents.narrative import extract_json
            try:
                answer = extract_json(generator(backfill_prompt(record, brief, script), stage="variation_backfill"),
                                      "episode profile")
                value["theme_tags"] = normalise_tags(answer.get("theme_tags")) or value["theme_tags"]
                value["primary"] = value["theme_tags"][0] if value["theme_tags"] else ""
                if answer.get("opening_device") in DEVICES:
                    value["opening_device"] = answer["opening_device"]
            except Exception:                            # noqa: BLE001 — the deterministic profile stays
                pass
        value.update(title=record.get("title", value["title"]), source="backfill")
        profiles[record["video_id"]] = save_profile(record["video_id"], value, path)
    return profiles


def history(ledger: list[dict], path=None) -> list[dict]:
    """Published episode profiles, oldest first (cached profiles; titles from the ledger)."""
    profiles = load_profiles(path)
    return [{**profiles.get(record["video_id"], {}), "video_id": record["video_id"], "title": record.get("title", "")}
            for record in episodes(ledger)]


# ── Checks ───────────────────────────────────────────────────────────────────

def _frame(title: str) -> str:
    """The syntactic frame of a title: function words kept, content words → "…" ("He … to …")."""
    keep = {"he", "she", "they", "i", "we", "the", "a", "an", "to", "who", "for", "of", "his", "her", "and",
            "with", "in", "on", "never", "always"}
    words = re.findall(r"[a-z']+", str(title).lower())
    frame, last = [], None
    for word in words:
        token = word if word in keep else "…"
        if token != last:
            frame.append(token)
        last = token
    return " ".join(frame)


def title_similarity(title: str, published: list[str]) -> list[dict]:
    """Published titles this one looks like: same first word, same frame, or a close character match."""
    found = []
    first = (re.findall(r"[a-z']+", str(title).lower()) or [""])[0]
    for other in published:
        if not other or other.strip().lower() == str(title).strip().lower():
            continue
        reasons = []
        other_first = (re.findall(r"[a-z']+", other.lower()) or [""])[0]
        if first and first == other_first:
            reasons.append("same first word")
        if _frame(title) == _frame(other):
            reasons.append("same frame")
        ratio = difflib.SequenceMatcher(a=str(title).lower(), b=other.lower()).ratio()
        if ratio >= 0.6:
            reasons.append(f"similar wording ({ratio:.0%})")
        if reasons:
            found.append({"title": other, "reasons": reasons, "ratio": round(ratio, 2)})
    return found


TITLE_PENALTY = 8               # points off the 0-100 title score per similarity signal


def title_penalty(title: str, published: list[str]) -> tuple[int, str]:
    """(points to subtract, the most similar published title) for the 3.6 title scoring."""
    matches = title_similarity(title, published)
    if not matches:
        return 0, ""
    closest = max(matches, key=lambda item: (len(item["reasons"]), item["ratio"]))
    return TITLE_PENALTY * len(closest["reasons"]), closest["title"]


def checks(current: dict, past: list[dict]) -> list[dict]:
    """Warnings for the new episode against the published ones (most recent last in ``past``)."""
    warnings = []
    recent = [item for item in past if item.get("primary") or item.get("theme_tags")]
    last_two = recent[-2:]
    if current.get("primary") and any(item.get("primary") == current["primary"] for item in last_two):
        twin = next(item for item in reversed(last_two) if item.get("primary") == current["primary"])
        warnings.append({"kind": "theme", "message": f"Mesmo tema principal ({current['primary']}) de “{twin.get('title')}”."})
    window = recent[-2:] + [current]
    if len(window) >= 2 and sum(1 for item in window if LOSS in (item.get("theme_tags") or [])) >= 2:
        warnings.append({"kind": "loss", "message": "Perda/morte de alguém querido é central em 2 dos últimos 3 episódios."})
    structure = past[-2:] + [current]
    if len(structure) == 3 and current.get("narrative_type") and current.get("opening_device") and all(
            item.get("narrative_type") == current["narrative_type"] and item.get("opening_device") ==
            current["opening_device"] for item in structure):
        warnings.append({"kind": "structure", "message": f"Os últimos 3 episódios têm o mesmo tipo e a mesma "
                                                         f"abertura ({current['opening_device']})."})
    moments = [(item.get("cold_open") or {}).get("moment") for item in past[-2:]] + [(current.get("cold_open") or {}).get("moment")]
    if len(moments) == 3 and moments[0] and all(moment == moments[0] for moment in moments):
        warnings.append({"kind": "cold_open", "message": f"Os últimos 3 episódios abrem com o mesmo tipo de momento no "
                                                         f"cold open ({moments[0]})."})
    moments = [(item.get("cold_open") or {}).get("moment") for item in past[-2:]] + [(current.get("cold_open") or {}).get("moment")]
    if len(moments) == 3 and moments[0] and all(moment == moments[0] for moment in moments):
        warnings.append({"kind": "cold_open", "message": f"Os últimos 3 episódios abrem com o mesmo tipo de momento no "
                                                         f"cold open ({moments[0]})."})
    for similar in title_similarity(current.get("title", ""), [item.get("title", "") for item in past]):
        warnings.append({"kind": "title", "message": f"Título parecido com: {similar['title']} "
                                                     f"({', '.join(similar['reasons'])})."})
    if past and current.get("thumbnail_composition") and \
            past[-1].get("thumbnail_composition") == current["thumbnail_composition"]:
        warnings.append({"kind": "thumbnail", "message": f"Mesma composição de miniatura do episódio anterior "
                                                         f"({current['thumbnail_composition']})."})
    return warnings


def opening_hint(past: list[dict]) -> str:
    """For the outline stage: when the last 2 episodes opened the same way, ask for another device."""
    devices = [item.get("opening_device") for item in past[-2:] if item.get("opening_device")]
    if len(devices) == 2 and devices[0] == devices[1]:
        others = [device for device in DEVICES if device != devices[0]]
        return (f"VARIATION: the last two episodes opened with a {devices[0]}; open this one with a different "
                f"device ({', '.join(others)}).")
    return ""


# ── Cadence ──────────────────────────────────────────────────────────────────

def _when(record: dict) -> datetime | None:
    value = record.get("scheduled_publish_at") if record.get("scheduled_publish_at") not in (None, "", "None") \
        else record.get("published_at") or record.get("uploaded_at")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def cadence(ledger: list[dict], planned: list[dict] | None = None, now: datetime | None = None) -> dict:
    """Episodes in the 7 days around ``now`` and the busiest day of Shorts, ledger + ``planned`` uploads
    ({"kind": "episode" | "short", "at": datetime})."""
    now = now or datetime.now(timezone.utc)
    events = [{"kind": "short" if record.get("parent_video_id") or record.get("kind") == "short" else "episode",
               "at": _when(record)} for record in ledger]
    events += list(planned or [])
    events = [item for item in events if item["at"] is not None]
    end = max([now] + [item["at"] for item in planned or [] if item.get("at")])     # the last planned upload
    window = [item for item in events if end - timedelta(days=7) < item["at"] <= end]
    episodes_week = sum(1 for item in window if item["kind"] == "episode")
    per_day: dict = {}
    for item in window:
        if item["kind"] == "short":
            per_day[item["at"].date()] = per_day.get(item["at"].date(), 0) + 1
    busiest = max(per_day.values(), default=0)
    warnings = []
    if episodes_week > MAX_EPISODES_PER_WEEK:
        warnings.append(f"{episodes_week} episódios em 7 dias (recomendado: até {MAX_EPISODES_PER_WEEK} por semana).")
    if busiest > MAX_SHORTS_PER_DAY:
        warnings.append(f"{busiest} Shorts no mesmo dia (recomendado: até {MAX_SHORTS_PER_DAY} por dia).")
    return {"episodes_per_week": episodes_week, "shorts_per_day": busiest, "warnings": warnings}


def report(output_dir, ledger: list[dict], path=None) -> dict:
    """Profile and warnings of the episode in ``output_dir`` (story brief, script, thumbnails)."""
    output = Path(output_dir)
    current = profile(load_json(output / "story_brief.json") or load_json(output / "research.json") or {},
                      load_json(output / "script.json"), load_json(output / "thumbnails" / "thumbnails.json"),
                      load_json(output / "story_outline.json"))
    past = [item for item in history(ledger, path) if item.get("title") != current.get("title")]
    return {"profile": current, "history": past, "warnings": checks(current, past)}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
