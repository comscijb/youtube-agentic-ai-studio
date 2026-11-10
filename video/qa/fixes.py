"""Apply a QA verdict to one shot (or the thumbnail): region edit or regeneration (Phase 3.5-D).

Klein must be loaded: the batched pipeline (``shot_generation``) calls this
between QA rounds, never while the VLM is resident.

* ``edit`` — a FLUX 3 Klein region edit of the current image (the shot is
  ``<ref_image_0>``, QA boxes become ``<obj_N>``, approved portraits follow
  for identity fixes), instruction composed by ``edits``;
* ``regenerate`` — a new seed (``QA_SEED_OFFSET``), at most
  ``IMAGEGEN_QA_RETRIES`` times;
* when both budgets are spent the best version seen (fewest blocking issues)
  is restored and the shot is flagged for the user.

Every version is kept as ``images/shots/history/<shot_id>_vN.jpg`` with the QA
verdict it had; every action is recorded on the shot (``qa.actions``) and in
asset_manifest.json (``qa_actions``).
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import config
from studio.filenames import safe_filename
from video.imagegen_comfyui import GenerationFailed, ImageJob
from PIL import Image

from video.qa import base, decision, edits, region

EDIT_SEED_OFFSET = 4_000_037
QA_SEED_OFFSET = 3_000_017
HISTORY = Path("images") / "shots" / "history"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state(item: dict) -> dict:
    qa = item.setdefault("qa", {})
    for key, value in (("edits", 0), ("regenerations", 0), ("history", []), ("actions", []), ("issues", [])):
        qa.setdefault(key, value)
    return qa


def record_result(item: dict, result: dict) -> dict:
    """Store a QA result on the shot, keeping its edit/regeneration budget, history and actions."""
    qa = state(item)
    qa.pop("hints_dismissed", None)
    qa.update(version=result.get("version"), status=result["status"], verdict=result["verdict"], mode=result.get("mode"),
              reason=result.get("verdict_reason", ""), issues=result.get("issues", []),
              people_count=result.get("people_count"), layers=result.get("layers", []),
              warnings=result.get("warnings", []), notes=base.notes(result), checked_at=_now())
    if result.get("identity"):
        qa["identity"] = result["identity"]
    return qa


def reapply_policy(item: dict, qa) -> bool:
    """Re-evaluate a stored QA result under ``qa``'s mode (``LayeredQA.policy``) without
    re-running the checks: results saved under another mode, or before the mode existed,
    turn their VLM signals into hints. Returns True when the verdict changed."""
    stored = item.get("qa") or {}
    if not stored.get("issues") or stored.get("version") != base.QA_VERSION or stored.get("verdict") == "recheck" \
            or stored.get("status") not in ("pass", "fail"):
        return False
    before = stored.get("verdict")
    result = decision.finalize({"status": stored["status"], "issues": qa.policy(stored["issues"]),
                                "warnings": stored.get("warnings", [])}, int(stored.get("edits", 0)))
    stored.update(status=result["status"], verdict=result["verdict"], reason=result["verdict_reason"],
                  issues=result["issues"], notes=base.notes(result), mode=qa.mode)
    return stored["verdict"] != before


def hinted(item: dict) -> bool:
    """A checked shot with a hint for the human review: an issue that would have blocked but the
    QA mode keeps advisory. "Unclear" answers stay on the card but do not ask for a review."""
    qa = item.get("qa") or {}
    return qa.get("status") in ("pass", "fail") and not qa.get("hints_dismissed") and not qa.get("reviewed")         and any(entry.get("hint") and not entry.get("ignored") for entry in qa.get("issues", []))


def record_second_opinion(item: dict, result: dict) -> dict:
    """Gemini's answer to the same questions, kept beside the local result (never replaces it,
    never triggers work)."""
    qa = state(item)
    qa["second_opinion"] = {"backend": "gemini", "issues": [dict(entry, severity="advisory")
                                                            for entry in result.get("issues", [])],
                            "layers": result.get("layers", []), "warnings": result.get("warnings", []),
                            "people_count": result.get("people_count"), "checked_at": _now()}
    return qa["second_opinion"]


def needs_qa(item: dict) -> bool:
    qa = item.get("qa") or {}
    return qa.get("version") != base.QA_VERSION or qa.get("verdict") == "recheck"


def needs_fix(item: dict) -> bool:
    qa = item.get("qa") or {}
    return qa.get("status") == "fail" and qa.get("verdict") in ("edit", "regenerate") and not qa.get("flagged")


def save_version(output, item: dict, image_path: Path) -> dict:
    """Copy the current image to history/<id>_vN.jpg with the QA verdict it has now."""
    qa = state(item)
    number = len(qa["history"]) + 1
    relative = HISTORY / f"{safe_filename(item['shot_id'])}_v{number}{image_path.suffix or '.jpg'}"
    target = Path(output) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_path, target)
    entry = {"version": number, "path": relative.as_posix(), "verdict": qa.get("verdict"),
             "blocking": len(base.blocking(qa)), "issues": qa.get("issues", []), "at": _now()}
    if item.pop("source", None) == "external":            # imported from outside: the image leaving the shot, not the next one
        entry["source"] = "external"
        from video.shot_generation import update_provenance
        update_provenance(output, item["shot_id"], source=None)
    qa["history"].append(entry)
    return entry


def restore_best(output, item: dict, image_path: Path) -> dict | None:
    """Put back the version with the fewest blocking issues (when it beats the current image) and flag."""
    qa = state(item)
    current = len(base.blocking(qa))
    best = min(qa["history"], key=lambda entry: entry["blocking"], default=None)
    qa["flagged"] = True
    if best is None or best["blocking"] >= current:
        return None
    save_version(output, item, image_path)
    shutil.copy2(Path(output) / best["path"], image_path)
    qa.update(issues=best["issues"], verdict=best["verdict"], status="fail",
              notes=base.notes({"issues": best["issues"]}))
    return best


REMOVAL_KINDS = {"remove_figure", "extra_person", "duplicate_person"}
STRICT_KEEP_KINDS = REMOVAL_KINDS | {"text", "custom"}


_DETECTOR = []


def _detector():
    """Layer-1 person detector for edit verification (loaded once), or None when OpenCV/models are missing."""
    if not _DETECTOR:
        from video.qa import deterministic as det
        found = det.Detector()
        _DETECTOR.append(found if found.available() else None)
    return _DETECTOR[0]


def _instruction(plan: dict, item: dict, vb: dict | None, source: Image.Image | None, detection: dict | None,
                 crop=None) -> str:
    """The plan's instruction with a concrete target for a figure removal and explicit keep clauses
    for the listed characters visible in the edited area (Phase 3.5.3-B)."""
    text = plan["instruction"]
    boxes = [entry[0] for entry in plan["boxes"]]
    removal = bool(REMOVAL_KINDS & set(plan["kinds"]))
    if removal and len(boxes) == 1 and source is not None:
        text = text.replace("Remove the figure in <obj_1>", f"Remove {region.describe_figure(source, boxes[0])} in <obj_1>")
    if not vb:
        return text
    keep = [cid for cid in item.get("characters") or [] if cid not in plan.get("targets", [])]
    if detection is not None and crop is not None and source is not None:
        width, height = source.size
        window = [crop[0] / width, crop[1] / height, crop[2] / width, crop[3] / height]
        people = sorted(detection.get("persons", []), key=lambda person: -base.area(person["box"]))
        listed = list(item.get("characters") or [])
        visible = {cid for cid, person in zip(listed, people) if region._intersection(person["box"], window) > 0}
        keep = [cid for cid in keep if cid in visible] if len(people) >= len(listed) else keep
    clause = region.keep_clause(vb, item, keep)
    if clause and not (STRICT_KEEP_KINDS & set(plan["kinds"])):
        clause = clause.replace("exactly as they are", "otherwise unchanged (faces, clothes, poses)")
    if boxes:
        clause = (clause + " Change nothing outside <obj_1>." if len(boxes) == 1 else
                  clause + " Change nothing outside the marked boxes.").strip()
    if clause and not text.rstrip().endswith((".", "!", "?")):
        text = text.rstrip() + "."
    return f"{text} {clause}".strip()


def edit_with_plan(generator, output, item: dict, plan: dict, *, image_path, base_job: ImageJob,
                   origin: str = "qa", progress: Callable[[str], None] = print, vb: dict | None = None,
                   detector=None) -> dict:
    """One Klein region edit of the current image from an ``edits.compose`` plan. The image
    before it is kept as a history version; on failure the original stays and the failed
    edit is recorded. ``origin`` "human" (GUI review) counts apart from the QA edit budget.

    With boxes (Phase 3.5.3) the edit runs on an enlarged crop and is composited back through
    a feathered mask, so nothing outside the boxes can change; an edit that changes nothing
    inside them is reported as "no_effect" and no version is kept. ``detector`` (default: the
    layer-1 detector when installed, ``False`` to skip) checks that a removed figure is gone."""
    output, image_path = Path(output), Path(image_path)
    qa = state(item)
    version = save_version(output, item, image_path)
    counter = "edits" if origin == "qa" else "human_edits"
    number = int(qa.get(counter, 0)) + 1
    seed_number = number if origin == "qa" else 100 + number
    source_path = output / version["path"]
    with Image.open(source_path) as opened:
        source = opened.convert("RGB")
    boxes = [entry[0] for entry in plan["boxes"]]
    crop = region.crop_window(region.union(boxes), source.size) if boxes else None
    if crop and (crop[2] - crop[0]) > region.MAX_CROP_FRACTION * source.width:
        crop = None                                   # the boxes span most of the frame: whole-image edit
    if detector is None:
        detector = _detector()
    detection = detector.detect(source_path) if detector else None
    instruction = _instruction(plan, item, vb, source, detection, crop)
    job_id = f"{item['shot_id']}_{'edit' if origin == 'qa' else 'human'}{number}"
    seed = base_job.seed + EDIT_SEED_OFFSET * seed_number
    stem = source_path.with_suffix("")
    if crop:
        size = region.edit_size(crop)
        before, after = Path(f"{stem}_crop_before.jpg"), Path(f"{stem}_crop_after.jpg")
        source.crop(crop).resize(size, Image.LANCZOS).save(before, "JPEG", quality=95)
        job = ImageJob(job_id=job_id, prompt=instruction, seed=seed, output_path=str(after), kind="region_edit",
                       references=[str(before), *plan["references"]],
                       edit_boxes=[[region.to_crop(box, crop, source.size), desc] for box, desc in plan["boxes"]],
                       size=size, final_size=size)
    else:
        job = ImageJob(job_id=job_id, prompt=instruction, seed=seed, output_path=str(image_path), kind="region_edit",
                       references=[str(source_path), *plan["references"]],
                       edit_boxes=[list(entry) for entry in plan["boxes"]], size=base_job.size,
                       final_size=base_job.final_size)
    progress(f"   ✎ {item['shot_id']}: {'region' if origin == 'qa' else 'review'} edit {number}"
             f"{' (crop ' + str(crop[2] - crop[0]) + 'x' + str(crop[3] - crop[1]) + ' px)' if crop else ''} — "
             f"{instruction[:160]}")
    verify = {}
    try:
        record = generator.generate(job)
        if crop:
            with Image.open(job.output_path) as edited:
                edited = edited.convert("RGB")
            soft, hard, ring = region.mask_for(boxes, crop, source.size)
            result = region.composite(source, edited, crop, soft, ring)
            change = region.check(source, result, crop, hard)
            verify["inside_change"] = round(change, 2)
            if change < region.MIN_CHANGE:
                raise region.NoEffect(f"the edit changed nothing inside the box (mean difference {change:.1f})")
            result.save(image_path, "JPEG", quality=93)
        if detector and detection is not None:
            verify.update(_verify_people(detector, detection, image_path, boxes, plan))
    except (GenerationFailed, region.NoEffect) as exc:
        qa["history"].pop()
        for path in (source_path, Path(f"{stem}_crop_before.jpg"), Path(f"{stem}_crop_after.jpg")):
            path.unlink(missing_ok=True)                  # the version number is used again next time
        no_effect = isinstance(exc, region.NoEffect)
        action = {"action": "no_effect" if no_effect else "failed", "tried": "edit", "origin": origin,
                  "error": str(exc)[:200], "instruction": instruction, "at": _now()}
        qa["actions"].append(action)
        progress(f"   ⚠ {item['shot_id']}: " + ("edição sem efeito, nada salvo" if no_effect else "edit failed")
                 + f", original kept: {exc}")
        return action
    qa[counter] = number
    action = {"action": "edit", "origin": origin, "number": number, "instruction": instruction,
              "boxes": boxes, "kinds": plan["kinds"], "crop": list(crop) if crop else None,
              "references": [Path(path).name for path in plan["references"]], "seed": record.get("seed"),
              "replaced_version": version["version"], "at": _now(), "seconds": record.get("seconds")}
    if verify:
        action["verify"] = verify
        if verify.get("warning"):
            progress(f"   ⚠ {item['shot_id']}: {verify['warning']}")
    qa["actions"].append(action)
    qa.update(status="pending", verdict="recheck", flagged=False)
    return action


def _verify_people(detector, before: dict, image_path: Path, boxes: list, plan: dict) -> dict:
    """Layer-1 people count after the edit: a removal must lower the count inside the boxes, any
    other edit must keep the count outside them."""
    after = detector.detect(image_path)
    inside = (region.persons_inside(before, boxes), region.persons_inside(after, boxes)) if boxes else (0, 0)
    outside = (len(before.get("persons", [])) - inside[0], len(after.get("persons", [])) - inside[1])
    found = {"people_inside": list(inside), "people_outside": list(outside)}
    if REMOVAL_KINDS & set(plan["kinds"]) and boxes and inside[1] >= inside[0] > 0:
        found["warning"] = "a figura continua dentro da caixa (detector)"
    elif not REMOVAL_KINDS & set(plan["kinds"]) and outside[1] != outside[0]:
        found["warning"] = f"o número de pessoas fora da caixa mudou ({outside[0]} → {outside[1]})"
    return found


def restore_version(output, item: dict, image_path, number: int) -> dict:
    """Undo: the current image becomes a new history version, version ``number`` comes back."""
    output, image_path = Path(output), Path(image_path)
    qa = state(item)
    entry = next((value for value in qa["history"] if value["version"] == int(number)), None)
    if entry is None or not (output / entry["path"]).is_file():
        raise FileNotFoundError(f"no version {number} for {item['shot_id']}")
    saved = save_version(output, item, image_path)
    shutil.copy2(output / entry["path"], image_path)
    if entry.get("source"):
        item["source"] = entry["source"]
    action = {"action": "restore", "version": entry["version"], "replaced_version": saved["version"], "at": _now()}
    qa["actions"].append(action)
    qa.update(issues=entry.get("issues", []), verdict="recheck", status="pending",
              notes=base.notes({"issues": entry.get("issues", [])}))
    return action


def apply(generator, output, item: dict, vb: dict, *, image_path, job_factory: Callable[[int], ImageJob],
          custom: str = "", custom_box=None, force: str = "", progress: Callable[[str], None] = print) -> dict:
    """One fix step for a shot whose ``qa`` holds a failed verdict. Returns the action record.

    ``job_factory(seed_offset)`` builds the shot's normal generation job (used to regenerate
    and for the edit's seed/sizes); ``custom``/``custom_box`` are the user's own edit
    instruction; ``force`` ("edit"/"regenerate") overrides the verdict (GUI buttons)."""
    output, image_path = Path(output), Path(image_path)
    qa = state(item)
    verdict = force or ("edit" if custom else qa.get("verdict"))
    retries = int(getattr(config, "IMAGEGEN_QA_RETRIES", 2))
    if verdict == "edit" and not custom and qa["edits"] >= decision.max_edits() and not force:
        verdict = "regenerate"
    if verdict == "regenerate" and qa["regenerations"] >= retries and not force:
        best = restore_best(output, item, image_path)
        action = {"action": "kept_best", "version": best["version"] if best else None, "at": _now()}
        progress(f"   ⛔ {item['shot_id']}: QA budget spent ({qa['edits']} edit(s), {qa['regenerations']} new "
                 f"seed(s)); best version kept and flagged")
        qa["actions"].append(action)
        return action
    if verdict not in ("edit", "regenerate"):
        return {"action": "none", "at": _now()}
    if verdict == "edit":
        plan = edits.compose(base.blocking(qa), vb, output, custom=custom, custom_box=custom_box)
        return edit_with_plan(generator, output, item, plan, image_path=image_path, base_job=job_factory(0),
                              progress=progress, vb=vb)
    version = save_version(output, item, image_path)
    number = qa["regenerations"] + 1
    try:
        progress(f"   ↻ {item['shot_id']}: new seed {number}/{retries}")
        record = generator.generate(job_factory(QA_SEED_OFFSET * number))
    except GenerationFailed as exc:
        qa["history"].pop()
        (output / version["path"]).unlink(missing_ok=True)
        action = {"action": "failed", "tried": verdict, "error": str(exc)[:200], "at": _now()}
        qa["actions"].append(action)
        progress(f"   ⚠ {item['shot_id']}: {verdict} failed: {exc}")
        return action
    qa["regenerations"] = number
    action = {"action": "regenerate", "number": number, "seed": record.get("seed"),
              "replaced_version": version["version"], "at": _now(), "seconds": record.get("seconds")}
    qa["actions"].append(action)
    qa.update(status="pending", verdict="recheck", flagged=False)
    return action
