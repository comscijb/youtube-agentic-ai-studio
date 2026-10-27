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
from video.qa import base, decision, edits

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
    qa.update(version=result.get("version"), status=result["status"], verdict=result["verdict"],
              reason=result.get("verdict_reason", ""), issues=result.get("issues", []),
              people_count=result.get("people_count"), layers=result.get("layers", []),
              warnings=result.get("warnings", []), notes=base.notes(result), checked_at=_now())
    return qa


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
    version = save_version(output, item, image_path)
    base_job = job_factory(0)
    try:
        if verdict == "edit":
            plan = edits.compose(base.blocking(qa), vb, output, custom=custom, custom_box=custom_box)
            number = qa["edits"] + 1
            job = ImageJob(job_id=f"{item['shot_id']}_edit{number}", prompt=plan["instruction"],
                           seed=base_job.seed + EDIT_SEED_OFFSET * number, output_path=str(image_path),
                           kind="region_edit", references=[str(output / version["path"]), *plan["references"]],
                           edit_boxes=[list(entry) for entry in plan["boxes"]], size=base_job.size,
                           final_size=base_job.final_size)
            progress(f"   ✎ {item['shot_id']}: region edit {number} — {plan['instruction'][:140]}")
            record = generator.generate(job)
            qa["edits"] = number
            action = {"action": "edit", "number": number, "instruction": plan["instruction"],
                      "boxes": [entry[0] for entry in plan["boxes"]], "kinds": plan["kinds"],
                      "references": [Path(path).name for path in plan["references"]]}
        else:
            number = qa["regenerations"] + 1
            job = job_factory(QA_SEED_OFFSET * number)
            progress(f"   ↻ {item['shot_id']}: new seed {number}/{retries}")
            record = generator.generate(job)
            qa["regenerations"] = number
            action = {"action": "regenerate", "number": number}
    except GenerationFailed as exc:
        qa["history"].pop()
        (output / version["path"]).unlink(missing_ok=True)
        action = {"action": "failed", "tried": verdict, "error": str(exc)[:200], "at": _now()}
        qa["actions"].append(action)
        progress(f"   ⚠ {item['shot_id']}: {verdict} failed: {exc}")
        return action
    action.update(seed=record.get("seed"), replaced_version=version["version"], at=_now(),
                  seconds=record.get("seconds"))
    qa["actions"].append(action)
    qa.update(status="pending", verdict="recheck", flagged=False)
    return action
