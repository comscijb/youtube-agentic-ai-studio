"""Does the rendered image follow the narration? (Phase 3.9.9-D.)

* **Vision questions** (optional, off by default, a per-project toggle): yes/no questions generated from the shot spec
  and its staging ("Is the woman inside a car, in the driver's seat?", "Is any person merged with or passing through
  furniture?"), asked to Gemini Vision in ONE call per shot on a 768 px copy. The local VLM is unreliable for this
  (3.5.1). The cost estimate comes from ``config.GEMINI_PRICE_PER_IMAGE``.
* **Person fused with an object** (deterministic, a candidate only): a person box whose body ends ON a strong horizontal
  furniture edge. Measured on "She Filmed Every Moment": it finds the real fusion (s03_05 v1, Hannah's torso coming out
  of the kitchen island) but also people legitimately behind a counter (s07_01), and the depth map does not separate
  them (depth gap 0.24 fused vs 0.26 behind the counter). So a candidate only raises the vision question, which decides.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image

import config

IMAGE_SIZE = 768
EDGE_RATIO = 6.0              # the furniture edge against the image's median horizontal gradient
EDGE_SPAN = 0.6               # of the person box's width
SAME_PLANE = 0.05             # depth difference above / below the edge
NO_LEGS_ASPECT = 1.7          # a standing figure is taller than this (height / width)


def _who(cid: str, vb: dict) -> str:
    from agents import shot_list as shots_module
    from agents import visual_bible as vbible
    descriptor = (vbible.character(vb, cid) or {}).get("descriptor", "")
    if re.search(r"\b(?:boy|girl|child)\b", descriptor):
        return "the " + re.search(r"\b(?:boy|girl|child)\b", descriptor).group(0)
    return f"the {shots_module._noun(descriptor) or 'person'}"


def questions(shot: dict, vb: dict) -> list[dict]:
    """[{qid, prompt, expected}] from the spec and staging; ``expected`` is the answer that means "follows"."""
    from agents import staging as staging_module
    location = next((item for item in vb.get("locations", []) if item.get("location_id") == shot.get("location")), {})
    found = []
    for item in (shot.get("staging") or {}).get("characters", []):
        place = staging_module._position(item.get("position", ""))
        inside = " inside a car," if location.get("enclosed") else ""
        posture = f" {item['posture']}" if item.get("posture") in ("seated", "standing", "lying", "kneeling") else ""
        found.append({"qid": f"pos_{item['id']}", "prompt": f"Is {_who(item['id'], vb)}{posture}{inside} {place}?".replace("  ", " "),
                      "expected": "yes", "label": f"{item['id']} {item.get('position', '')}"})
    listed = shot.get("characters") or []
    if listed and not (shot.get("staging") or {}).get("characters"):
        for cid in listed:
            found.append({"qid": f"present_{cid}", "prompt": f"Is {_who(cid, vb)} visible in the image?", "expected": "yes", "label": f"{cid} visível"})
    if listed:
        found.append({"qid": "fused", "prompt": "Is any person merged with, cut by or passing through furniture or an object?",
                      "expected": "no", "label": "personagem fundido com objeto"})
    if not shot.get("others_present"):
        count = len(listed)
        found.append({"qid": "extra", "prompt": f"Are there more than {count} {'person' if count == 1 else 'people'} in the image?" if count
                      else "Is there any person in the image?", "expected": "no", "label": "pessoa a mais"})
    if location:
        name = re.sub(r"_", " ", str(location.get("name") or location.get("location_id"))).lower()
        found.append({"qid": "setting", "prompt": f"Does the setting look like {name}?", "expected": "yes", "label": f"cenário: {name}"})
    return found


def estimate(count: int) -> dict:
    price = float(getattr(config, "GEMINI_PRICE_PER_IMAGE", 0) or 0)
    return {"calls": count, "price_per_call": price, "total": round(count * price, 4), "known": price > 0}


def ask(shot: dict, image_path, vb: dict, *, generator=None) -> dict:
    """One Gemini Vision call: {qid: answer} for every question, plus the failures."""
    from agents.gemini_client import generate
    from agents.narrative import extract_json
    items = questions(shot, vb)
    if not items:
        return {"answers": {}, "failures": [], "asked": 0}
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    image.thumbnail((IMAGE_SIZE, IMAGE_SIZE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    prompt = ("You check one frame of an illustrated film. Answer each question about the image with yes, no or unclear. Return "
              "ONLY JSON {" + ", ".join(f'"{item["qid"]}": "yes|no|unclear"' for item in items) + "}.\n"
              + "\n".join(f'{item["qid"]}: {item["prompt"]}' for item in items))
    raw = (generator or generate)(prompt, getattr(config, "IMAGEGEN_VISUAL_QA_MODEL", "") or None, stage="image_adherence",
                                  temperature=0.0, response_mime_type="application/json", images=[(buffer.getvalue(), "image/jpeg")])
    answer = extract_json(raw, "image adherence")
    answers = {item["qid"]: str(answer.get(item["qid"], "unclear")).strip().lower() for item in items}
    failures = [item for item in items if answers[item["qid"]] not in (item["expected"], "unclear")]
    return {"answers": answers, "failures": [{"qid": item["qid"], "label": item["label"], "prompt": item["prompt"]} for item in failures],
            "asked": len(items)}


def hints(result: dict) -> list[dict]:
    from video.qa import base
    found = []
    for item in result.get("failures", []):
        note = "personagem fundido com objeto" if item["qid"] == "fused" else f"fora da narração: {item['label']}"
        found.append(base.issue("wrong_subject" if item["qid"] != "fused" else "other", severity="advisory", source="gemini", note=note))
    return found


def patch_for(result: dict) -> str:
    """The sentence a regeneration adds so the same failure does not come back."""
    parts = []
    for item in result.get("failures", []):
        if item["qid"] == "fused":
            parts.append("Every person is clearly separate from the furniture, in front of it or behind it, the whole body readable")
        elif item["qid"] == "extra":
            parts.append("Nobody else anywhere in the frame")
        else:
            parts.append(re.sub(r"^(?:Is|Does|Are)\s+", "", item["prompt"]).rstrip("?").replace("the setting look like", "The setting is"))
    return ". ".join(part[0].upper() + part[1:] for part in parts if part)


# ── Person fused with an object (deterministic) ──────────────────────────────

def fused_with_object(image: Image.Image, persons: list[dict], depth: np.ndarray | None = None) -> list[dict]:
    """[{box, edge, depth_gap}] for people cut by a horizontal furniture edge with no legs or seat below, on one depth
    plane with the object. Advisory only."""
    import cv2
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    height, width = gray.shape
    gradient = np.abs(cv2.Sobel(cv2.GaussianBlur(gray, (0, 0), 1.5), cv2.CV_32F, 0, 1, ksize=3))
    median = float(np.median(gradient)) + 1e-3
    found = []
    for person in persons:
        x0, y0, x1, y1 = person["box"]
        tall, wide = y1 - y0, x1 - x0
        if tall < 0.15 or y1 > 0.92 or wide <= 0:
            continue                                        # a figure cut by the frame's bottom is just framing
        left, right = int(x0 * width), int(x1 * width)
        rows = range(int((y1 - 0.08 * tall) * height), min(height, int((y1 + 0.03 * tall) * height)))   # the body ends ON the edge
        best = None
        for row in rows:
            line = gradient[row, left:right]
            strong = line > EDGE_RATIO * median
            if strong.mean() >= EDGE_SPAN and (best is None or line.mean() > best[1]):
                best = (row, float(line.mean()))
        if best is None:
            continue
        row = best[0]
        gap = None
        if depth is not None:                               # reported, not used to decide (see the module note)
            scaled = np.asarray(Image.fromarray((depth * 65535).astype(np.uint16)).resize((width, height)), dtype=np.float32) / 65535.0
            above = scaled[max(0, row - int(0.06 * height)):row, left:right]
            below = scaled[row:min(height, row + int(0.06 * height)), left:right]
            if above.size and below.size:
                gap = abs(float(above.mean()) - float(below.mean()))
        found.append({"box": person["box"], "edge": round(best[1] / median, 1), "depth_gap": None if gap is None else round(gap, 3)})
    return found


def run(shot_list: dict, vb: dict, output, *, generator=None, detector=None, only=None, progress=print) -> dict:
    """"Checar aderência das imagens": one vision call per rendered shot with characters (or ``only``); stores
    shot["image_adherence"] and advisory hints. Returns {"checked", "failed", "estimate"}."""
    from video.qa import fixes
    output = Path(output)
    detector = detector if detector is not None else fixes._detector()
    targets = [shot for shot in shot_list.get("shots", []) if shot.get("characters") and not shot.get("reuse_of")
               and (only is None or shot["shot_id"] in only)
               and (output / str(shot.get("image_path", "")).replace("\\", "/")).is_file()]
    failed = []
    for number, shot in enumerate(targets, 1):
        path = output / str(shot["image_path"]).replace("\\", "/")
        candidates = []
        if detector is not None:
            try:
                with Image.open(path) as opened:
                    candidates = fused_with_object(opened.convert("RGB"), detector.detect(path).get("persons", []))
            except Exception:                            # noqa: BLE001 — the vision question still asks
                candidates = []
        try:
            result = ask(shot, path, vb, generator=generator)
        except Exception as exc:                         # noqa: BLE001 — quota, network: the shot stays unchecked
            progress(f"   ⚠ {shot['shot_id']}: image adherence skipped ({str(exc)[:120]})")
            continue
        result["fused_candidates"] = candidates
        result["patch"] = patch_for(result)
        shot["image_adherence"] = result
        qa = shot.setdefault("qa", {})
        qa["issues"] = [item for item in qa.get("issues") or [] if item.get("source") != "gemini" or
                        not str(item.get("note", "")).startswith(("fora da narração", "personagem fundido"))] + hints(result)
        if result["failures"]:
            failed.append(shot["shot_id"])
        progress(f"   [{number}/{len(targets)}] {shot['shot_id']}: " + (", ".join(item["label"] for item in result["failures"]) or "segue a narração"))
    return {"checked": len(targets), "failed": failed, "estimate": estimate(len(targets))}


def regenerate_patch(shot: dict) -> str:
    """"Gerar de novo com o prompt corrigido": the prompt patch from C plus the one from this check."""
    parts = [str(shot.get("prompt_patch") or "").strip().rstrip("."), str((shot.get("image_adherence") or {}).get("patch") or "").strip().rstrip(".")]
    return ". ".join(dict.fromkeys(part for part in parts if part))
