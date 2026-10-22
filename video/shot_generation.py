"""Episode image generation: references, approvals, shots and the thumbnail image.

Order of work (each step resumable, every image cached by content hash):

1. ``generate_reference_candidates`` — 4 chest-up portraits per character
   (front and three-quarter, neutral-to-dark backdrop, immutable descriptor,
   episode wardrobe) and 2 establishing images per recurring location.
2. ``approve_reference`` — the user picks one per character/location (GUI or
   CLI); it becomes ``character_refs/<id>.png`` / ``location_refs/<id>.png``
   and is recorded with seed and prompt in ``visual_bible.json``.
3. ``generate_shots`` — refuses to start until every character in the shot
   list has an approved portrait; generates unique shots (``reuse_of`` shots
   share an image), retries once with a new seed, falls back to stock
   (objects/environments only), updates ``shot_list.json`` after each shot and
   records provenance (``synthetic: true``) in ``asset_manifest.json``.
4. ``generate_thumbnail_image`` — a dedicated 1280x720 image from the visual
   bible's thumbnail shot.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image

import config
from agents import identity
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from video.imagegen_comfyui import ComfyUnavailable, GenerationFailed, ImageGenerator, ImageJob

PORTRAIT_SIZE = (1024, 1024)
CANDIDATES_FILE = "reference_candidates.json"
CALIBRATION_FILE = Path(__file__).resolve().parent.parent / "data" / "imagegen_calibration.json"
PORTRAIT_VIEWS = ("front view", "three-quarter view", "front view", "three-quarter view")


class ApprovalRequired(RuntimeError):
    """Shots cannot be generated before the character references are approved."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def native_size() -> tuple:
    return tuple(getattr(config, "IMAGEGEN_SIZE", (1360, 752)))


def _artifacts(output: Path) -> tuple[dict, dict, dict, dict]:
    names = ("script.json", "story_brief.json", "character_bible.json", "story_outline.json")
    values = [load_json(output / name) for name in names]
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise FileNotFoundError(f"missing story artifacts in {output}: {', '.join(missing)}")
    return tuple(values)


def ensure_visual_bible(output_dir, force: bool = False) -> dict:
    """Load visual_bible.json, building it (one Gemini call) when missing or stale."""
    output = Path(output_dir)
    script, brief, bible, outline = _artifacts(output)
    return vbible.build_visual_bible(script, brief, bible, outline, output, force=force)


def ensure_shot_list(output_dir, force: bool = False) -> dict:
    """Load shot_list.json, building it (one Gemini call) when missing or stale."""
    output = Path(output_dir)
    script, _, _, outline = _artifacts(output)
    vb = ensure_visual_bible(output)
    return shots_module.build_shot_list(script, outline, vb, output, force=force)


# ── References ─────────────────────────────────────────────────────────────

def _article(noun: str) -> str:
    return "an" if noun[0] in "aeiou" else "a"


def portrait_prompt(vb: dict, character_id: str, view: str) -> str:
    entry = vbible.character(vb, character_id) or {}
    outfit = (entry.get("wardrobe") or [{}])[0].get("outfit", "")
    noun = identity.image_noun()
    return (f"{_article(noun).capitalize()} {noun} portrait, chest-up, {view}, of "
            f"{entry.get('descriptor', character_id)}{', wearing ' + outfit if outfit else ''}. "
            f"Dark backdrop, a single low light source, calm expression, looking near the viewer. "
            f"{identity.style_suffix()}")


def location_prompt(vb: dict, location_id: str) -> str:
    entry = vbible.location(vb, location_id) or {}
    noun = identity.image_noun()
    return (f"{_article(noun).capitalize()} {noun}: establishing wide view of "
            f"{entry.get('descriptor', location_id)}, empty, no people. {identity.style_suffix()}")


def archive_stale_references(output) -> Path | None:
    """Move reference images made under another style to character_refs/archive/<stamp>/."""
    output = Path(output)
    record = load_json(output / CANDIDATES_FILE) or {}
    current = identity.style_hash()
    stale = {key: [item for item in items if item.get("style_hash") != current]
             for key, items in record.items()}
    if not any(stale.values()) and not any((output / folder).glob("*.png")
                                           for folder in ("character_refs", "location_refs")):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output / "character_refs" / "archive" / stamp
    moved = 0
    for folder in ("character_refs", "location_refs"):
        for path in list((output / folder).glob("*.png")) + list((output / folder / "candidates").glob("*.jpg")):
            target = archive / folder / path.relative_to(output / folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved += 1
    record = {key: [item for item in items if item.get("style_hash") == current]
              for key, items in record.items()}
    atomic_write_json(output / CANDIDATES_FILE, record)
    return archive if moved else None


def generate_reference_candidates(vb: dict, output_dir, generator: ImageGenerator, *,
                                  characters: list[str] | None = None, locations: bool = True,
                                  progress: Callable[[str], None] = print) -> dict:
    output = Path(output_dir)
    archived = archive_stale_references(output)
    if archived:
        progress(f"   → Previous references archived in {archived} (they belong to another style)")
    record = load_json(output / CANDIDATES_FILE) or {}
    wanted = characters or [item["character_id"] for item in vb.get("characters", [])]
    jobs = []
    for cid in wanted:
        base = (vbible.character(vb, cid) or {}).get("seed", vbible.seed_for("character", cid))
        for number, view in enumerate(PORTRAIT_VIEWS, 1):
            jobs.append(("character", cid, number, ImageJob(
                job_id=f"ref_{cid}_{number}", prompt=portrait_prompt(vb, cid, view),
                seed=base + number,
                output_path=str(output / "character_refs" / "candidates" / f"{cid}_{number}.jpg"),
                size=PORTRAIT_SIZE, final_size=PORTRAIT_SIZE)))
    if locations:
        for location in vbible.recurring_locations(vb):
            lid = location["location_id"]
            for number in (1, 2):
                jobs.append(("location", lid, number, ImageJob(
                    job_id=f"loc_{lid}_{number}", prompt=location_prompt(vb, lid),
                    seed=location.get("seed", vbible.seed_for("location", lid)) + number,
                    output_path=str(output / "location_refs" / "candidates" / f"{lid}_{number}.jpg"),
                    size=native_size())))
    for index, (kind, ident, number, job) in enumerate(jobs, 1):
        progress(f"   → Reference candidate {index}/{len(jobs)}: {kind} {ident} #{number}")
        result = generator.generate_with_retry(job)
        entries = record.setdefault(vbible.reference_key(kind, ident), [])
        entries[:] = [item for item in entries if item["number"] != number]
        entries.append({"number": number, "path": str(Path(job.output_path).relative_to(output)),
                        "seed": result["seed"], "prompt": job.prompt,
                        "style_hash": identity.style_hash()})
        atomic_write_json(output / CANDIDATES_FILE, record)
    return record


def approve_reference(vb: dict, kind: str, ident: str, number: int, output_dir) -> dict:
    output = Path(output_dir)
    candidates = (load_json(output / CANDIDATES_FILE) or {}).get(vbible.reference_key(kind, ident), [])
    chosen = next((item for item in candidates if item["number"] == int(number)
                   and item.get("style_hash") == identity.style_hash()), None)
    if not chosen or not (output / chosen["path"]).is_file():
        raise FileNotFoundError(f"no candidate #{number} for {kind} {ident}; generate candidates first")
    folder = "character_refs" if kind == "character" else "location_refs"
    target = output / folder / f"{ident}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(output / chosen["path"]) as image:
        image.convert("RGB").save(target, "PNG")
    return vbible.record_approval(vb, kind, ident, f"{folder}/{ident}.png", seed=chosen["seed"],
                                  prompt=chosen["prompt"], output_dir=output)


def characters_in_shots(shot_list: dict) -> list[str]:
    seen = []
    for shot in shot_list.get("shots", []):
        for cid in shot.get("characters") or []:
            if cid not in seen:
                seen.append(cid)
    return seen


def missing_approvals(vb: dict, shot_list: dict, output_dir) -> list[str]:
    return [cid for cid in characters_in_shots(shot_list)
            if not vbible.approved_reference(vb, "character", cid, output_dir)]


# ── Shots ──────────────────────────────────────────────────────────────────

def seconds_per_image(mode: str = "ref") -> float | None:
    data = load_json(getattr(config, "IMAGEGEN_CALIBRATION_FILE", None) or CALIBRATION_FILE) or {}
    entry = (data.get("modes") or {}).get(mode) or {}
    value = entry.get("seconds_per_image")
    return float(value) if value else None


def estimate_gpu_seconds(shot_list: dict, output_dir=None) -> float | None:
    pending = [shot for shot in shot_list.get("shots", [])
               if not shot.get("reuse_of") and not _done(shot, output_dir)]
    per = seconds_per_image("ref") or seconds_per_image("t2i")
    return None if per is None else round(len(pending) * per, 1)


def _done(shot: dict, output_dir) -> bool:
    path = shot.get("image_path")
    if shot.get("hold_previous"):
        return True
    return bool(path) and (Path(path).is_file() or (output_dir and (Path(output_dir) / path).is_file()))


def _record_provenance(output: Path, records: list[dict]) -> None:
    path = output / "asset_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        manifest = {}
    existing = {item.get("job_id"): item for item in manifest.get("generated_assets", [])}
    for record in records:
        existing[record.get("job_id")] = record
    manifest["generated_assets"] = list(existing.values())
    manifest["synthetic_media"] = any(item.get("synthetic") for item in manifest["generated_assets"])
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def stock_fallback(output_dir, vb: dict, shot: dict) -> Callable[[ImageJob], dict | None]:
    """Fallback for a failed shot: stock objects/environments only (3.0-E rules)."""
    def fallback(job: ImageJob) -> dict | None:
        from video.stock import StockManager
        location = vbible.location(vb, shot.get("location")) or {}
        names = tuple(item.get("character_id", "").replace("_", " ") for item in vb.get("characters", []))
        description = f"{shot.get('action', '')} {location.get('name', '')}"
        manager = StockManager(str(output_dir), character_names=names)
        path = manager.acquire(description, "landscape", int(shot.get("section_id", 0) or 0),
                               90 + int(shot["shot_id"].rsplit("_", 1)[-1]))
        if not path:
            return None
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, job.output_path)
        asset = manager.manifest_assets[-1] if manager.manifest_assets else {}
        return {**asset, "job_id": job.job_id, "synthetic": False, "local_path": job.output_path}
    return fallback


def generate_shots(output_dir, generator: ImageGenerator, *, limit: int | None = None,
                   only: str | None = None, progress: Callable[[str], None] = print,
                   clock: Callable[[], float] = time.monotonic) -> dict:
    output = Path(output_dir)
    vb = load_json(output / vbible.FILE_NAME)
    shot_list = load_json(output / shots_module.FILE_NAME)
    if not vb or not shot_list:
        raise FileNotFoundError("visual_bible.json and shot_list.json are required; build them first")
    missing = missing_approvals(vb, shot_list, output)
    if missing:
        raise ApprovalRequired(
            "Approve a reference portrait for: " + ", ".join(missing)
            + " (python -m scripts.generate_character_refs, then approve one per character in the GUI "
              "or with --approve character:<id>:<n>). No shot is generated before approval.")
    shots = shot_list["shots"]
    queue = [shot for shot in shots if not shot.get("reuse_of")
             and (not only or shot["scene_id"] == only or shot["shot_id"] == only)
             and not _done(shot, output)]
    if limit:
        queue = queue[:limit]
    per = seconds_per_image("ref") or seconds_per_image("t2i")
    every = max(1, int(getattr(config, "IMAGEGEN_FREE_MEMORY_EVERY", 10)))
    started = clock()
    summary = {"generated": 0, "cached": 0, "fallback": 0, "failed": [], "held": [], "total": len(queue)}
    for index, shot in enumerate(queue, 1):
        composed = shots_module.compose_shot(shot, vb, output)
        job = ImageJob(job_id=shot["shot_id"], prompt=composed["prompt"], seed=composed["seed"],
                       output_path=str(output / "images" / "shots" / f"{shot['shot_id']}.jpg"),
                       kind=composed["kind"], references=composed["references"], size=native_size())
        remaining = (len(queue) - index + 1) * per if per else None
        progress(f"   → Shot {index}/{len(queue)} {shot['shot_id']} ({composed['kind']}, "
                 f"{len(composed['references'])} refs)"
                 + (f" — ETA {remaining / 60:.0f} min" if remaining else ""))
        illustration = identity.is_illustration()
        try:
            # Stock photos cannot stand in for illustrations: in that style a failed
            # shot gets two new seeds and then holds the previous shot longer.
            record = generator.generate_with_retry(
                job, fallback=None if illustration else stock_fallback(output, vb, shot),
                new_seeds=2 if illustration else 1)
        except ComfyUnavailable:
            raise
        except GenerationFailed as exc:
            if illustration and index > 0:
                shot["hold_previous"] = True
                shot["generated_at"] = _now()
                summary["held"].append(shot["shot_id"])
                _record_provenance(output, [{"job_id": shot["shot_id"], "provider": "none",
                                             "fallback": "hold_previous", "synthetic": False,
                                             "generation_errors": [str(exc)]}])
                atomic_write_json(output / shots_module.FILE_NAME, shot_list)
                progress(f"   ⚠ {shot['shot_id']}: generation failed; holding the previous shot longer")
            else:
                summary["failed"].append(shot["shot_id"])
                progress(f"   ⚠ {shot['shot_id']}: {exc}")
            continue
        shot["prompt"] = composed["prompt"]
        shot["reference_slots"] = composed["slots"]
        shot.pop("hold_previous", None)
        shot["image_path"] = str(Path(job.output_path).relative_to(output))
        shot["provider"] = record.get("provider", "comfyui")
        shot["generated_at"] = _now()
        key = "fallback" if record.get("fallback_from") else ("cached" if record.get("cache_hit") else "generated")
        summary[key] += 1
        _record_provenance(output, [record])
        atomic_write_json(output / shots_module.FILE_NAME, shot_list)
        if index % every == 0:
            generator.client.free_memory()
    summary["seconds"] = round(clock() - started, 1)
    return summary


def generate_thumbnail_image(output_dir, generator: ImageGenerator) -> str:
    output = Path(output_dir)
    vb = load_json(output / vbible.FILE_NAME) or {}
    thumb = vb.get("thumbnail_shot") or {}
    # The protagonist (first visual-bible character) anchors the thumbnail.
    protagonist = [vb["characters"][0]["character_id"]] if vb.get("characters") else []
    characters = list(thumb.get("characters") or [])
    characters = protagonist + [cid for cid in characters if cid not in protagonist]
    shot = {"shot_id": "thumbnail", "scene_id": "", "shot_type": "medium",
            "characters": characters, "location": thumb.get("location_id"),
            "action": f"{thumb.get('description', '')}, strong emotional focal point on the right, "
                      "dark negative space on the left", "face_visibility": "partial",
            "seed": thumb.get("seed", vbible.seed_for("thumbnail", "episode")), "seed_offset": 0}
    composed = shots_module.compose_shot(shot, vb, output)
    job = ImageJob(job_id="thumbnail", prompt=composed["prompt"], seed=composed["seed"],
                   output_path=str(output / "thumbnail_bg.jpg"), kind=composed["kind"],
                   references=composed["references"], size=native_size(), final_size=(1280, 720))
    record = generator.generate_with_retry(job, new_seeds=2 if identity.is_illustration() else 1)
    _record_provenance(output, [record])
    return job.output_path
