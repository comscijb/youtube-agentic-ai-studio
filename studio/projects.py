"""Projects: one folder per episode, so work in progress can be continued.

Layout: ``projects/<slug>/`` (slug = date + title, e.g.
``2026-10-23-the-man-who-needed-everyone-to-envy-him``) holds the same artifact
names the single ``output/`` folder used to hold, plus ``project.json`` (title,
created/updated, video type, target duration, stage status, published video
id). Channel-wide assets stay outside: ``assets/style_refs``, ``music library``
and ``data/``. Character and location references are per project.

The active project is resolved at runtime — ``--project <slug>`` on a script,
the GUI selection, else the one last opened, else the most recently updated —
and ``activate`` points ``config.OUTPUT_DIR``/``IMAGES_DIR`` at it, so every
module that reads ``config.OUTPUT_DIR`` follows. With no project at all the
legacy ``output/`` folder is used, as before.

Stage status is computed from the artifacts and their ``source_hash`` chain:
``done``, ``stale`` (an upstream artifact changed), ``partial`` (e.g. 5/50
shots), ``missing`` or ``skipped``.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json, stable_hash

PROJECT_FILE = "project.json"
ACTIVE_FILE = ".active"
LEGACY_DIR = "output"
MTIME_SLACK = 1.0          # seconds; artifacts written in one run may share a timestamp

# (stage, label, GUI pipeline steps that (re)build it, optional)
STAGES = (
    ("story_brief", "Story Brief", ("research",), False),
    ("script", "Script", ("script",), False),
    ("narration", "Narration", ("narration",), False),
    ("music", "Music", ("music",), True),
    ("visual_bible", "Visual bible", ("visual_bible",), False),
    ("references", "References", (), False),
    ("shots", "Shots", ("shots",), False),
    ("video", "Video", ("video",), False),
    ("publish", "Publish", (), True),
)
STAGE_KEYS = tuple(stage for stage, *_ in STAGES)
UPSTREAM = {
    "script": ("story_brief",), "narration": ("script",), "music": ("narration",),
    "visual_bible": ("script",), "references": ("visual_bible",),
    "shots": ("narration", "visual_bible", "references"),
    "video": ("narration", "shots"), "publish": ("video",),
}


class ProjectNotFound(LookupError):
    pass


def root() -> Path:
    return Path(getattr(config, "PROJECTS_DIR", "projects"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(title: str, date: str | None = None) -> str:
    text = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode("ascii")
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70].rstrip("-") or "untitled"
    return f"{date or datetime.now().date().isoformat()}-{words}"


def _unique(slug: str) -> str:
    candidate, number = slug, 2
    while (root() / candidate).exists():
        candidate, number = f"{slug}-{number}", number + 1
    return candidate


def path_for(slug: str) -> Path:
    if not slug or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ProjectNotFound(f"invalid project slug {slug!r}")
    path = root() / slug
    if not (path / PROJECT_FILE).is_file():
        raise ProjectNotFound(f"no project {slug!r} in {root()} (python -m scripts.project list)")
    return path


def load(path) -> dict:
    return load_json(Path(path) / PROJECT_FILE) or {}


def save(path, data: dict) -> dict:
    data["updated"] = _now()
    atomic_write_json(Path(path) / PROJECT_FILE, data)
    return data


def create(title: str = "", *, video_type: str = "normal", target_duration: float | None = None,
           date: str | None = None) -> Path:
    """New project folder. Without a title it is provisional until the Story Brief names it."""
    provisional = not str(title or "").strip()
    date = date or datetime.now().date().isoformat()          # local date, as in the slug
    stamp = datetime.now().strftime("%H%M%S")
    slug = _unique(slugify(title or f"untitled-{stamp}", date))
    path = root() / slug
    path.mkdir(parents=True)
    now = _now()
    atomic_write_json(path / PROJECT_FILE, {
        "slug": slug, "title": str(title or "").strip(), "provisional": provisional, "date": date,
        "created": now, "updated": now, "video_type": video_type,
        "target_duration_minutes": target_duration, "skipped": [], "stages": {},
        "published_video_id": None,
    })
    return path


def retitle(path, title: str) -> Path:
    """Give a provisional project its title (and the slug that goes with it)."""
    path = Path(path)
    data = load(path)
    if not data or not data.get("provisional") or not str(title or "").strip():
        return path
    was_active = active_slug() == path.name
    slug = _unique(slugify(title, data.get("date") or str(data.get("created", ""))[:10] or None))
    target = root() / slug
    os.replace(path, target)
    data.update(slug=slug, title=title.strip(), provisional=False)
    save(target, data)
    if was_active:
        set_active(slug)
    return target


def set_active(slug: str) -> None:
    root().mkdir(parents=True, exist_ok=True)
    (root() / ACTIVE_FILE).write_text(slug + "\n", encoding="utf-8")


def active_slug() -> str | None:
    try:
        slug = (root() / ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return slug if slug and (root() / slug / PROJECT_FILE).is_file() else None


def all_paths() -> list[Path]:
    if not root().is_dir():
        return []
    paths = [path for path in root().iterdir() if (path / PROJECT_FILE).is_file()]
    return sorted(paths, key=lambda path: str(load(path).get("updated", "")), reverse=True)


def resolve(slug: str | None = None) -> Path:
    """--project slug, else the active project, else the most recent one, else legacy output/."""
    if slug:
        return path_for(slug)
    active = active_slug()
    if active:
        return root() / active
    paths = all_paths()
    return paths[0] if paths else Path(LEGACY_DIR)


def activate(path) -> Path:
    """Point config.OUTPUT_DIR / IMAGES_DIR at ``path`` (every module reads them at call time)."""
    path = Path(path)
    config.OUTPUT_DIR = str(path)
    config.IMAGES_DIR = str(path / "images")
    return path


def is_project(path) -> bool:
    return (Path(path) / PROJECT_FILE).is_file()


def touch(path, **fields) -> dict | None:
    """Update project.json (when ``path`` is a project) with ``fields`` and fresh stage status."""
    path = Path(path)
    if not is_project(path):
        return None
    data = load(path)
    data.update(fields)
    data["stages"] = {item["stage"]: item["status"] for item in stage_status(path)}
    return save(path, data)


# ── CLI helpers ─────────────────────────────────────────────────────────────

def add_argument(parser) -> None:
    parser.add_argument("--project", metavar="SLUG",
                        help="project folder in projects/ (default: the active or most recent project)")


def cli_output(args, attribute: str = "output_dir") -> Path:
    """Resolve and activate the folder a script works in: --project, an explicit
    --output-dir, else the active/most recent project (legacy output/ without projects)."""
    explicit = getattr(args, attribute, None) if attribute else None
    if getattr(args, "project", None):
        return activate(path_for(args.project))
    if explicit:
        return Path(explicit)          # an explicit folder, as before projects existed
    return activate(resolve())


# ── Stage status ────────────────────────────────────────────────────────────

def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _newer(upstream: list[Path], downstream: Path) -> bool:
    """True when any existing upstream file is newer than ``downstream``."""
    own = _mtime(downstream)
    times = [time for time in (_mtime(path) for path in upstream) if time is not None]
    return own is not None and bool(times) and max(times) > own + MTIME_SLACK


def _entry(stage: str, status: str, detail: str = "") -> dict:
    label, optional = next((label, optional) for key, label, _, optional in STAGES if key == stage)
    return {"stage": stage, "label": label, "status": status, "detail": detail, "optional": optional}


def stage_status(path) -> list[dict]:
    path = Path(path)
    data = load(path)
    skipped = set(data.get("skipped") or [])
    files = {name: path / name for name in (
        "story_brief.json", "research.json", "character_bible.json", "story_outline.json", "script.json",
        "narrative_review.json", "narration.mp3", "narration.srt", "scene_timeline.json",
        "narration_mixed.mp3", "visual_bible.json", "shot_list.json", "final_video.mp4", "final_short.mp4")}
    brief = load_json(files["story_brief.json"]) or load_json(files["research.json"]) or {}
    bible = load_json(files["character_bible.json"]) or {}
    outline = load_json(files["story_outline.json"]) or {}
    script = load_json(files["script.json"]) or {}
    review = load_json(files["narrative_review.json"]) or {}
    vb = load_json(files["visual_bible.json"]) or {}
    shot_list = load_json(files["shot_list.json"]) or {}
    result = []

    result.append(_entry("story_brief", "done" if brief else "missing",
                         brief.get("video_title", "") if brief else "no story brief yet"))

    if not script:
        result.append(_entry("script", "partial" if bible or outline else "missing",
                             "bible/outline only" if bible or outline else ""))
    elif (bible.get("source_hash") and bible["source_hash"] != stable_hash(brief)) or \
            (script.get("source_hash") and script["source_hash"] !=
             stable_hash({"brief": brief, "bible": bible, "outline": outline})):
        result.append(_entry("script", "stale", "the story brief, bible or outline changed"))
    elif review and review.get("source_hash") and review["source_hash"] != stable_hash(script):
        result.append(_entry("script", "stale", "the editorial review is older than the script"))
    else:
        result.append(_entry("script", "done", f"{len(script.get('sections', []))} scenes"))

    narration_files = [files[name] for name in ("narration.mp3", "narration.srt", "scene_timeline.json")]
    present = [item for item in narration_files if item.is_file()]
    if not present:
        result.append(_entry("narration", "missing"))
    elif len(present) < len(narration_files):
        result.append(_entry("narration", "partial", f"{len(present)}/{len(narration_files)} files"))
    elif _newer([files["script.json"]], files["scene_timeline.json"]):
        result.append(_entry("narration", "stale", "the script changed after the narration"))
    else:
        timeline = load_json(files["scene_timeline.json"]) or {}
        result.append(_entry("narration", "done", f"{float(timeline.get('total_duration', 0)) / 60:.1f} min"))

    if not files["narration_mixed.mp3"].is_file():
        result.append(_entry("music", "missing"))
    elif _newer([files["narration.mp3"]], files["narration_mixed.mp3"]):
        result.append(_entry("music", "stale", "the narration changed after the mix"))
    else:
        result.append(_entry("music", "done"))

    from agents import shot_list as shots_module
    from agents import visual_bible as vbible
    if not vb:
        result.append(_entry("visual_bible", "missing"))
    elif script and bible and vb.get("source_hash") != vbible.source_hash_for(script, bible, outline):
        result.append(_entry("visual_bible", "stale", "the script or Character Bible changed"))
    else:
        result.append(_entry("visual_bible", "done", f"{len(vb.get('characters', []))} characters, "
                                                     f"{len(vb.get('locations', []))} locations"))

    if not vb:
        result.append(_entry("references", "missing"))
    else:
        wanted = [item["character_id"] for item in vb.get("characters", [])]
        if shot_list.get("shots"):
            in_shots = {cid for shot in shot_list["shots"] for cid in shot.get("characters") or []}
            wanted = [cid for cid in wanted if cid in in_shots] or wanted
        approved = [cid for cid in wanted if vbible.approved_reference(vb, "character", cid, path)]
        status = "done" if len(approved) == len(wanted) else "partial" if approved else "missing"
        result.append(_entry("references", status, f"{len(approved)}/{len(wanted)} characters approved"))

    shots = [shot for shot in shot_list.get("shots", []) if not shot.get("reuse_of")]
    done = [shot for shot in shots if shot.get("hold_previous")
            or (shot.get("image_path") and (path / shot["image_path"]).is_file())]
    if not shots:
        result.append(_entry("shots", "missing", "no shot list yet"))
    elif script and vb and shot_list.get("source_hash") != shots_module.expected_source_hash(script, vb, path):
        result.append(_entry("shots", "stale", f"narration, visual bible or shot rules changed "
                                               f"({len(done)}/{len(shots)} images)"))
    else:
        status = "done" if len(done) == len(shots) else "partial" if done else "missing"
        result.append(_entry("shots", status, f"{len(done)}/{len(shots)} images"))

    video = files["final_short.mp4"] if files["final_short.mp4"].is_file() else files["final_video.mp4"]
    if not video.is_file():
        result.append(_entry("video", "missing"))
    else:
        inputs = [files["narration_mixed.mp3"], files["narration.mp3"], files["shot_list.json"]]
        inputs += [path / shot["image_path"] for shot in done if shot.get("image_path")]
        result.append(_entry("video", "stale", "narration or shots changed after the render")
                      if _newer(inputs, video) else _entry("video", "done"))

    published = data.get("published_video_id")
    result.append(_entry("publish", "done" if published else "missing",
                         f"https://youtu.be/{published}" if published else ""))

    for item in result:
        if item["stage"] in skipped and item["status"] != "done":
            item["status"] = "skipped"
    return result


def next_step(statuses: list[dict]) -> dict | None:
    """First stage that needs work (optional stages only when stale or partial)."""
    for item in statuses:
        if item["status"] in {"done", "skipped"}:
            continue
        if item["optional"] and item["status"] == "missing":
            continue
        return item
    return None


def blocking_upstream(statuses: list[dict], stage: str) -> list[dict]:
    """Upstream stages (transitively) that are stale or missing before ``stage`` runs."""
    by_stage = {item["stage"]: item for item in statuses}
    seen, order, pending = set(), [], list(UPSTREAM.get(stage, ()))
    while pending:
        key = pending.pop(0)
        if key in seen:
            continue
        seen.add(key)
        pending.extend(UPSTREAM.get(key, ()))
        item = by_stage.get(key)
        if item and item["status"] in {"stale", "missing", "partial"} and not (
                item["optional"] and item["status"] == "missing"):
            order.append(item)
    return sorted(order, key=lambda item: STAGE_KEYS.index(item["stage"]))


def summary(path) -> dict:
    path = Path(path)
    data = load(path)
    statuses = stage_status(path)
    thumbnail = next((name for name in ("thumbnail.jpg", "thumbnail_bg.jpg") if (path / name).is_file()), None)
    return {
        "slug": path.name, "title": data.get("title") or path.name, "created": data.get("created"),
        "updated": data.get("updated"), "video_type": data.get("video_type", "normal"),
        "target_duration_minutes": data.get("target_duration_minutes"),
        "provisional": bool(data.get("provisional")), "published_video_id": data.get("published_video_id"),
        "thumbnail": thumbnail, "stages": statuses, "next_step": next_step(statuses),
    }


def steps_for(stage: str) -> tuple:
    return next(steps for key, _, steps, _ in STAGES if key == stage)


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
