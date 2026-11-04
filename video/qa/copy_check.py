"""Did a generated image copy its reference? (Phase 3.9.3-B)

With one reference FLUX Klein edits the image instead of borrowing its style. Shared drawing style must
not count, so the measure ignores tone and texture: SIFT keypoints matched between the two images that
**stay in place** (a copy keeps its chair where the board has it; hatching never lines up), plus the
correlation of the blurred edge maps.

Calibration on the real pairs (aligned matches / edge correlation):

* copies: board_6 and village_bakery_1-4 620-630 / 0.91, the_moor_2 508 / 0.85;
* a chair and a doorway pasted on a moor: the_moor_1 28 / 0.30;
* different pictures: every other candidate, and approved shots of episodes 2-3 at most 8 / 0.49.

Only the style boards can flag an image. A shot is meant to resemble its own location and characters (a
close-up reached 39 matches with its portrait), so those references are reported, never flagged. A
flagged image is regenerated once with a new seed and without the style anchor.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image

ANALYSIS_WIDTH = 640
FEATURES = 3000
RATIO_TEST = 0.75
IN_PLACE = 40              # pixels at the analysis width
COPY_MATCHES = 20
COPY_EDGE = 0.75
LABEL = "cópia da referência"


def _gray(source) -> np.ndarray:
    image = source if isinstance(source, Image.Image) else Image.open(source)
    try:
        height = ANALYSIS_WIDTH * 9 // 16
        return np.asarray(image.convert("L").resize((ANALYSIS_WIDTH, height), Image.LANCZOS), dtype=np.uint8)
    finally:
        if image is not source:
            image.close()


def _edges(gray: np.ndarray) -> np.ndarray:
    import cv2
    small = cv2.resize(gray, (256, 144), interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.GaussianBlur(small, (0, 0), 1.2), 40, 120).astype(np.float32)
    return cv2.GaussianBlur(edges, (0, 0), 4)


def compare(first, second) -> dict:
    """{"matches": keypoints matched in place, "edge": edge-map correlation} of two images or paths."""
    import cv2
    a, b = _gray(first), _gray(second)
    edge_a, edge_b = _edges(a), _edges(b)
    edge = 0.0
    if edge_a.std() > 0.5 and edge_b.std() > 0.5:
        edge = float(np.corrcoef(edge_a.ravel(), edge_b.ravel())[0, 1])
    sift = cv2.SIFT_create(nfeatures=FEATURES)
    keys_a, descriptors_a = sift.detectAndCompute(a, None)
    keys_b, descriptors_b = sift.detectAndCompute(b, None)
    matches = 0
    if descriptors_a is not None and descriptors_b is not None and len(keys_a) >= 2 and len(keys_b) >= 2:
        for pair in cv2.BFMatcher().knnMatch(descriptors_a, descriptors_b, k=2):
            if len(pair) == 2 and pair[0].distance < RATIO_TEST * pair[1].distance:
                start = np.array(keys_a[pair[0].queryIdx].pt)
                end = np.array(keys_b[pair[0].trainIdx].pt)
                if np.linalg.norm(start - end) < IN_PLACE:
                    matches += 1
    return {"matches": matches, "edge": round(edge, 3)}


def is_copy(score: dict) -> bool:
    return score["matches"] >= COPY_MATCHES or score["edge"] >= COPY_EDGE


def boards() -> list[str]:
    """Every approved style board."""
    from agents import style_refs
    return list(dict.fromkeys(list(style_refs.approved_paths()) + list(style_refs.empty_paths())))


def without_style_sentence(prompt: str) -> str:
    return " ".join(re.sub(r"Rendered in the drawing style of the \w+ image\.", "", prompt).split())


def is_anchor(path) -> bool:
    return str(path) in {str(item) for item in boards()}


def check(image, references=(), *, anchors=None) -> dict:
    """The image against its references and every approved board.

    {"flagged", "matches", "edge", "of"} for the closest board, and "reference" for the closest other
    reference (reported only)."""
    anchors = [str(path) for path in (boards() if anchors is None else anchors)]
    others = [str(path) for path in references if str(path) not in anchors]
    result = {"flagged": False, "matches": 0, "edge": 0.0, "of": ""}
    for path in anchors:
        if Path(path).is_file():
            score = compare(image, path)
            if (is_copy(score), score["matches"]) > (result["flagged"], result["matches"]):
                result.update(flagged=is_copy(score), matches=score["matches"], edge=score["edge"], of=Path(path).name)
    for path in others:
        if Path(path).is_file():
            score = compare(image, path)
            if score["matches"] > result.get("reference", {}).get("matches", -1):
                result["reference"] = {**score, "of": Path(path).name}
    return result
