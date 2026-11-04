"""Thin paper margins around generated illustrations (Phase 3.9.3-E), deterministic, on the CPU.

The older ``video.image_checks`` catches wide torn edges. This one catches what it misses: an ivory
margin of 8-16 px at 1920 wide, uneven, sometimes with a drawn rule line. A side has a margin when its
outer lines are much brighter than the picture just inside them and almost entirely light along their
whole length (a bright window or a shaft of fog touching an edge does not fill it). A frame line is a
thin straight light line parallel to the edge, within the outer 4 %.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

ANALYSIS_WIDTH = 960
SIDES = ("top", "bottom", "left", "right")
LIGHT = 200                # "paper" brightness
OVER_INTERIOR = 60         # mean of a margin line over the mean of the band just inside
MIN_LIGHT_SHARE = 0.55     # share of the line above LIGHT (paper texture and rule lines take the rest)
MAX_DEPTH = 0.08           # a run is looked for in the outer 8 % of a side
INNER_BAND = (0.08, 0.16)
LINE_ZONE = 0.04           # a frame line lies within the outer 4 %
LINE_OVER_NEIGHBOURS = 35
LINE_MIN_LIGHT_SHARE = 0.75
CROP_LIMIT = 0.05          # up to 5 % per side is cropped; deeper is regenerated
CROP_SAFETY = 2            # analysis pixels past the last margin line
LABEL = "moldura"


def _lines(gray: np.ndarray, side: str) -> np.ndarray:
    """Rows from the outer edge inward, for any side."""
    return {"top": gray, "bottom": gray[::-1], "left": gray.T, "right": gray.T[::-1]}[side]


def _side(gray: np.ndarray, side: str) -> dict:
    lines = _lines(gray, side)
    size = lines.shape[0]
    inner = float(lines[int(size * INNER_BAND[0]):int(size * INNER_BAND[1])].mean())
    means = lines.mean(axis=1)
    light = (lines > LIGHT).mean(axis=1)
    limit = int(size * MAX_DEPTH)
    depth = misses = 0
    for index in range(limit):
        if means[index] > inner + OVER_INTERIOR and light[index] >= MIN_LIGHT_SHARE:
            depth, misses = index + 1, 0
        else:
            misses += 1
            if misses > 1 and depth or misses > 2:    # a dark line or two in the paper, then the picture
                break
    frame_line = None
    zone = int(size * LINE_ZONE)
    for index in range(depth, zone):
        before, after = means[max(0, index - 3)], means[min(size - 1, index + 3)]
        if (means[index] > before + LINE_OVER_NEIGHBOURS and means[index] > after + LINE_OVER_NEIGHBOURS
                and light[index] >= LINE_MIN_LIGHT_SHARE):
            frame_line = index
            break
    return {"depth": depth, "share": round(depth / size, 4), "inner_mean": round(inner, 1), "frame_line": frame_line}


def detect(image: Image.Image) -> dict:
    """{"margin": bool, "sides": {side: {...}}, "max_share": deepest side as a share of its axis}.
    Paper margins come from the outline of a print, so at least two sides must show it (or one frame line
    plus another side)."""
    width = ANALYSIS_WIDTH
    height = max(1, round(image.height * width / image.width))
    gray = np.asarray(image.convert("L").resize((width, height), Image.BILINEAR), dtype=np.float32)
    sides = {side: _side(gray, side) for side in SIDES}
    marked = [side for side, item in sides.items() if item["depth"] or item["frame_line"] is not None]
    return {"margin": len(marked) >= 2, "marked": marked, "sides": sides,
            "max_share": max(item["share"] for item in sides.values()), "analysis": [width, height]}


def crop_box(image: Image.Image, report: dict, ratio: float | None = None) -> tuple[int, int, int, int]:
    """Inner content box in image pixels, centred back to ``ratio`` (the image's own by default)."""
    width, height = image.size
    scale = width / report["analysis"][0]

    def cut(side):
        item = report["sides"][side]
        lines = max(item["depth"], (item["frame_line"] + 1) if item["frame_line"] is not None else 0)
        return (lines + CROP_SAFETY) * scale if lines else 0
    left, right = cut("left"), width - cut("right")
    top, bottom = cut("top"), height - cut("bottom")
    ratio = ratio or width / height
    inner_w, inner_h = right - left, bottom - top
    if inner_w / inner_h > ratio:
        excess = inner_w - inner_h * ratio
        left, right = left + excess / 2, right - excess / 2
    else:
        excess = inner_h - inner_w / ratio
        top, bottom = top + excess / 2, bottom - excess / 2
    return tuple(int(round(value)) for value in (left, top, right, bottom))


def crop(image: Image.Image, report: dict | None = None) -> tuple[Image.Image, dict]:
    """The picture without its margin, at the original size and ratio; and the pixels removed."""
    report = report or detect(image)
    box = crop_box(image, report)
    cleaned = image.crop(box).resize(image.size, Image.LANCZOS)
    return cleaned, {"box": list(box), "left": box[0], "top": box[1], "right": image.width - box[2],
                     "bottom": image.height - box[3]}


def verdict(report: dict) -> str:
    """"clean", "crop" (at most 5 % per side) or "regenerate" (deeper)."""
    if not report["margin"]:
        return "clean"
    return "crop" if report["max_share"] <= CROP_LIMIT else "regenerate"
