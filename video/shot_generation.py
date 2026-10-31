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
import math
import queue
import shutil
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image

import config
from agents import identity
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from studio.filenames import safe_filename
from video import visual_qa
from video.imagegen_comfyui import ComfyUnavailable, GenerationFailed, ImageGenerator, ImageJob
from video.qa import fixes, policy

PORTRAIT_SIZE = (1024, 1024)
CANDIDATES_FILE = "reference_candidates.json"
CALIBRATION_FILE = Path(__file__).resolve().parent.parent / "data" / "imagegen_calibration.json"
PORTRAIT_VIEWS = ("facing the viewer", "in a three-quarter view", "facing the viewer",
                  "in a three-quarter view")


class ApprovalRequired(RuntimeError):
    """Shots cannot be generated before the character references are approved."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def native_size() -> tuple:
    return tuple(getattr(config, "IMAGEGEN_SIZE", (1280, 720)))


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
    """A neutral character sheet: identity only, never a scene's pose, clothes or setting.

    Head and shoulders on a plain flat dark-grey backdrop, a simple plain dark top
    (not the scene wardrobe), neutral relaxed expression, even soft key light,
    and the drawing technique without environment clauses (sheet_style_suffix).
    """
    entry = vbible.character(vb, character_id) or {}
    noun = identity.image_noun()
    return (f"{_article(noun).capitalize()} {noun} character reference sheet: head and shoulders of "
            f"{entry.get('descriptor', character_id)}, {view}, wearing a simple plain dark top. "
            "Neutral relaxed expression, standing still, centred. Plain flat dark-grey backdrop, "
            "uniform and empty; even soft key light on the face. "
            f"{identity.sheet_style_suffix()}")


def location_prompt(vb: dict, location_id: str, *, anchor: bool = False) -> str:
    """Only positive wording and the figure-free suffix: the negative is zeroed, so "no people"
    (or "small figures" in the style) draws people, who would then be copied into every shot."""
    entry = vbible.location(vb, location_id) or {}
    noun = identity.image_noun()
    style = "Rendered in the drawing style of the first image. " if anchor else ""
    return (f"{_article(noun).capitalize()} {noun}: establishing wide view of a deserted, unoccupied "
            f"{entry.get('descriptor', location_id)}; only architecture, furniture and objects; perfectly "
            f"still. {style}{identity.style_suffix(figures=False)}")


def candidate_is_current(key: str, item: dict) -> bool:
    """A candidate made under the current style and, for characters, the current sheet version."""
    if item.get("style_hash") != identity.style_hash():
        return False
    return not key.startswith("character:") or item.get("sheet_version", 1) == vbible.CHARACTER_SHEET_VERSION


def archive_stale_references(output, vb: dict | None = None) -> Path | None:
    """Move only stale references to character_refs/archive/<stamp>/.

    Stale = made under another style, or a character sheet of an older version
    (candidates and approved images). Current candidates and approvals — e.g.
    location references after a character-sheet change — stay where they are.
    """
    output = Path(output)
    record = load_json(output / CANDIDATES_FILE) or {}
    bodiless = {vbible.reference_key("character", item["character_id"])
                for item in (vb or {}).get("characters", []) if item.get("presence") == "none"}
    stale = [output / item["path"] for key, items in record.items() for item in items
             if (key in bodiless or not candidate_is_current(key, item)) and item.get("path")]
    if vb is not None:
        for kind, folder in (("character", "character_refs"), ("location", "location_refs")):
            for path in (output / folder).glob("*.png"):        # approved images without a current approval
                if not vbible.approved_reference(vb, kind, path.stem, output):
                    stale.append(path)
    stale = [path for path in dict.fromkeys(stale) if path.is_file()]
    current = {key: [item for item in items if candidate_is_current(key, item)] for key, items in record.items()
               if key not in bodiless}
    if current != record:
        atomic_write_json(output / CANDIDATES_FILE, current)
    if not stale:
        return None
    archive = output / "character_refs" / "archive" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path in stale:
        target = archive / path.relative_to(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    return archive


# A location gets a reference (and is part of "References") when it recurs: in 2+ scenes, or in
# 3+ shots of the shot list (a single long scene, e.g. the cemetery). Any other location can get one
# on request ("Gerar referência" in the GUI).
RECURRING_MIN_SCENES = 2
RECURRING_MIN_SHOTS = 3


def recurring_locations(vb: dict, shot_list: dict | None = None, output_dir=None) -> list[dict]:
    """Locations in 2+ scenes or 3+ planned shots (reused images not counted)."""
    if shot_list is None and output_dir is not None:
        shot_list = load_json(Path(output_dir) / shots_module.FILE_NAME) or {}
    shots = {}
    for shot in (shot_list or {}).get("shots", []):
        if not shot.get("reuse_of"):
            shots[shot.get("location")] = shots.get(shot.get("location"), 0) + 1
    return [item for item in vb.get("locations", [])
            if len(item.get("scene_ids", [])) >= RECURRING_MIN_SCENES
            or shots.get(item["location_id"], 0) >= RECURRING_MIN_SHOTS]


LOCATION_PEOPLE_RETRIES = 3        # new seeds for a location candidate with someone in it
BATCH_SIZES = {"character": len(PORTRAIT_VIEWS), "location": 2}


def _location_job(vb: dict, lid: str, number: int, output: Path) -> ImageJob:
    """Establishing image of a location; style-anchored only by an anchor with nobody in it."""
    from agents import style_refs
    location = vbible.location(vb, lid) or {}
    anchors = style_refs.empty_paths()
    return ImageJob(
        job_id=f"loc_{lid}_{number}", prompt=location_prompt(vb, lid, anchor=bool(anchors)),
        seed=location.get("seed", vbible.seed_for("location", lid)) + number,
        output_path=str(output / "location_refs" / "candidates" / f"{safe_filename(lid)}_{number}.jpg"),
        size=native_size(), **({"kind": "ref", "references": anchors[:1]} if anchors else {}))


def _character_job(vb: dict, cid: str, number: int, output: Path) -> ImageJob:
    base = (vbible.character(vb, cid) or {}).get("seed", vbible.seed_for("character", cid))
    return ImageJob(
        job_id=f"ref_{cid}_{number}", prompt=portrait_prompt(vb, cid, PORTRAIT_VIEWS[(number - 1) % len(PORTRAIT_VIEWS)]),
        seed=base + number,
        output_path=str(output / "character_refs" / "candidates" / f"{safe_filename(cid)}_{number}.jpg"),
        size=PORTRAIT_SIZE, final_size=PORTRAIT_SIZE)


def people_in(path) -> int | None:
    """People the layer-1 detector finds in an image (None when the detector is unavailable)."""
    detector = fixes._detector()
    if detector is None:
        return None
    from video.qa.deterministic import DetectorUnavailable
    try:
        return len(detector.detect(path).get("persons", []))
    except DetectorUnavailable:          # unreadable image: not checked
        return None


def generate_location_candidates(vb: dict, output_dir, generator: ImageGenerator, location_ids: list[str], *,
                                 progress: Callable[[str], None] = print) -> dict:
    """Two candidates for each given location, recurring or not (optional "Gerar referência")."""
    output = Path(output_dir)
    known = {item["location_id"] for item in vb.get("locations", [])}
    unknown = [lid for lid in location_ids if lid not in known]
    if unknown:
        raise ValueError(f"unknown locations: {unknown}")
    jobs = [("location", lid, number, _location_job(vb, lid, number, output))
            for lid in location_ids for number in (1, 2)]
    return _run_reference_jobs(jobs, output, generator, progress)


def generate_reference_candidates(vb: dict, output_dir, generator: ImageGenerator, *,
                                  characters: list[str] | None = None, locations: bool = True,
                                  progress: Callable[[str], None] = print) -> dict:
    output = Path(output_dir)
    archived = archive_stale_references(output, vb)
    if archived:
        progress(f"   → Stale references archived in {archived} (another style or an older character sheet)")
    drawn = [item["character_id"] for item in vbible.on_screen(vb)]
    wanted = [cid for cid in characters or drawn if cid in drawn]       # never a portrait of a voice / no body
    jobs = [("character", cid, number, _character_job(vb, cid, number, output))
            for cid in wanted for number in range(1, len(PORTRAIT_VIEWS) + 1)]
    if locations:
        for location in recurring_locations(vb, output_dir=output):
            lid = location["location_id"]
            if vbible.approved_reference(vb, "location", lid, output):
                continue                     # still valid: no new candidates needed
            jobs += [("location", lid, number, _location_job(vb, lid, number, output)) for number in (1, 2)]
    return _run_reference_jobs(jobs, output, generator, progress)


def generate_new_batch(vb: dict, output_dir, generator: ImageGenerator, kind: str, ident: str, *,
                       progress: Callable[[str], None] = print) -> dict:
    """"Gerar novos candidatos": a new batch with new seeds (new numbers); earlier ones stay in the history."""
    output = Path(output_dir)
    if kind == "character" and ident not in [item["character_id"] for item in vbible.on_screen(vb)]:
        raise ValueError(f"no reference for {ident!r} (unknown, voice only or no body)")
    if kind == "location" and not vbible.location(vb, ident):
        raise ValueError(f"unknown location {ident!r}")
    if kind not in BATCH_SIZES:
        raise ValueError("kind must be character or location")
    existing = (load_json(output / CANDIDATES_FILE) or {}).get(vbible.reference_key(kind, ident), [])
    start = max([int(item["number"]) for item in existing] or [0]) + 1
    batch = max([int(item.get("batch", 1)) for item in existing] or [0]) + 1
    make = _character_job if kind == "character" else _location_job
    jobs = [(kind, ident, number, make(vb, ident, number, output))
            for number in range(start, start + BATCH_SIZES[kind])]
    return _run_reference_jobs(jobs, output, generator, progress, batch=batch)


def _run_reference_jobs(jobs: list, output: Path, generator: ImageGenerator,
                        progress: Callable[[str], None], *, batch: int = 1) -> dict:
    """Generate and record candidates. A location candidate with someone in it is generated again
    with another seed (LOCATION_PEOPLE_RETRIES times) before it is shown; if someone is still
    there, the candidate is kept and marked (``people``) for the GUI."""
    record = load_json(output / CANDIDATES_FILE) or {}
    for index, (kind, ident, number, job) in enumerate(jobs, 1):
        progress(f"   → Reference candidate {index}/{len(jobs)}: {kind} {ident} #{number}")
        result = generator.generate_with_retry(job)
        people, attempts = None, 1
        if kind == "location":
            people = people_in(job.output_path)
            while people and attempts <= LOCATION_PEOPLE_RETRIES:
                progress(f"     {people} person(s) in {ident} #{number}: new seed ({attempts}/{LOCATION_PEOPLE_RETRIES})")
                job = replace(job, seed=job.seed + 7919 * attempts)
                result = generator.generate_with_retry(job)
                people = people_in(job.output_path)
                attempts += 1
        entries = record.setdefault(vbible.reference_key(kind, ident), [])
        entries[:] = [item for item in entries if item["number"] != number]
        entries.append({"number": number, "path": str(Path(job.output_path).relative_to(output)),
                        "seed": result["seed"], "prompt": job.prompt, "batch": batch,
                        "style_hash": identity.style_hash(),
                        **({"people": people, "attempts": attempts} if kind == "location" else {}),
                        **({"sheet_version": vbible.CHARACTER_SHEET_VERSION} if kind == "character" else {})})
        atomic_write_json(output / CANDIDATES_FILE, record)
    return record


def approve_reference(vb: dict, kind: str, ident: str, number: int, output_dir) -> dict:
    output = Path(output_dir)
    candidates = (load_json(output / CANDIDATES_FILE) or {}).get(vbible.reference_key(kind, ident), [])
    chosen = next((item for item in candidates if item["number"] == int(number)
                   and candidate_is_current(vbible.reference_key(kind, ident), item)), None)
    if not chosen or not (output / chosen["path"]).is_file():
        raise FileNotFoundError(f"no candidate #{number} for {kind} {ident}; generate candidates first")
    folder = "character_refs" if kind == "character" else "location_refs"
    target = output / folder / f"{safe_filename(ident)}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(output / chosen["path"]) as image:
        image.convert("RGB").save(target, "PNG")
    return vbible.record_approval(vb, kind, ident, f"{folder}/{target.name}", seed=chosen["seed"],
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


def update_provenance(output, job_id: str, **fields) -> None:
    """Add fields to one generated-asset record (e.g. a later visual QA result)."""
    path = Path(output) / "asset_manifest.json"
    manifest = load_json(path) or {}
    for item in manifest.get("generated_assets", []):
        if item.get("job_id") == job_id:
            item.update(fields)
            path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            return


def stock_allowed(shot: dict) -> bool:
    """Stock is opt-in (``STOCK_ENABLED``); then it serves any shot in
    ``cinematic_photo`` mode and, in the illustration style, only environment
    and object shots without characters, as a last resort after the new seeds."""
    from video import stock
    return stock.enabled() and not (identity.is_illustration() and shot.get("characters"))


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


def shot_job(shot: dict, vb: dict, output: Path, seed_offset: int = 0) -> tuple[ImageJob, dict]:
    composed = shots_module.compose_shot(shot, vb, output)
    job = ImageJob(job_id=shot["shot_id"], prompt=composed["prompt"], seed=composed["seed"] + seed_offset,
                   output_path=str(output / "images" / "shots" / f"{safe_filename(shot['shot_id'])}.jpg"),
                   kind=composed["kind"], references=composed["references"], size=native_size())
    return job, composed


def _in_scope(shot: dict, only) -> bool:
    """``only``: a scene id, a shot id, or a list of them (batch regeneration)."""
    if isinstance(only, (list, tuple, set)):
        return shot["scene_id"] in only or shot["shot_id"] in only
    return not only or shot["scene_id"] == only or shot["shot_id"] == only


def generate_shots(output_dir, generator: ImageGenerator, *, limit: int | None = None,
                   only: str | list | None = None, progress: Callable[[str], None] = print,
                   clock: Callable[[], float] = time.monotonic, qa=None, fix_only: bool = False,
                   qa_mode: str | None = None, prepared: dict | None = None) -> dict:
    """Phase-batched so Klein and the QA VLM never share the 8 GB of VRAM (Phase 3.5-E):

    1. generate every pending shot (Klein loaded), layer-1 QA inline on the CPU;
    2. free ComfyUI's models, the local VLM answers the batch's questions, unload it;
    3. Klein again: region edits / new seeds for the shots that failed;
    4. repeat 2-3 for the fixed shots only (``IMAGEGEN_QA_ROUNDS``).

    ``fix_only`` skips step 1 and works on shots whose stored QA failed (or was never run).
    ``qa_mode`` (``IMAGEGEN_QA_MODE``): in the default ``advisory`` mode steps 3-4 only run for
    the reliable deterministic checks; everything else is stored as hints for the GUI review
    (``summary["qa_hints"]``). Everything is checkpointed in shot_list.json, so an interrupted
    run resumes."""
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
    narration = shots_module.scene_narration(shots)
    pending = [shot for shot in shots if not shot.get("reuse_of") and _in_scope(shot, only)
               and not _done(shot, output)] if not fix_only else []
    # Flagged shots broke a blocking rule the planner could not repair: skipped until edited.
    flagged = [shot["shot_id"] for shot in pending if shot.get("flags")]
    queue = [shot for shot in pending if not shot.get("flags")]
    if limit:
        queue = queue[:limit]
    warned = [shot["shot_id"] for shot in queue if shot.get("warnings")]
    if warned:
        progress(f"   ⚠ {len(warned)} shot(s) carry advisory notes (generated anyway): {', '.join(warned)}")
    if flagged:
        progress(f"   ⛔ {len(flagged)} flagged shot(s) skipped until edited: {', '.join(flagged)}")
    per = seconds_per_image("ref") or seconds_per_image("t2i")
    every = max(1, int(getattr(config, "IMAGEGEN_FREE_MEMORY_EVERY", 10)))
    started = clock()
    summary = {"generated": 0, "cached": 0, "fallback": 0, "failed": [], "held": [], "total": len(queue),
               "flagged": flagged, "warned": warned, "qa_failed": [], "qa_flagged": [], "qa_edits": 0,
               "qa_regenerations": 0}
    use_qa = visual_qa.enabled() and policy.mode(qa_mode) != "off"
    if use_qa and qa is None:
        from video.qa.layered import LayeredQA
        qa = LayeredQA(comfy=generator.client, progress=progress, mode=qa_mode)
    if use_qa:
        progress(f"   {policy.summary_line(qa.mode)}")
    items, pendings = {}, dict(prepared or {})          # prepared: layer 1 already run by the render lane

    def expected(shot):
        return visual_qa.expected_for(shot, vb, narration.get(shot.get("scene_id", ""), ""))

    def track(shot):
        items[shot["shot_id"]] = {"item": shot, "image": output / shot["image_path"], "expected": expected(shot),
                                  "factory": lambda offset, shot=shot: shot_job(shot, vb, output, offset)[0]}
        return items[shot["shot_id"]]

    if queue:
        progress(f"   Phase 1/3 — generating {len(queue)} shot(s)"
                 + (f", ~{len(queue) * per / 60:.0f} min" if per else "") + (" + layer-1 QA (CPU)" if use_qa else ""))
    save = lambda: atomic_write_json(output / shots_module.FILE_NAME, shot_list)
    for index, shot in enumerate(queue, 1):
        remaining = (len(queue) - index + 1) * per if per else None
        record = _render_shot(generator, output, vb, shot, label=f"{index}/{len(queue)}", narration=narration,
                              summary=summary, save=save, progress=progress,
                              eta=f" — ETA {remaining / 60:.0f} min" if remaining else "")
        if record is None:
            continue
        if use_qa and not record.get("fallback_from"):
            entry = track(shot)
            pendings[shot["shot_id"]] = qa.prepare(entry["image"], entry["expected"])     # layer 1, CPU
        if index % every == 0:
            generator.client.free_memory()
    if use_qa:
        for shot in shots:                                # resume: unchecked or failed shots in scope
            if shot.get("reuse_of") or not _in_scope(shot, only) or shot["shot_id"] in items:
                continue
            if not (shot.get("image_path") and (output / shot["image_path"]).is_file()
                    and shot.get("provider") != "stock"):
                continue
            fixes.reapply_policy(shot, qa)               # results stored under another mode
            if fixes.needs_qa(shot) or fixes.needs_fix(shot):
                track(shot)
        outcome = qa_rounds(generator, output, vb, list(items.values()), qa, save, progress=progress,
                            pendings=pendings, per=per)
        summary.update(outcome)
        summary["qa_mode"] = qa.mode
        summary["qa_hints"] = [shot["shot_id"] for shot in shots
                               if not shot.get("reuse_of") and _in_scope(shot, only) and fixes.hinted(shot)]
        save()
    summary["seconds"] = round(clock() - started, 1)
    return summary


def _render_shot(generator: ImageGenerator, output: Path, vb: dict, shot: dict, *, label: str, narration: dict,
                 summary: dict, save: Callable[[], None], progress: Callable[[str], None], eta: str = "",
                 lock=None) -> dict | None:
    """Generate one shot's image and record it on ``shot`` (under ``lock`` when another thread reads the
    shot list). Returns the generation record, or None when it failed (held or listed in summary)."""
    guard = lock or nullcontext()
    job, composed = shot_job(shot, vb, output)
    for note in shots_module.prompt_advice(composed["prompt"], narration.get(shot.get("scene_id", ""), "")):
        progress(f"   ⚠ {shot['shot_id']}: {note} (advisory; generating anyway)")
    progress(f"   → Shot {label} {shot['shot_id']} ({composed['kind']}, {len(composed['references'])} refs){eta}")
    illustration = identity.is_illustration()
    try:
        # In the illustration style a failed shot gets two new seeds, then (only with
        # STOCK_ENABLED, environment/object shots only) stock, then holds the previous shot.
        record = generator.generate_with_retry(
            job, fallback=stock_fallback(output, vb, shot) if stock_allowed(shot) else None,
            new_seeds=2 if illustration else 1)
    except ComfyUnavailable:
        raise
    except GenerationFailed as exc:
        if illustration:
            with guard:
                shot["hold_previous"] = True
                shot["generated_at"] = _now()
                summary["held"].append(shot["shot_id"])
                _record_provenance(output, [{"job_id": shot["shot_id"], "provider": "none",
                                             "fallback": "hold_previous", "synthetic": False,
                                             "generation_errors": [str(exc)]}])
                save()
            progress(f"   ⚠ {shot['shot_id']}: generation failed; holding the previous shot longer")
        else:
            summary["failed"].append(shot["shot_id"])
            progress(f"   ⚠ {shot['shot_id']}: {exc}")
        return None
    with guard:
        shot["prompt"] = composed["prompt"]
        shot["reference_slots"] = composed["slots"]
        shot.pop("hold_previous", None)
        shot["image_path"] = str(Path(job.output_path).relative_to(output))
        shot["provider"] = record.get("provider", "comfyui")
        shot["generated_at"] = _now()
        shot.pop("qa", None)                              # a new image starts a new QA budget
        key = "fallback" if record.get("fallback_from") else ("cached" if record.get("cache_hit") else "generated")
        summary[key] += 1
        _record_provenance(output, [record])
        save()
    return record


def _summary(total: int = 0) -> dict:
    return {"generated": 0, "cached": 0, "fallback": 0, "failed": [], "held": [], "total": total,
            "flagged": [], "warned": [], "qa_failed": [], "qa_flagged": [], "qa_edits": 0,
            "qa_regenerations": 0}


def characters_to_approve(vb: dict, outline: dict, output_dir) -> list[str]:
    """On-screen characters the outline puts in a scene without an approved portrait (checked before
    planning, since the shots that will list them don't exist yet)."""
    cast = {cid for scene in outline.get("scenes", []) for cid in scene.get("characters_present") or []}
    return [item["character_id"] for item in vbible.on_screen(vb)
            if item["character_id"] in cast and not vbible.approved_reference(vb, "character", item["character_id"],
                                                                               output_dir)]


def plan_and_render(output_dir, generator: ImageGenerator, *, progress: Callable[[str], None] = print,
                    stop: threading.Event | None = None, concurrency: int | None = None, qa=None,
                    qa_mode: str | None = None, force: bool = False, on_status: Callable[[dict], None] | None = None,
                    clock: Callable[[], float] = time.monotonic) -> dict:
    """Phase 3.8: plan the shot list and render it at the same time.

    * Planner lane (a thread, network): scene by scene, Gemini plans the shots (checkpointed); each
      scene's shots go into the render queue as soon as it is ready.
    * Render lane (this thread, GPU — the only ComfyUI caller): renders the queue lowest scene/shot
      first, layer-1 QA inline on the CPU; it never waits for later scenes and keeps working while the
      planner sits in a Gemini backoff wait.
    * Then, as in 3.5, the local VLM QA runs once over the batch, followed by edits/new seeds
      (``generate_shots`` with the layer-1 results), and anything still pending is rendered.

    A scene the planner cannot plan is flagged and reported (``failed_scenes``); the rest goes on.
    ``stop`` drains cleanly: the current image is finished, checkpoints and the image cache make a
    rerun resume both lanes. ``on_status`` receives {"planning", "images", "eta_seconds"} updates.
    """
    from agents import gemini_client
    output = Path(output_dir)
    script, _, _, outline = _artifacts(output)
    vb = ensure_visual_bible(output)
    missing = characters_to_approve(vb, outline, output)
    if missing:
        raise ApprovalRequired("Approve a reference portrait for: " + ", ".join(missing)
                               + " before the shots (Review & Edit → References).")
    current = load_json(output / shots_module.FILE_NAME) or {}
    if not force and current.get("source_hash") and \
            current["source_hash"] == shots_module.expected_source_hash(script, vb, output):
        return generate_shots(output, generator, progress=progress, qa=qa, qa_mode=qa_mode, clock=clock)

    lock = threading.RLock()
    said = threading.Lock()
    stop = stop or threading.Event()

    def say(message: str) -> None:
        with said:
            progress(message)

    segments_total = len({segment["scene_id"] for segment in shots_module._segments_and_hash(script, vb, output)[0]})
    per = seconds_per_image("ref") or seconds_per_image("t2i")
    started = clock()
    status = {"planning": {"done": 0, "total": segments_total, "waiting_seconds": 0},
              "images": {"done": 0, "total": 0}, "eta_seconds": None}

    def publish() -> None:
        with said:
            planning, images = status["planning"], status["images"]
            elapsed = clock() - started
            per_scene = elapsed / planning["done"] if planning["done"] else None
            left_images = (images["total"] - images["done"]) * per if per else None
            left_scenes = (planning["total"] - planning["done"]) * per_scene if per_scene else None
            status["eta_seconds"] = round(max(left_images or 0, left_scenes or 0)) if per else None
            snapshot = json.loads(json.dumps(status))
        if on_status:
            on_status(snapshot)

    work: queue.PriorityQueue = queue.PriorityQueue()
    outcome: dict = {}
    narration: dict = {}                                 # scene id -> its narration (prompt advice, QA)

    def on_scene(index: int, scene_id: str, entries: list[dict]) -> None:
        narration[scene_id] = " ".join(str(shot.get("narration_excerpt", "")) for shot in entries)
        added = 0
        for number, shot in enumerate(entries):
            if not shot.get("reuse_of") and not shot.get("flags") and not _done(shot, output):
                work.put((index, number, shot["shot_id"], shot))
                added += 1
        with said:
            status["planning"]["done"] += 1
            status["planning"]["waiting_seconds"] = 0
            status["images"]["total"] += added
        say(f"   Planejamento: {status['planning']['done']}/{status['planning']['total']} cenas "
            f"({scene_id}: {added} imagem(ns) na fila)")
        publish()

    def waiting(seconds: float, reason: str, stage: str) -> None:
        if threading.current_thread() is planner:
            with said:
                status["planning"]["waiting_seconds"] = round(seconds)
            say(f"   Planejamento aguardando o Gemini ({reason}, {seconds:.0f} s); as imagens continuam")
            publish()

    def plan() -> None:
        try:
            outcome["shot_list"] = shots_module.build_shot_list(script, outline, vb, output, force=force,
                                                               on_scene=on_scene, concurrency=concurrency,
                                                               stop=stop, lock=lock)
        except BaseException as exc:                     # noqa: BLE001 — reported by the render lane
            outcome["error"] = exc
        finally:
            work.put((math.inf, 0, "", None))            # after every queued shot

    planner = threading.Thread(target=plan, name="shot-planner", daemon=True)
    gemini_client.wait_listeners.append(waiting)
    summary = _summary()
    use_qa = visual_qa.enabled() and policy.mode(qa_mode) != "off"
    if use_qa and qa is None:
        from video.qa.layered import LayeredQA
        qa = LayeredQA(comfy=generator.client, progress=say, mode=qa_mode)
    prepared = {}
    every = max(1, int(getattr(config, "IMAGEGEN_FREE_MEMORY_EVERY", 10)))

    def save() -> None:
        if "shot_list" in outcome:                       # the file exists once planning is complete
            atomic_write_json(output / shots_module.FILE_NAME, outcome["shot_list"])

    planner.start()
    rendered = 0
    try:
        while True:
            _, _, _, shot = work.get()
            if shot is None:
                break
            if stop.is_set():
                continue                                 # drain without rendering
            left = (status["images"]["total"] - status["images"]["done"]) * per if per else None
            record = _render_shot(generator, output, vb, shot,
                                  label=f"{status['images']['done'] + 1}/{status['images']['total']}",
                                  narration=narration, summary=summary, save=lambda: None, progress=say,
                                  eta=f" — ETA {left / 60:.0f} min" if left else "", lock=lock)
            with said:
                status["images"]["done"] += 1
            say(f"   Imagens: {status['images']['done']}/{status['images']['total']}")
            publish()
            rendered += 1
            if record is not None and use_qa and not record.get("fallback_from"):
                expected = visual_qa.expected_for(shot, vb, narration.get(shot["scene_id"], ""))
                prepared[shot["shot_id"]] = qa.prepare(output / shot["image_path"], expected)   # layer 1, CPU
            if rendered % every == 0:
                generator.client.free_memory()
    except BaseException:                                # Ctrl+C / ComfyUI down: let the planner stop too
        stop.set()
        raise
    finally:
        planner.join()
        gemini_client.wait_listeners.remove(waiting)
    error = outcome.get("error")
    if isinstance(error, shots_module.PlanningStopped) or stop.is_set():
        say("   Interrompido: a imagem atual foi concluída; checkpoints e cache retomam na próxima execução.")
        return {**summary, "stopped": True, "seconds": round(clock() - started, 1)}
    if error is not None:
        raise error
    with lock:
        save()
    failed_scenes = outcome["shot_list"].get("failed_scenes") or {}
    rest = generate_shots(output, generator, progress=say, qa=qa, qa_mode=qa_mode, clock=clock, prepared=prepared)
    for key in ("generated", "cached", "fallback"):
        rest[key] = rest.get(key, 0) + summary[key]
    for key in ("failed", "held"):
        rest[key] = summary[key] + rest.get(key, [])
    rest["total"] = rest.get("total", 0) + status["images"]["total"]
    rest["failed_scenes"] = failed_scenes
    rest["planned_scenes"] = status["planning"]["done"]
    rest["seconds"] = round(clock() - started, 1)
    if failed_scenes:
        say("   ⛔ Cenas que o planejamento não conseguiu fazer (planos marcados): " + ", ".join(sorted(failed_scenes)))
    return rest


def qa_rounds(generator: ImageGenerator, output: Path, vb: dict, items: list[dict], qa, save: Callable[[], None],
              *, progress: Callable[[str], None] = print, pendings: dict | None = None,
              per: float | None = None, rounds: int | None = None) -> dict:
    """QA → fix rounds over ``items`` ({"item", "image", "expected", "factory"}); the VLM and Klein
    alternate, never loaded together. Returns the QA part of the summary."""
    rounds = int(rounds if rounds is not None else getattr(config, "IMAGEGEN_QA_ROUNDS", 2))
    pendings = dict(pendings or {})
    outcome = {"qa_failed": [], "qa_flagged": [], "qa_edits": 0, "qa_regenerations": 0, "qa_checked": 0}
    check = [entry for entry in items if fixes.needs_qa(entry["item"])]
    fix = [entry for entry in items if not fixes.needs_qa(entry["item"]) and fixes.needs_fix(entry["item"])]
    for round_number in range(rounds + 1):
        if check:
            for entry in check:                           # layer 1 for anything not prepared inline
                shot_id = entry["item"]["shot_id"]
                if shot_id not in pendings:
                    budget = (entry["item"].get("qa") or {}).get("edits", 0)
                    pendings[shot_id] = qa.prepare(entry["image"], entry["expected"], budget)
                else:
                    pendings[shot_id]["edits_done"] = (entry["item"].get("qa") or {}).get("edits", 0)
            batch = [pendings[entry["item"]["shot_id"]] for entry in check]
            questions = sum(len(pending["questions"]) for pending in batch)
            progress(f"   Phase 2/3 — QA round {round_number + 1}: {len(check)} shot(s), {questions} VLM "
                     f"question(s) (~{questions * 1.5 / 60:.1f} min; Klein unloaded first)")
            qa.answer(batch)
            for entry, pending in zip(check, batch):
                result = qa.finish(pending)
                fixes.record_result(entry["item"], result)
                update_provenance(output, entry["item"]["shot_id"], visual_qa=result,
                                  qa_actions=entry["item"]["qa"].get("actions", []))
                if result["status"] == "fail":
                    progress(f"   ⛔ {entry['item']['shot_id']} → {result['verdict']}: {result['verdict_reason']}")
                outcome["qa_checked"] += 1
            pendings = {}
            save()
            fix = [entry for entry in check if fixes.needs_fix(entry["item"])] + \
                  [entry for entry in fix if entry not in check]
        if not fix or round_number >= rounds:
            break
        progress(f"   Phase 3/3 — fixing {len(fix)} shot(s) with Klein"
                 + (f" (~{len(fix) * per / 60:.0f} min)" if per else ""))
        check = []
        for entry in fix:
            action = fixes.apply(generator, output, entry["item"], vb, image_path=entry["image"],
                                 job_factory=entry["factory"], progress=progress)
            if action["action"] in ("edit", "regenerate"):
                outcome["qa_edits" if action["action"] == "edit" else "qa_regenerations"] += 1
                check.append(entry)
            update_provenance(output, entry["item"]["shot_id"], qa_actions=entry["item"]["qa"].get("actions", []))
            save()
        fix = []
    for entry in items:
        qa_state = entry["item"].get("qa") or {}
        if qa_state.get("flagged"):
            outcome["qa_flagged"].append(entry["item"]["shot_id"])
        elif qa_state.get("status") == "fail":
            outcome["qa_failed"].append(entry["item"]["shot_id"])
    return outcome


QA_SEED_OFFSET = fixes.QA_SEED_OFFSET


# The figure must dominate the thumbnail (the evidence one left the whole left half empty).
THUMBNAIL_FIGURE = ("the protagonist's head and shoulders fill at least half of the frame height on the right "
                    "third, face and identity markers clearly visible, lit by one warm light; dark negative "
                    "space on the left third for the title text")


def thumbnail_shot(vb: dict, brief: dict, shot_list: dict | None = None) -> dict:
    """The thumbnail as a shot: the protagonist (first visual-bible character) + the brief's
    thumbnail concept + a composition where the figure occupies at least half the height."""
    thumb = vb.get("thumbnail_shot") or {}
    protagonist = [vbible.on_screen(vb)[0]["character_id"]] if vbible.on_screen(vb) else []
    characters = protagonist + [cid for cid in thumb.get("characters") or [] if cid not in protagonist]
    concept = str(brief.get("thumbnail_concept") or thumb.get("description") or "").strip().rstrip(".")
    settings = {item.get("location"): item.get("setting_condition") for item in (shot_list or {}).get("shots", [])}
    return {"shot_id": "thumbnail", "scene_id": "", "shot_type": "medium", "characters": characters[:3],
            "location": thumb.get("location_id"), "action": f"{concept}. {THUMBNAIL_FIGURE}",
            "face_visibility": "full",
            "others_present": bool(shots_module.unnamed_people(concept, len(characters[:3]))),
            "setting_condition": settings.get(thumb.get("location_id")) or "",
            "seed": thumb.get("seed", vbible.seed_for("thumbnail", "episode")), "seed_offset": 0}


THUMBNAIL_QA_FILE = "thumbnail_qa.json"


def generate_thumbnail_image(output_dir, generator: ImageGenerator,
                             progress: Callable[[str], None] = print, qa=None) -> str:
    output = Path(output_dir)
    vb = load_json(output / vbible.FILE_NAME) or {}
    brief = load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    shot = thumbnail_shot(vb, brief, load_json(output / shots_module.FILE_NAME))
    if shot["characters"] and not vbible.approved_reference(vb, "character", shot["characters"][0], output):
        raise ApprovalRequired(f"Approve {shot['characters'][0]}'s character sheet before the thumbnail: "
                               "it must show the protagonist's reference, not a generic face.")
    composed = shots_module.compose_shot(shot, vb, output)
    job = ImageJob(job_id="thumbnail", prompt=composed["prompt"], seed=composed["seed"],
                   output_path=str(output / "thumbnail_bg.jpg"), kind=composed["kind"],
                   references=composed["references"], size=native_size(), final_size=(1280, 720))
    record = generator.generate_with_retry(job, new_seeds=2 if identity.is_illustration() else 1)
    _record_provenance(output, [record])
    if visual_qa.enabled() and policy.mode() != "off":   # same QA → fix path as the shots
        from video.qa.layered import LayeredQA
        holder = {"shot_id": "thumbnail", "qa": {}}
        factory = lambda offset: replace(job, seed=job.seed + offset)
        entry = {"item": holder, "image": Path(job.output_path), "factory": factory,
                 "expected": visual_qa.expected_for(shot, vb, str(brief.get("thumbnail_concept") or ""))}
        save = lambda: atomic_write_json(output / THUMBNAIL_QA_FILE, holder)
        qa_rounds(generator, output, vb, [entry], qa or LayeredQA(comfy=generator.client, progress=progress),
                  save, progress=progress)
    return job.output_path
