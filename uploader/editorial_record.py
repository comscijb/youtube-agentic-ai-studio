"""What the human did on an episode (Phase 3.9-D): evidence of creative input for a YPP review.

Collected when the episode is approved and stored in ``review_approval.json`` (archived with the
episode), as raw data and a short English summary:

* the premise brief the author wrote (if any);
* screenplay edits applied by hand (``human_editorial``);
* shots edited or regenerated from the review, shot descriptions rewritten, shots marked reviewed;
* character and location references approved;
* the title chosen (the author's, picked from the scored list, or typed) and the thumbnail variant;
* the review checklist itself (approval time, reviewer).
"""
from __future__ import annotations

from pathlib import Path

from agents.narrative import load_json


def build(run_dir) -> dict:
    run = Path(run_dir)
    script = load_json(run / "script.json") or {}
    shots = [shot for shot in (load_json(run / "shot_list.json") or {}).get("shots", []) if not shot.get("reuse_of")]
    vb = load_json(run / "visual_bible.json") or {}
    titles = load_json(run / "titles.json") or {}
    thumbnails = load_json(run / "thumbnails" / "thumbnails.json") or {}
    brief = load_json(run / "premise_brief.json") or {}

    edits = [edit for edit in script.get("editorial_edits") or [] if edit.get("author") == "human_editorial"]
    human_actions = {}
    for shot in shots:
        actions = (shot.get("qa") or {}).get("actions") or []
        mine = [action for action in actions if action.get("origin") == "human" or action.get("request")]
        if mine:
            human_actions[shot["shot_id"]] = [action.get("action") for action in mine]
    regenerated = sorted({shot["shot_id"] for shot in shots if int(shot.get("seed_offset", 0) or 0) > 0}
                         | {sid for sid, kinds in human_actions.items() if "regenerate" in kinds})
    edited = sorted(sid for sid, kinds in human_actions.items() if "edit" in kinds)
    rewritten = sorted(shot["shot_id"] for shot in shots if shot.get("action_edited_by_human"))
    reviewed = sum(1 for shot in shots if (shot.get("qa") or {}).get("reviewed"))
    references = sorted((vb.get("references") or {}).keys())
    selected = next((item for item in thumbnails.get("variants", []) if item.get("number") == thumbnails.get("selected")), None)
    return {
        "premise_brief": bool(brief),
        "premise_brief_fields": sorted(key for key in brief if key not in ("hash", "notes")),
        "script_edits": [{"scene": edit.get("scene_id"), "applied_at": edit.get("applied_at")} for edit in edits],
        "shots_total": len(shots), "shots_reviewed": reviewed,
        "shots_edited": edited, "shots_regenerated": regenerated, "shot_descriptions_rewritten": rewritten,
        "references_approved": references,
        "title": {"chosen": titles.get("selected") or script.get("title", ""),
                  "how": titles.get("selected_reason", "scored"),
                  "alternatives_considered": len(titles.get("candidates") or [])},
        "thumbnail": {"selected_variant": thumbnails.get("selected"),
                      "variants_generated": len(thumbnails.get("variants") or []),
                      "composition": (selected or {}).get("concept", {}).get("composition", "")},
    }


def summary(record: dict) -> str:
    """A few plain sentences a reviewer can read."""
    lines = []
    if record.get("premise_brief"):
        lines.append("The author wrote the premise brief (" + ", ".join(record["premise_brief_fields"][:8]) + ").")
    edits = record.get("script_edits") or []
    if edits:
        scenes = sorted({str(item["scene"]) for item in edits})
        lines.append(f"{len(edits)} screenplay edit(s) applied by hand in scene(s) {', '.join(scenes)}.")
    lines.append(f"{record.get('shots_reviewed', 0)} of {record.get('shots_total', 0)} shots reviewed by hand"
                 + (f"; {len(record['shots_edited'])} edited" if record.get("shots_edited") else "")
                 + (f"; {len(record['shots_regenerated'])} regenerated" if record.get("shots_regenerated") else "")
                 + (f"; {len(record['shot_descriptions_rewritten'])} shot descriptions rewritten"
                    if record.get("shot_descriptions_rewritten") else "") + ".")
    if record.get("references_approved"):
        lines.append(f"{len(record['references_approved'])} character/location reference(s) approved by hand.")
    title = record.get("title") or {}
    how = {"user": "picked by hand from the scored list", "edited": "typed by hand", "kept": "the author's own title",
           "scored": "the highest-scored candidate, approved"}.get(title.get("how"), "approved")
    lines.append(f"Title “{title.get('chosen', '')}”: {how} ({title.get('alternatives_considered', 0)} candidates).")
    thumbnail = record.get("thumbnail") or {}
    if thumbnail.get("selected_variant"):
        lines.append(f"Thumbnail variant #{thumbnail['selected_variant']} chosen from {thumbnail['variants_generated']}.")
    return " ".join(lines)
