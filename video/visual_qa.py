"""Automatic visual QA of generated shots with Gemini vision (Phase 3.4-D).

After a shot (or the thumbnail) is generated, a downscaled copy (~768 px) and a
compact checklist go to Gemini (multimodal, JSON mode). The answer is turned
into blocking and advisory findings:

* blocking — wrong headcount, the same character twice, a visible face missing
  its signature markers (e.g. Julian's glasses), injuries/blood/marks the scene
  narration does not state, text/letters/watermark;
* advisory — unexpected extra people beyond the count, borders/panels,
  legibility notes.

Since Phase 3.5 the generation pipeline uses the local-first layered QA
(``video/qa/``); this module still provides ``enabled()`` and ``expected_for``
(what a shot should show), and the whole-image Gemini checklist kept for
reference. When Gemini is unavailable ``review`` is skipped with a warning.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from PIL import Image

import config
from agents import visual_bible as vbible

QA_VERSION = 1
STAGE = "visual_qa"
BLOCKING = ("headcount", "duplicate", "signature_marker", "injury", "text")


def enabled() -> bool:
    return bool(getattr(config, "IMAGEGEN_VISUAL_QA", True))


def expected_for(shot: dict, vb: dict, scene_narration: str = "") -> dict:
    """What the image should show: listed characters with their markers, and who else may be there."""
    from agents import shot_list as shots_module
    listed = list(shot.get("characters") or [])
    order = [item["character_id"] for item in vb.get("characters", [])]
    ranked = sorted(listed, key=lambda cid: order.index(cid) if cid in order else len(order))
    foreground = [cid for cid in listed if cid in ranked[:shots_module.MAX_CHARACTER_REFS]]
    characters = []
    for cid in foreground:
        entry = vbible.character(vb, cid) or {}
        characters.append({
            "id": cid, "who": f"a {shots_module._noun(entry.get('descriptor', ''))}",
            "markers": vbible.signature_markers(vb, cid),
            "outfit": vbible.wardrobe(vb, cid, shot.get("scene_id", "")),
        })
    return {"people": len(foreground), "background": len(listed) - len(foreground),
            "others_present": bool(shot.get("others_present")), "characters": characters,
            "shot_id": shot.get("shot_id", ""), "scene_id": shot.get("scene_id", ""), "shot_type": shot.get("shot_type", ""),
            "narration": str(scene_narration or shot.get("narration_excerpt", ""))[:1200]}


def checklist(expected: dict) -> str:
    people = expected["people"]
    others = ("Unnamed distant guests may also be present (do not count them)." if expected["others_present"]
              else "Nobody else should be present.")
    background = (f" {expected['background']} more distant, indistinct figure(s) may appear in the background "
                  "(do not count them)." if expected["background"] else "")
    return f"""You are the visual QA checker of an illustrated film. Look at the image and answer ONLY JSON.
Expected: exactly {people} main person/people.{background} {others}
Characters expected (with the identity markers that must be visible when their face is visible):
{json.dumps(expected["characters"], ensure_ascii=False)}
Scene narration (the only source of injuries, tears or marks that may appear):
{expected["narration"]}

Return {{"people_count": <int, main people you can see>,
 "duplicated_person": <true if two figures look like the same character/person>,
 "characters": [{{"id": "<id from the list>", "visible": <bool>, "face_visible": <bool>,
                 "missing_markers": [<markers from its list you cannot see although the face is visible>]}}],
 "unstated_injury": <true if blood, wounds, scratches, bruises or marks appear that the narration does not state>,
 "extra_people": <true if there are people beyond the expected ones>,
 "text_or_watermark": <true if letters, words, numbers, signatures or watermarks are visible>,
 "borders_or_panels": <true if the picture has paper margins, frames or comic panels>,
 "notes": "<one short sentence>"}}"""


def downscale(path, max_side: int | None = None) -> bytes:
    max_side = int(max_side or getattr(config, "IMAGEGEN_QA_MAX_SIDE", 768))
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def evaluate(answer: dict, expected: dict) -> tuple[list[str], list[str]]:
    """(blocking findings, advisory findings) from Gemini's checklist answer."""
    blocking, advisory = [], []
    count = answer.get("people_count")
    if isinstance(count, int):
        if count != expected["people"] and not (expected["others_present"] and count > expected["people"]):
            blocking.append(f"headcount: {count} people instead of {expected['people']}")
    if answer.get("duplicated_person"):
        blocking.append("duplicate: the same person appears twice")
    known = {item["id"]: item for item in expected["characters"]}
    for item in answer.get("characters") or []:
        if not isinstance(item, dict) or item.get("id") not in known:
            continue
        missing = [marker for marker in item.get("missing_markers") or [] if marker in known[item["id"]]["markers"]]
        if item.get("face_visible") and missing:
            blocking.append(f"signature_marker: {item['id']} without {', '.join(missing)}")
    if answer.get("unstated_injury"):
        blocking.append("injury: blood, wounds or marks the narration does not state")
    if answer.get("text_or_watermark"):
        blocking.append("text: letters or a watermark are visible")
    if answer.get("extra_people") and not expected["others_present"] and not blocking:
        advisory.append("extra people in the background")
    if answer.get("borders_or_panels"):
        advisory.append("borders or panels")
    if str(answer.get("notes") or "").strip():
        advisory.append(f"note: {str(answer['notes']).strip()[:200]}")
    return blocking, advisory


def review(path, expected: dict, *, generator=None) -> dict:
    """QA one image. Never raises: an unavailable Gemini gives status "skipped" with a warning."""
    from agents.gemini_client import generate
    from agents.narrative import extract_json
    generator = generator or generate
    started = time.monotonic()
    try:
        raw = generator(checklist(expected), getattr(config, "IMAGEGEN_VISUAL_QA_MODEL", "") or None,
                        stage=STAGE, temperature=0.0, response_mime_type="application/json",
                        images=[(downscale(path), "image/jpeg")])
        answer = extract_json(raw, "visual QA")
    except Exception as exc:                           # Gemini down, quota, parse error: never block
        return {"status": "skipped", "version": QA_VERSION, "blocking": [], "advisory": [],
                "warning": f"visual QA skipped: {exc.__class__.__name__}: {str(exc)[:160]}",
                "seconds": round(time.monotonic() - started, 1)}
    blocking, advisory = evaluate(answer, expected)
    return {"status": "fail" if blocking else "pass", "version": QA_VERSION, "blocking": blocking,
            "advisory": advisory, "people_count": answer.get("people_count"),
            "seconds": round(time.monotonic() - started, 1)}


def better(first: dict, second: dict) -> bool:
    """True when QA result ``first`` is better than ``second`` (pass beats fail; fewer blocking findings)."""
    rank = {"pass": 0, "skipped": 1, "fail": 2}
    return (rank.get(first["status"], 3), len(first["blocking"])) < (rank.get(second["status"], 3),
                                                                     len(second["blocking"]))


def qa_note(result: dict) -> list[str]:
    """Short notes for the GUI shot card."""
    if result.get("status") == "skipped":
        return [result.get("warning", "visual QA skipped")]
    return list(result.get("blocking") or []) + list(result.get("advisory") or [])


def path_with_suffix(path, suffix: str) -> Path:
    path = Path(path)
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")
