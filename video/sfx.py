"""Sound design (Phase 4.2-D): a licensed SFX library, a cue sheet per episode, levels for the mix.

* **Library** — ``assets/sfx/<tag>/<file>`` (or ``<tag>_<name>.wav`` at the top level), only licences that allow
  commercial use without attribution (CC0, the YouTube Audio Library's sound effects). Every file used must be in
  ``assets/sfx/LICENSES.json`` (``{"<relative path>": {"source", "license", "url"}}``); an unregistered file is
  never used. ``python -m scripts.sfx_library`` lists the missing tags; nothing is ever scraped.
* **Cue sheet** — one semantic pass over the screenplay writes ``sfx_cues.json``: actions with a sound (door, phone,
  bell, coins, pages, steps), an ambience bed per location (looped, crossfaded at scene changes) and accents (a soft
  impact on reveals, a riser into the cold open's cut, a heartbeat or drone under tension). Every cue carries the
  quote it rests on (verified in the narration) and is timed from the word timings; at most ~4 accents per minute.
* **Levels** — beds about 26 dB under the voice, actions 18 dB, accents 14 dB; everything is ducked by the narration
  in the mix (``video.sound``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import config
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash

TAGS = ("door", "footsteps", "phone_buzz", "bell", "paper", "wind", "rain", "river", "fire", "clock", "crowd_murmur",
        "heartbeat", "impact", "riser", "whoosh", "coins", "room_tone", "drone", "glass", "water", "birds", "car")
BEDS = {"wind", "rain", "river", "fire", "crowd_murmur", "room_tone", "water", "birds", "drone"}
ACCENTS = {"impact", "riser", "whoosh", "heartbeat", "drone"}
UNDER_VOICE_DB = {"bed": 26.0, "action": 18.0, "accent": 14.0}
ACCENTS_PER_MINUTE = 4
ALLOWED_LICENSES = ("cc0", "public domain", "youtube audio library", "pixabay content license")
EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac", ".m4a")
FILE_NAME = "sfx_cues.json"
VERSION = 1


def folder() -> Path:
    return Path(getattr(config, "SFX_DIR", Path(__file__).resolve().parent.parent / "assets" / "sfx"))


def enabled() -> bool:
    return bool(getattr(config, "SOUND_DESIGN_ENABLED", True))


def licenses(root: Path | None = None) -> dict:
    return load_json((root or folder()) / "LICENSES.json") or {}


def license_ok(entry: dict | None) -> bool:
    text = str((entry or {}).get("license", "")).lower()
    return bool(entry) and any(name in text for name in ALLOWED_LICENSES) and "attribution required" not in text


def library(root: Path | None = None) -> dict[str, list[dict]]:
    """{tag: [{"path", "relative", "license"}]}: registered files with an allowed licence only."""
    root = root or folder()
    registry = licenses(root)
    found: dict[str, list[dict]] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in EXTENSIONS:
            continue
        relative = path.relative_to(root).as_posix()
        tag = relative.split("/")[0] if "/" in relative else re.split(r"[_\-. ]", path.stem)[0].lower()
        if tag not in TAGS:
            continue
        entry = registry.get(relative)
        if not license_ok(entry):
            continue
        found.setdefault(tag, []).append({"path": str(path), "relative": relative, "license": entry.get("license")})
    return found


def missing_tags(root: Path | None = None) -> list[str]:
    have = library(root)
    return [tag for tag in TAGS if tag not in have]


def unregistered(root: Path | None = None) -> list[str]:
    """Audio files in the folder that are not in LICENSES.json (or have a licence that is not allowed)."""
    root = root or folder()
    registry = licenses(root)
    if not root.is_dir():
        return []
    return [path.relative_to(root).as_posix() for path in sorted(root.rglob("*")) if path.suffix.lower() in EXTENSIONS
            and not license_ok(registry.get(path.relative_to(root).as_posix()))]


# ── The cue sheet ───────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", str(text or "").lower().replace("’", "'"))).strip()


def contract() -> str:
    return f"""You design the sound of one narrated illustrated story, sparingly, like a film mixer. For the numbered
SENTENCES return cues of three kinds:
- "action": a sound the narration describes happening (a door, a buzzing phone, a bell, coins, pages, steps);
- "bed": the ambience of a scene's location (room tone, rain, river, wind), one per scene, on its first sentence;
- "accent": a soft impact on a reveal, a riser into the cut after the cold open, a heartbeat or low drone under
  tension. At most {ACCENTS_PER_MINUTE} accents per minute; silence is part of the design.
Tags: {list(TAGS)}. Each cue: {{"kind": "action"|"bed"|"accent", "tag": one tag, "sentence": index, "quote": the exact
words of that sentence the sound sits on (copied)}}.
Return ONLY JSON {{"cues": [...]}}."""


def validate(value: dict, sentences: list[dict]) -> list[dict]:
    items = value.get("cues") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise NarrativeValidationError("sfx cue sheet needs a cues array")
    by_index = {item["index"]: item for item in sentences}
    kept = []
    for cue in items:
        if not isinstance(cue, dict) or cue.get("kind") not in ("action", "bed", "accent") or cue.get("tag") not in TAGS:
            continue
        sentence = by_index.get(cue.get("sentence"))
        quote = str(cue.get("quote", "")).strip()
        if sentence is None or not quote or _norm(quote) not in _norm(sentence["text"]):
            continue                                     # a quote that is not in the narration is not a cue
        kept.append({"kind": cue["kind"], "tag": cue["tag"], "sentence": sentence["index"], "quote": quote,
                     "scene": sentence.get("section")})
    return kept


def time_cues(cues: list[dict], words: list[dict], sentences: list[dict], scene_bounds: list[tuple[float, float]]) -> list[dict]:
    """Give every cue its time from the word timings (the quote's first word); beds span their scene."""
    from video.key_lines import find_span
    found = []
    for cue in cues:
        span = find_span(words, cue["quote"])
        if span is None:
            continue
        start = float(words[span[0]]["start"])
        if cue["kind"] == "bed":
            scene = next(((low, high) for low, high in scene_bounds if low <= start < high), None)
            if scene is None:
                continue
            found.append({**cue, "start": round(scene[0], 3), "end": round(scene[1], 3)})
        elif cue["kind"] == "accent" and cue["tag"] == "riser":
            found.append({**cue, "start": round(max(0.0, start - 1.6), 3), "end": round(start, 3)})
        else:
            found.append({**cue, "start": round(start, 3), "end": round(float(words[span[1]]["end"]) + 1.5, 3)})
    return cap_accents(found)


def cap_accents(cues: list[dict], per_minute: int = ACCENTS_PER_MINUTE, gap: float = 1.0) -> list[dict]:
    """At most ``per_minute`` accents in any minute and no accent within ``gap`` s of another."""
    kept, accents = [], []
    for cue in sorted(cues, key=lambda item: item["start"]):
        if cue["kind"] != "accent":
            kept.append(cue)
            continue
        recent = [time for time in accents if cue["start"] - time < 60.0]
        if len(recent) >= per_minute or any(abs(cue["start"] - time) < gap for time in accents):
            continue
        accents.append(cue["start"])
        kept.append(cue)
    return kept


def build(output_dir, sections: list[dict], *, generator=None, force: bool = False) -> dict:
    """sfx_cues.json for the episode (one Gemini call, cached on the narration)."""
    from agents import attention
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    output = Path(output_dir)
    words = (load_json(output / "narration_words.json") or {}).get("words", [])
    timeline = load_json(output / "scene_timeline.json") or {}
    sentences = attention.timing(sections)
    key = stable_hash({"narration": [item["text"] for item in sentences], "version": VERSION})
    path = output / FILE_NAME
    saved = load_json(path) or {}
    if saved.get("key") == key and not force:
        return saved
    numbered = [{"index": item["index"], "scene": item["section"], "type": item["section_type"], "text": item["text"]} for item in sentences]
    cues = generate_json(contract(), "sfx cues", lambda value: validate(value, sentences), generator=generator or generate,
                         context=f"SENTENCES: {compact_json(numbered)}", stage="sfx_cues", temperature_key="editorial_review",
                         run_dir=str(output))
    bounds = [(float(scene["start"]), float(scene["end"])) for scene in timeline.get("scenes", [])]
    record = {"key": key, "cues": time_cues(cues, words, sentences, bounds)}
    atomic_write_json(path, record)
    return record


def assign_files(cues: list[dict], root: Path | None = None) -> tuple[list[dict], list[str]]:
    """Each cue with a registered file of its tag (rotating), and the tags that have none."""
    available = library(root)
    used: dict[str, int] = {}
    placed, missing = [], []
    for cue in cues:
        files = available.get(cue["tag"]) or []
        if not files:
            missing.append(cue["tag"])
            continue
        index = used.get(cue["tag"], 0)
        used[cue["tag"]] = index + 1
        placed.append({**cue, "file": files[index % len(files)]["path"], "under_voice_db": UNDER_VOICE_DB[cue["kind"]]})
    return placed, sorted(set(missing))


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)
