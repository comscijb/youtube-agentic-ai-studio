"""Cheap, conservative checks on generated illustrations.

Two failure modes seen in the first style calibration:

* **Paper margins** — torn or light paper edges around the picture, which in a
  16:9 video become a flickering ragged white frame. An edge counts as a margin
  when its 3 % strip is much lighter than the band just inside it. The image is
  "bordered" when at least 3 edges are margins, or when one edge is a light
  strip along (almost) its full length. A bright window touching one edge is
  not a border.
* **Comic pages** — several panels separated by light gutters. A gutter is a
  run of light pixels crossing most of the picture, at least 3 lines thick, or
  two shorter gutters crossing each other.

``crop_border`` cuts inward past the margins to the largest 16:9 area.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

ANALYSIS_SIZE = (480, 270)
STRIP = 0.03            # edge strip, share of the shorter side
MARGIN_DELTA = 40       # strip mean minus inner-band mean (0-255)
MARGIN_MIN_MEAN = 120
FULL_EDGE_COVERAGE = 0.95
FULL_EDGE_MIN_MEAN = 190
LIGHT = 185             # "paper white" threshold for gutters
GUTTER_RUN = 0.75       # a gutter crosses at least this share of the picture
CROSS_RUN_COL, CROSS_RUN_ROW = 0.5, 0.35
INTERIOR_MARGIN = 0.08


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L").resize(ANALYSIS_SIZE), dtype=np.float32)


def _edges(gray: np.ndarray) -> dict:
    h, w = gray.shape
    s = max(3, int(STRIP * min(h, w)))
    return {
        "top": (gray[:s], gray[s:3 * s], 0),
        "bottom": (gray[-s:], gray[-3 * s:-s], 0),
        "left": (gray[:, :s], gray[:, s:3 * s], 1),
        "right": (gray[:, -s:], gray[:, -3 * s:-s], 1),
    }


def detect_border(image: Image.Image) -> dict:
    gray = _gray(image)
    details, margins, full = {}, [], []
    for name, (strip, inner, axis) in _edges(gray).items():
        strip_mean, inner_mean = float(strip.mean()), float(inner.mean())
        along = (strip > 170).mean(axis=1 - axis if axis else 0)    # per position along the edge
        coverage = float((along > 0.5).mean())
        is_margin = strip_mean - inner_mean >= MARGIN_DELTA and strip_mean >= MARGIN_MIN_MEAN
        is_full = coverage >= FULL_EDGE_COVERAGE and strip_mean >= FULL_EDGE_MIN_MEAN
        details[name] = {"strip_mean": round(strip_mean, 1), "inner_mean": round(inner_mean, 1),
                         "coverage": round(coverage, 2), "margin": is_margin, "full_strip": is_full}
        if is_margin:
            margins.append(name)
        if is_full:
            full.append(name)
    return {"bordered": len(margins) >= 3 or bool(full), "margin_edges": margins,
            "full_edges": full, "edges": details}


def _longest_runs(mask: np.ndarray) -> np.ndarray:
    """Longest run of True per row of a 2-D mask."""
    best = np.zeros(mask.shape[0], dtype=np.int32)
    current = np.zeros(mask.shape[0], dtype=np.int32)
    for column in range(mask.shape[1]):
        current = np.where(mask[:, column], current + 1, 0)
        best = np.maximum(best, current)
    return best


def _thick(flags: np.ndarray, thickness: int = 3) -> bool:
    run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        if run >= thickness:
            return True
    return False


def detect_panels(image: Image.Image) -> dict:
    gray = _gray(image)
    h, w = gray.shape
    light = gray > LIGHT
    mh, mw = int(h * INTERIOR_MARGIN), int(w * INTERIOR_MARGIN)
    rows = _longest_runs(light[mh:h - mh, :]) / w
    cols = _longest_runs(light[:, mw:w - mw].T) / h
    long_gutter = _thick(rows >= GUTTER_RUN) or _thick(cols >= GUTTER_RUN)
    crossing = _thick(cols >= CROSS_RUN_COL) and _thick(rows >= CROSS_RUN_ROW)
    return {"panels": bool(long_gutter or crossing), "max_row_run": round(float(rows.max()), 2),
            "max_col_run": round(float(cols.max()), 2)}


def _margin_depth(gray: np.ndarray, side: str, limit: float = 0.18) -> int:
    """Lines to cut from one side until the picture's interior starts."""
    h, w = gray.shape
    lines = {"top": gray, "bottom": gray[::-1], "left": gray.T, "right": gray.T[::-1]}[side]
    span = int(limit * lines.shape[0])
    interior = float(lines[span:2 * span].mean())
    for depth in range(span):
        if lines[depth].mean() < interior + MARGIN_DELTA / 2:
            return depth
    return span


def crop_border(image: Image.Image, ratio: float = 16 / 9, safety: float = 0.01) -> tuple:
    """Largest ``ratio`` area inside the margins; returns (cropped image, box)."""
    report = detect_border(image)
    gray = _gray(image)
    sx, sy = image.width / ANALYSIS_SIZE[0], image.height / ANALYSIS_SIZE[1]
    edges = set(report["margin_edges"]) | set(report["full_edges"])
    depth = {side: (_margin_depth(gray, side) if side in edges else 0) for side in
             ("top", "bottom", "left", "right")}
    left = depth["left"] * sx + safety * image.width
    right = image.width - depth["right"] * sx - safety * image.width
    top = depth["top"] * sy + safety * image.height
    bottom = image.height - depth["bottom"] * sy - safety * image.height
    width, height = right - left, bottom - top
    if width / height > ratio:
        excess = width - height * ratio
        left, right = left + excess / 2, right - excess / 2
    else:
        excess = height - width / ratio
        top, bottom = top + excess / 2, bottom - excess / 2
    box = tuple(int(round(value)) for value in (left, top, right, bottom))
    return image.crop(box), box


def inspect(image: Image.Image) -> dict:
    border = detect_border(image)
    panels = detect_panels(image)
    return {"bordered": border["bordered"], "panels": panels["panels"],
            "margin_edges": border["margin_edges"] + [e for e in border["full_edges"]
                                                      if e not in border["margin_edges"]],
            "max_row_run": panels["max_row_run"], "max_col_run": panels["max_col_run"]}
