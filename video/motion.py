"""Motion beyond the slideshow: 2.5D parallax from a depth map (Phase 3.9-B.1).

Per shot, after its image is final, a depth map is estimated once and cached in
``images/shots/depth/<shot>.png`` (redone when the image is newer: an edit or a new seed). The image
is split into 2-3 depth layers; the holes a near layer leaves in the layer behind it are filled with
a cheap OpenCV inpaint (never the image model). Each frame moves the layers with a slow dolly/truck:
near layers move more than far ones, at most ``PARALLAX_MAX`` of the frame width over the shot, with
a small overscan so no edge is ever revealed.

Shot type → motion:

=================  ================================================
wide / establishing  parallax dolly (full amplitude)
medium               gentle parallax (60 %)
close-up             very slow push-in (Ken Burns, smaller)
insert / object      slow push or static with micro-drift
=================  ================================================

Falls back to Ken Burns when the depth map is flat or noisy, when thin structures sit on a layer
edge (they would tear), or when a face crosses a layer boundary. ``MOTION_STYLE = "kenburns"``
switches it all off. Depth comes from Depth Anything V2 Small (Apache-2.0) through ``transformers``
when installed; without it every shot keeps Ken Burns.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

import config

PARALLAX_MAX = 0.025          # ≤ 2.5 % of the frame width over a whole shot
OVERSCAN = 1.04               # layers are drawn 4 % larger so a shift never shows an edge
FLAT_STD = 0.07               # depth spread below this: nothing to separate
NOISY = 0.12                  # mean |Laplacian| of the normalised depth above this: unreliable
THIN_SHARE = 0.25             # share of a near layer an opening removes: thin structures (poles, hair)
FACE_SPLIT = 0.15             # a face with ≥ 15 % of its box on each side of a layer edge
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
_estimator = {}


class DepthUnavailable(RuntimeError):
    """No depth estimator installed (pip install transformers)."""


def style() -> str:
    value = str(getattr(config, "MOTION_STYLE", "parallax")).lower()
    return value if value in ("parallax", "kenburns") else "parallax"


def motion_for(shot_type: str) -> dict:
    """The motion family and strength a shot type gets (before the depth checks)."""
    if shot_type in ("wide", "establishing"):
        return {"kind": "parallax", "amount": 1.0}
    if shot_type in ("medium", "over_the_shoulder"):
        return {"kind": "parallax", "amount": 0.6}
    if shot_type in ("close_up",):
        return {"kind": "push", "zoom": (1.0, 1.05)}
    return {"kind": "drift", "zoom": (1.0, 1.03)}                    # insert, hands, objects


# ── Depth ─────────────────────────────────────────────────────────────────────

def depth_path(output_dir, shot_id: str) -> Path:
    from studio.filenames import safe_filename
    return Path(output_dir) / "images" / "shots" / "depth" / f"{safe_filename(shot_id)}.png"


def estimate(image_path) -> np.ndarray:
    """Normalised depth (0 far … 1 near), the image's size."""
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise DepthUnavailable("parallax needs a depth model: pip install transformers") from exc
    if "model" not in _estimator:
        _estimator["model"] = pipeline("depth-estimation", model=DEPTH_MODEL, device="cpu")
    from PIL import Image
    with Image.open(image_path) as image:
        result = _estimator["model"](image.convert("RGB"))
    depth = np.asarray(result["depth"], dtype=np.float32)
    span = float(depth.max() - depth.min()) or 1.0
    return (depth - depth.min()) / span


def depth_map(output_dir, shot_id: str, image_path) -> np.ndarray | None:
    """The cached depth of a shot (redone when the image is newer); None without an estimator."""
    from PIL import Image
    path = depth_path(output_dir, shot_id)
    image_path = Path(image_path)
    if path.is_file() and path.stat().st_mtime >= image_path.stat().st_mtime:
        with Image.open(path) as cached:
            return np.asarray(cached, dtype=np.float32) / 65535.0
    try:
        depth = estimate(image_path)
    except DepthUnavailable:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((depth * 65535).astype(np.uint16)).save(path)
    return depth


# ── Layer plan and fallbacks ─────────────────────────────────────────────────

def layers_from(depth: np.ndarray, count: int = 3) -> list[np.ndarray]:
    """Boolean masks far → near from depth quantiles."""
    edges = np.quantile(depth, np.linspace(0, 1, count + 1)[1:-1])
    index = np.digitize(depth, edges)
    return [index == layer for layer in range(count)]


def noisiness(depth: np.ndarray) -> float:
    import cv2
    small = cv2.resize(depth, (256, max(1, int(256 * depth.shape[0] / depth.shape[1]))))
    return float(np.abs(cv2.Laplacian(small, cv2.CV_32F)).mean())


def thin_share(mask: np.ndarray) -> float:
    """Share of a mask that a 7-px opening removes: thin structures that would tear when shifted."""
    import cv2
    if not mask.any():
        return 0.0
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    return float((mask & ~opened.astype(bool)).sum()) / float(mask.sum())


def face_split(masks: list[np.ndarray], faces: list) -> bool:
    """A face (normalised box) with a real share of its area in two different layers."""
    height, width = masks[0].shape
    for box in faces or []:
        x0, y0, x1, y1 = (int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))
        if x1 <= x0 or y1 <= y0:
            continue
        shares = [float(mask[y0:y1, x0:x1].mean()) for mask in masks]
        if sum(1 for share in shares if share >= FACE_SPLIT) >= 2:
            return True
    return False


def plan(depth: np.ndarray | None, shot_type: str, faces: list | None = None) -> dict:
    """{"kind": parallax | push | drift | kenburns, "reason", "masks", "amount"} for one shot."""
    wanted = motion_for(shot_type)
    if style() != "parallax":
        return {"kind": "kenburns", "reason": "MOTION_STYLE is kenburns"}
    if wanted["kind"] != "parallax":
        return {**wanted, "reason": f"{shot_type}: {wanted['kind']}"}
    if depth is None:
        return {"kind": "kenburns", "reason": "no depth map"}
    if float(depth.std()) < FLAT_STD:
        return {"kind": "kenburns", "reason": "flat depth"}
    if noisiness(depth) > NOISY:
        return {"kind": "kenburns", "reason": "noisy depth"}
    masks = layers_from(depth, 3 if wanted["amount"] >= 1 else 2)
    if any(thin_share(mask) > THIN_SHARE for mask in masks[1:]):
        return {"kind": "kenburns", "reason": "thin structures on a layer edge"}
    if face_split(masks, faces or []):
        return {"kind": "kenburns", "reason": "a face crosses a layer boundary"}
    return {"kind": "parallax", "reason": "", "masks": masks, "amount": wanted["amount"]}


# ── Rendering ─────────────────────────────────────────────────────────────────

def offsets(progress: float, layer_count: int, amount: float, width: int, direction: int = 1) -> list[float]:
    """Horizontal shift (px) of each layer, far → near, at ``progress`` 0..1 of the shot: the near layer
    travels PARALLAX_MAX·amount of the width over the shot, farther layers proportionally less."""
    total = PARALLAX_MAX * amount * width
    eased = progress * progress * (3 - 2 * progress)               # smoothstep: no jolt at the cut
    return [direction * total * (index + 1) / layer_count * (eased - 0.5) for index in range(layer_count)]


def build_layers(image: np.ndarray, masks: list[np.ndarray]) -> list[tuple[np.ndarray, np.ndarray]]:
    """[(rgb, alpha)] far → near; each layer's holes (where nearer layers sit) inpainted cheaply."""
    import cv2
    layers = []
    for index, mask in enumerate(masks):
        nearer = np.zeros_like(mask)
        for other in masks[index + 1:]:
            nearer |= other
        rgb = image
        if nearer.any():
            scale = 0.25                                          # inpaint at quarter size, then upscale
            small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            hole = cv2.resize(nearer.astype(np.uint8) * 255, (small.shape[1], small.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
            hole = cv2.dilate(hole, np.ones((3, 3), np.uint8))
            filled = cv2.inpaint(small, hole, 3, cv2.INPAINT_TELEA)
            filled = cv2.resize(filled, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
            rgb = np.where(nearer[..., None], filled, image)
        if index == 0:
            layers.append({"rgb": np.ascontiguousarray(rgb)})
            continue
        # Premultiplied once, so each frame is one warp + one multiply-add in uint8 (measured: 149 → ~40 ms
        # per 1080p frame against float compositing).
        alpha = cv2.GaussianBlur((mask | nearer).astype(np.float32), (0, 0), 2.0)   # soft edge, no halo
        premul = (rgb.astype(np.float32) * alpha[..., None]).astype(np.uint8)
        inverse = np.repeat(((1 - alpha) * 255).astype(np.uint8)[..., None], 3, axis=2)
        layers.append({"premul": premul, "inverse": inverse})
    return layers


def render(layers: list[dict], progress: float, amount: float, size: tuple[int, int],
           direction: int = 1) -> np.ndarray:
    """One frame: every layer scaled by OVERSCAN, shifted by its offset, composited far → near."""
    import cv2
    width, height = size
    shifts = offsets(progress, len(layers), amount, width, direction)
    scale = OVERSCAN + 0.01 * progress                             # a slight dolly-in as well
    frame = None
    for layer, shift in zip(layers, shifts):
        matrix = np.float32([[scale, 0, (1 - scale) * width / 2 + shift], [0, scale, (1 - scale) * height / 2]])
        if frame is None:
            frame = cv2.warpAffine(layer["rgb"], matrix, (width, height), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REFLECT)
            continue
        premul = cv2.warpAffine(layer["premul"], matrix, (width, height), flags=cv2.INTER_LINEAR)
        inverse = cv2.warpAffine(layer["inverse"], matrix, (width, height), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        frame = cv2.add(cv2.multiply(frame, inverse, scale=1 / 255), premul)
    return frame
