"""Visual rhythm from the same images (Phase 4.2-A).

The archived episodes held each image 9.5-9.7 s (max 19 s): on a phone, a slideshow with a voice. The GPU budget is
the same, so the rhythm comes from **more views per generated image**:

* ``views_for`` — from one shot: the full frame, a medium crop around the main subject (layer-1 person/face boxes),
  a detail crop (a face, or the busiest region: hands, the story object, the light). Crops are 16:9, never cut
  through a face, keep the eye-line in the upper part, and are rejected when mostly empty texture. Without an
  upscaler a crop is at least 60 % of the frame; with one, 40 %.
* ``split`` — each shot's time is cut into views at word or sentence boundaries (``narration_words.json``),
  preferring the attention events of 4.1 (question, reveal, dialogue turn), to the beat's target seconds per view
  (``RHYTHM``); never more than 7 s without a visual change, except the final held image.
* ``report`` — average and maximum seconds per view, per scene, against the targets (shown in the GUI).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

import config

DEFAULT_RHYTHM = {"cold_open": [2.0, 3.0], "tension": [2.0, 4.0], "development": [3.0, 5.0], "reflection": [4.0, 7.0],
                  "max_hold": 7.0}
BEATS = {"cold_open": "cold_open", "reversal": "tension", "climax": "tension", "opening": "development",
         "setup": "development", "development": "development", "resolution": "reflection", "aftermath": "reflection"}
ASPECT = 16 / 9
MIN_SHARE_UPSCALED = 0.40
MIN_SHARE_PLAIN = 0.60
EMPTY_EDGES = 0.015            # mean edge density below this: empty texture
SNAP_SECONDS = 0.8             # how far a cut may move to land on a word boundary


def settings() -> dict:
    return {**DEFAULT_RHYTHM, **(getattr(config, "RHYTHM", None) or {})}


def enabled() -> bool:
    return bool(getattr(config, "RHYTHM_ENABLED", True))


def beat_for(section_type: str) -> str:
    return BEATS.get(str(section_type or ""), "development")


# ── Crops ───────────────────────────────────────────────────────────────────

def _fit(cx: float, cy: float, width: float) -> list[float]:
    """A 16:9 box (normalised to a 16:9 frame) of ``width`` centred near (cx, cy), kept inside the frame."""
    width = min(1.0, width)
    height = width                                       # same share of each side keeps the 16:9 aspect
    x0 = min(max(0.0, cx - width / 2), 1.0 - width)
    y0 = min(max(0.0, cy - height / 2), 1.0 - height)
    return [round(x0, 4), round(y0, 4), round(x0 + width, 4), round(y0 + height, 4)]


def _inside(inner, outer) -> bool:
    return inner[0] >= outer[0] - 1e-6 and inner[1] >= outer[1] - 1e-6 and inner[2] <= outer[2] + 1e-6 and inner[3] <= outer[3] + 1e-6


def _overlaps(first, second) -> bool:
    return not (first[2] <= second[0] or second[2] <= first[0] or first[3] <= second[1] or second[3] <= first[1])


def cuts_face(box, faces) -> bool:
    """A face partly in, partly out of the crop."""
    return any(_overlaps(box, face) and not _inside(face, box) for face in faces)


def eye_line_ok(box, faces) -> bool:
    """Faces inside the crop sit in its upper part (their centre above 60 % of the crop's height)."""
    height = box[3] - box[1]
    return all((((face[1] + face[3]) / 2) - box[1]) / height <= 0.6 for face in faces if _inside(face, box))


def edge_density(gray: np.ndarray, box) -> float:
    height, width = gray.shape
    region = gray[int(box[1] * height):int(box[3] * height), int(box[0] * width):int(box[2] * width)]
    if region.size < 16:
        return 0.0
    gx, gy = np.abs(np.diff(region, axis=1)), np.abs(np.diff(region, axis=0))
    return float(((gx[:-1, :] > 18) | (gy[:, :-1] > 18)).mean())


def _adjust_for_faces(box, faces, width):
    """Move the crop so every face it touches is fully inside (or fully outside)."""
    for face in faces:
        if _overlaps(box, face) and not _inside(face, box):
            cx = min(max((face[0] + face[2]) / 2, width / 2), 1 - width / 2)
            cy = min(max(face[1] + (face[3] - face[1]) * 0.5 + width * 0.12, width / 2), 1 - width / 2)
            box = _fit(cx, cy, width)
    return box


def views_for(image: Image.Image, persons: list[dict], *, upscaled: bool) -> list[dict]:
    """Full, medium and detail views of one image ([{"kind", "box"}]); weak crops are left out."""
    minimum = MIN_SHARE_UPSCALED if upscaled else MIN_SHARE_PLAIN
    gray = np.asarray(image.convert("L").resize((320, 180)), dtype=np.int16)
    faces = [person["face"]["box"] for person in persons if person.get("face")]
    bodies = sorted((person["box"] for person in persons if person.get("box")),
                    key=lambda box: -(box[2] - box[0]) * (box[3] - box[1]))
    found = [{"kind": "full", "box": [0.0, 0.0, 1.0, 1.0]}]
    if bodies:
        main = bodies[0]
        width = max(minimum, min(0.8, (main[2] - main[0]) * 1.5, (main[3] - main[1]) * 1.3))
        medium = _adjust_for_faces(_fit((main[0] + main[2]) / 2, main[1] + (main[3] - main[1]) * 0.42, width), faces, width)
    else:                                                # no person: the busiest region at 70 %
        medium = _busiest(gray, max(minimum, 0.7), faces)
    if medium and not cuts_face(medium, faces) and eye_line_ok(medium, faces) and edge_density(gray, medium) >= EMPTY_EDGES:
        found.append({"kind": "medium", "box": medium})
    detail = None
    if faces:
        face = max(faces, key=lambda box: (box[2] - box[0]) * (box[3] - box[1]))
        width = max(minimum, min(0.6, (face[2] - face[0]) * 3.2))
        detail = _adjust_for_faces(_fit((face[0] + face[2]) / 2, face[1] + (face[3] - face[1]) * 0.8, width), faces, width)
    else:
        detail = _busiest(gray, minimum, faces)
    if detail and detail != (found[-1]["box"] if len(found) > 1 else None) and not cuts_face(detail, faces) \
            and eye_line_ok(detail, faces) and edge_density(gray, detail) >= EMPTY_EDGES * 1.5:
        found.append({"kind": "detail", "box": detail})
    return found


def _busiest(gray: np.ndarray, width: float, faces) -> list[float] | None:
    """The crop of ``width`` with the most edges (an object, hands, a light) that does not cut a face."""
    best, best_score = None, -1.0
    for cy in np.linspace(width / 2, 1 - width / 2, 4):
        for cx in np.linspace(width / 2, 1 - width / 2, 5):
            box = _fit(float(cx), float(cy), width)
            if cuts_face(box, faces):
                continue
            score = edge_density(gray, box)
            if score > best_score:
                best, best_score = box, score
    return best


# ── Splitting a shot into views ──────────────────────────────────────────────

def boundaries(words: list[dict], events: set[float] | None = None) -> list[tuple[float, bool]]:
    """Candidate cut times: every word start (sentence starts and attention events marked as preferred)."""
    found, after_sentence = [], True
    for word in words:
        start = float(word["start"])
        found.append((start, after_sentence or start in (events or set())))
        after_sentence = bool(word.get("sentence_end"))
    return found


def _snap(target: float, candidates: list[tuple[float, bool]], low: float, high: float) -> float:
    """The nearest preferred boundary within reach, else the nearest word start, else the target."""
    near = [(time, preferred) for time, preferred in candidates if low < time < high and abs(time - target) <= SNAP_SECONDS]
    preferred = [time for time, good in near if good]
    pool = preferred or [time for time, _ in near]
    return min(pool, key=lambda time: abs(time - target)) if pool else target


def split(start: float, end: float, views: list[dict], beat: str, candidates: list[tuple[float, bool]],
          *, final: bool = False) -> list[dict]:
    """[{"kind", "box", "start", "end"}] covering [start, end] at the beat's pace, cut on word boundaries."""
    rule = settings()
    low, high = rule.get(beat, rule["development"])
    duration = end - start
    if final and duration <= rule["max_hold"] * 2:
        count = max(1, math.ceil(duration / max(high, rule["max_hold"])))
    else:
        count = max(1, math.ceil(duration / high))
        while count > 1 and duration / count < low * 0.8:
            count -= 1
    edges = [start]
    for index in range(1, count):
        target = start + duration * index / count
        edges.append(_snap(target, candidates, edges[-1] + 0.6, end - 0.6))
    edges.append(end)
    order = _order(views, count)
    return [{**order[index], "start": round(edges[index], 3), "end": round(edges[index + 1], 3)} for index in range(count)]


def _order(views: list[dict], count: int) -> list[dict]:
    """Full first, then medium, detail; reuse with a different motion when there are more slots than views."""
    sequence = [views[index % len(views)] for index in range(count)]
    return [{**view, "motion_variant": index // len(views)} for index, view in enumerate(sequence)]


def report(sections: list[dict]) -> dict:
    """Seconds per view per scene against its beat's target ([{"title", "beat", "views", "average", "max", "target"}])."""
    rule = settings()
    rows = []
    for section in sections:
        views = section.get("views") or []
        if not views:
            continue
        lengths = [view["end"] - view["start"] for view in views]
        beat = section.get("beat", "development")
        rows.append({"title": section.get("title", ""), "beat": beat, "views": len(views),
                     "average": round(sum(lengths) / len(lengths), 2), "max": round(max(lengths), 2),
                     "target": rule.get(beat, rule["development"]),
                     "ok": max(lengths) <= rule["max_hold"] + 0.01 or section.get("final", False)})
    all_lengths = [view["end"] - view["start"] for section in sections for view in section.get("views") or []]
    return {"scenes": rows, "views": len(all_lengths),
            "average": round(sum(all_lengths) / len(all_lengths), 2) if all_lengths else 0,
            "max": round(max(all_lengths), 2) if all_lengths else 0, "max_hold": rule["max_hold"]}


SECONDS_PER_IMAGE = 30.0       # the GPU budget: ~30 s per generated image on 8 GB


def extra_requests(rows: list[dict]) -> list[dict]:
    """Key moments (the cold open, the reversal's first shot, the final image) whose views repeat: one extra
    generated image each would add variety. Proposals only; the GUI shows the GPU cost before rendering."""
    found = []
    for row in rows:
        views = row.get("views") or []
        key = row.get("beat") == "cold_open" or row.get("section_type") == "reversal" or row.get("final")
        kinds = [view.get("view") for view in views]
        if key and len(kinds) > len(set(kinds)) + 1:
            found.append({"section": row.get("title", ""), "reason": f"{len(kinds)} vistas de {len(set(kinds))} enquadramentos",
                          "images": 1})
    return found[:3]


def gpu_budget(extras: list[dict]) -> dict:
    images = sum(item["images"] for item in extras)
    return {"images": images, "seconds": round(images * SECONDS_PER_IMAGE)}
