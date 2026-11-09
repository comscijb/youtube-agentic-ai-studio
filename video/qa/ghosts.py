"""Ghost detector for figure removal (Phase 3.9.6-B): does the repainted region still show the figure's silhouette?

The person detector cannot see a blurred outline, so a removal can pass with "something was here" in plain view. This
compares the region with the ring around it and with the old outline:

* **texture:** high-frequency energy (Laplacian) inside vs the ring. A smear on dense hatching has far less;
* **contour:** gradient along the old outline vs a band a few pixels outside it. A silhouette keeps its edge;
* **blocks:** steps lined up on an 8/16/32 px grid, from a latent or a fill made at low resolution and upscaled;
* **tone:** the region's mean and spread vs the ring's after the tone match.

Thresholds are calibrated on the 3.9.6 fixtures (six results that show a silhouette, the same masks over untouched parts of
the same shots); see ``docs/reports/3.9.6``.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

TEXTURE_MIN = 0.6
CONTOUR_MAX = 1.6
BLOCK_MAX = 1.35
TONE_MAX = 0.6
RING_SHARE = 0.04
EDGE_BAND = 2
OUTSIDE_OFFSET = 8
BLOCK_GRIDS = (8, 16, 32)


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32)


def _grow(mask: np.ndarray, pixels: int) -> np.ndarray:
    import cv2
    if pixels <= 0:
        return mask.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pixels + 1, 2 * pixels + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel) > 0


def _shrink(mask: np.ndarray, pixels: int) -> np.ndarray:
    import cv2
    if pixels <= 0:
        return mask.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pixels + 1, 2 * pixels + 1))
    return cv2.erode(mask.astype(np.uint8), kernel) > 0


def _laplacian(gray: np.ndarray) -> np.ndarray:
    import cv2
    return np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))


def _gradient(gray: np.ndarray) -> np.ndarray:
    import cv2
    smooth = cv2.GaussianBlur(gray, (0, 0), 1.5)
    return np.hypot(cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3))


def _block_score(gray: np.ndarray, inside: np.ndarray) -> float:
    """Steps on an 8/16/32 px grid inside the region (a latent or a low-resolution fill upscaled), against the steps
    between grid lines: the strongest ratio over period, phase and axis. Hatching has no reason to line up with a grid."""
    best = 0.0
    for axis in (0, 1):
        steps = np.abs(np.diff(gray, axis=1 - axis))
        valid = (inside[:, 1:] & inside[:, :-1]) if axis == 0 else (inside[1:, :] & inside[:-1, :])
        totals = np.where(valid, steps, 0).sum(axis=axis)
        counts = valid.sum(axis=axis)
        if counts.sum() < 2000:
            continue
        for period in BLOCK_GRIDS:
            phases = []
            for phase in range(period):
                on = np.zeros(len(totals), dtype=bool)
                on[phase::period] = True
                phases.append(totals[on].sum() / max(counts[on].sum(), 1))
            phases = np.array(phases)
            best = max(best, float(phases.max() / (np.median(phases) + 1e-6)))
    return best


def score(result: Image.Image, hard_mask: Image.Image, contour_mask: Image.Image | None = None) -> dict:
    """{"texture", "contour", "blocks", "tone", "ghost" (bool), "failed" ([signal names])} of ``result`` inside ``hard_mask``,
    the repainted region (same size as ``result``), against the ring of untouched pixels around it. ``contour_mask`` is the
    figure's own outline when the region was grown beyond it (default: the region's edge)."""
    gray = _gray(result)
    mask = np.asarray(hard_mask.convert("L").resize(result.size, Image.NEAREST)) > 127
    outline = mask if contour_mask is None else np.asarray(contour_mask.convert("L").resize(result.size, Image.NEAREST)) > 127
    side = min(mask.shape)
    ring_width = max(4, int(RING_SHARE * side))
    inner = _shrink(mask, 3)
    if inner.sum() < 50:
        inner = mask
    ring = _grow(mask, ring_width) & ~_grow(mask, 3)
    if ring.sum() < 50 or inner.sum() < 50:
        return {"texture": 1.0, "contour": 1.0, "blocks": 0.0, "tone": 0.0, "ghost": False, "failed": [], "skipped": True}
    lap = _laplacian(gray)
    texture = float(lap[inner].mean() / (lap[ring].mean() + 1e-6))
    grad = _gradient(gray)
    edge = _grow(outline, EDGE_BAND) & ~_shrink(outline, EDGE_BAND)
    outside = _grow(outline, OUTSIDE_OFFSET + EDGE_BAND) & ~_grow(outline, OUTSIDE_OFFSET - EDGE_BAND)
    contour = float(grad[edge].mean() / (grad[outside].mean() + 1e-6)) if edge.any() and outside.any() else 1.0
    blocks = _block_score(gray, inner)
    ring_std = float(gray[ring].std()) + 1e-6
    tone = float(max(abs(gray[inner].mean() - gray[ring].mean()) / ring_std, abs(np.log((gray[inner].std() + 1e-6) / ring_std))))
    failed = [name for name, bad in (("texture", texture < TEXTURE_MIN), ("contour", contour > CONTOUR_MAX),
                                     ("blocks", blocks > BLOCK_MAX), ("tone", tone > TONE_MAX)) if bad]
    return {"texture": round(texture, 3), "contour": round(contour, 3), "blocks": round(blocks, 2), "tone": round(tone, 3),
            "ghost": bool(failed), "failed": failed}


def blur_fill(image: Image.Image, mask: Image.Image, radius: float | None = None) -> Image.Image:
    """The surroundings, blurred into the hole: a neutral start for a repaint when LaMa would draw a shape (3.9.6-A)."""
    import cv2
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    hole = np.asarray(mask.convert("L").resize(image.size, Image.NEAREST)) > 127
    radius = radius or max(8.0, 0.05 * min(image.size))
    weight = (~hole).astype(np.float32)
    blurred = cv2.GaussianBlur(pixels * weight[..., None], (0, 0), radius)
    norm = cv2.GaussianBlur(weight, (0, 0), radius)[..., None]
    filled = np.where(norm > 1e-3, blurred / np.maximum(norm, 1e-3), pixels.mean(axis=(0, 1)))
    out = np.where(hole[..., None], filled, pixels)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
