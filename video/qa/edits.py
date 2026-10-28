"""Region-edit instructions composed by code from the QA issues (Phase 3.5-D).

One edit fixes up to two blocking issues (``decision.EDIT_MAX_ISSUES``). The
shot is ``<ref_image_0>``; each detector box becomes ``<obj_N>`` in order; an
approved character portrait is attached as the next image when the fix needs
the character's identity (a missing marker, a wrong face). Issues without a
box (injuries, lettering found by the VLM) give an instruction-only edit.
"""
from __future__ import annotations

from agents import shot_list as shots_module
from agents import visual_bible as vbible

ORDINALS = ("first", "second", "third", "fourth")
KEEP = "keep everything else unchanged"


def _who(vb: dict, cid: str) -> tuple[str, str]:
    noun = shots_module._noun((vbible.character(vb, cid) or {}).get("descriptor", "")) if cid else "person"
    return noun, {"man": "his", "woman": "her"}.get(noun, "their")


def instruction_for(item: dict, obj: str | None, image: str | None, vb: dict) -> str:
    """One sentence for one issue. ``obj`` is "<obj_N>" or None; ``image`` the ordinal of its portrait."""
    kind = item["type"]
    noun, pronoun = _who(vb, item.get("target", ""))
    where = f" in {obj}" if obj else ""
    if kind == "missing_marker":
        marker = item.get("marker") or "identity markers"
        source = f" of the {noun} in the {image} image" if image else ""
        return (f"On the {noun}{where}, add the {marker}{source}; keep {pronoun} face, pose, lighting and "
                f"everything else unchanged.")
    if kind == "unstated_injury":
        target = f"the skin{where}" if obj else "every person's skin"
        return (f"Remove the blood, wounds and red marks on {target}, restoring clean skin consistent with the "
                f"surrounding drawing; {KEEP}.")
    if kind in ("extra_person", "duplicate_person"):
        if not obj:
            return f"Remove the extra figure and continue the background behind it in the same drawing style; {KEEP}."
        return f"Remove the figure{where} and continue the background behind it in the same drawing style; {KEEP}."
    if kind == "text":
        target = f"the lettering{where}" if obj else "all lettering, numbers and watermarks"
        return f"Remove {target}, continuing the surface behind it; {KEEP}."
    if kind == "identity":
        source = f" with the face of the {noun} in the {image} image" if image else ""
        return f"Replace the face of the {noun}{where}{source}, keeping pose, lighting and drawing style."
    return f"Fix {item.get('note') or kind}{where}; {KEEP}."


def compose(issues: list[dict], vb: dict, output_dir, *, custom: str = "", custom_box=None) -> dict:
    """{"instruction", "boxes": [(box, desc)], "references": [portrait paths], "kinds"} for one region edit.

    ``custom`` is the user's own instruction from the GUI (with an optional box)."""
    boxes, references, sentences = [], [], []
    if custom:
        if custom_box:
            boxes.append((custom_box, "region to edit"))
            custom = custom.replace("<box>", "<obj_1>")
            if "<obj_1>" not in custom:
                custom = f"In <obj_1>: {custom}"
        return {"instruction": f"In <ref_image_0>, {custom.strip()}", "boxes": boxes, "references": [],
                "kinds": ["custom"]}
    for item in issues:
        obj = None
        if item.get("box"):
            boxes.append((item["box"], item["type"].replace("_", " ")))
            obj = f"<obj_{len(boxes)}>"
        image = None
        cid = item.get("target", "")
        if item["type"] in ("missing_marker", "identity") and cid:
            portrait = vbible.approved_reference(vb, "character", cid, output_dir)
            if portrait and len(references) < 3:
                if portrait not in references:
                    references.append(portrait)
                image = ORDINALS[1 + references.index(portrait)]
        sentences.append(instruction_for(item, obj, image, vb))
    return {"instruction": "In <ref_image_0>: " + " ".join(sentences), "boxes": boxes, "references": references,
            "kinds": [item["type"] for item in issues],
            "targets": [item["target"] for item in issues if item.get("target")]}
