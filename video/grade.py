"""Light and colour follow the beats (Phase 4.2-C).

A per-beat grade on top of the film look (``BEAT_GRADES``): calm keeps the palette; tension adds contrast, deeper
blacks and cooler midtones; the reversal briefly desaturates and keeps a single crimson or gold accent; the ending is
warmer and softer; the cold open keeps its own darker, slower treatment (3.9.7). Grades ease in over half a second,
and the transitions are: hard cuts inside tension, a soft crossfade inside calm scenes, a 0.4 s dip to black on time
jumps and on the return after the cold open. The photosensitivity guard holds: no flash, a mean luminance step of at
most 10 % between frames (``max_step``).
"""
from __future__ import annotations

import numpy as np

import config

DEFAULT_GRADES = {
    "calm": {},
    "tension": {"contrast": 1.12, "black": 0.05, "cool": 0.05},
    "reversal": {"saturation": 0.35, "accent": "crimson", "settle_seconds": 2.5, "settled_saturation": 0.75},
    "ending": {"warm": 0.06, "soft": 0.92},
    "cold_open": {},                                     # creator.cold_open_treatment darkens and slows it already
}
BEATS = {"cold_open": "cold_open", "reversal": "reversal", "climax": "tension", "resolution": "ending",
         "aftermath": "ending"}
RAMP_SECONDS = 0.5
DIP_SECONDS = 0.4
MAX_LUMA_STEP = 0.10


def enabled() -> bool:
    return bool(getattr(config, "BEAT_GRADES_ENABLED", True))


def grades() -> dict:
    return {**DEFAULT_GRADES, **(getattr(config, "BEAT_GRADES", None) or {})}


def beat_for(section_type: str) -> str:
    return BEATS.get(str(section_type or ""), "calm")


def _hue_mask(rgb: np.ndarray, accent: str) -> np.ndarray:
    """Pixels of the accent colour (crimson: red dominant; gold: red and green over blue), saturated enough."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    if accent == "gold":
        mask = (r > 0.35) & (g > 0.25) & (r - b > 0.18) & (g - b > 0.10)
    else:
        mask = (r > 0.30) & (r - g > 0.15) & (r - b > 0.12)
    return mask.astype(np.float32)


def apply(frame: np.ndarray, grade: dict, strength: float = 1.0, local: float | None = None) -> np.ndarray:
    """``frame`` (uint8 RGB) graded; ``strength`` 0..1 eases the grade in."""
    if not grade or strength <= 0:
        return frame
    rgb = frame.astype(np.float32) / 255.0
    out = rgb
    if grade.get("contrast"):
        amount = 1 + (grade["contrast"] - 1) * strength
        out = (out - 0.5) * amount + 0.5
    if grade.get("black"):
        floor = grade["black"] * strength
        out = np.clip((out - floor) / (1 - floor), 0, 1)
    if grade.get("cool"):
        shift = grade["cool"] * strength
        mid = 4 * out * (1 - out)                        # strongest in the midtones
        out = out + np.stack([-shift * mid[..., 0], np.zeros_like(mid[..., 1]), shift * mid[..., 2]], axis=-1)
    if grade.get("warm"):
        shift = grade["warm"] * strength
        out = out + np.array([shift, shift * 0.45, -shift * 0.6], dtype=np.float32)
    if grade.get("soft"):
        out = 0.5 + (out - 0.5) * (1 - (1 - grade["soft"]) * strength)
    if "saturation" in grade:
        target = grade["saturation"]
        if local is not None and grade.get("settle_seconds"):
            progress = min(1.0, max(0.0, local / grade["settle_seconds"]))
            target = grade["saturation"] + (grade.get("settled_saturation", 1.0) - grade["saturation"]) * progress
        level = 1 - (1 - target) * strength
        gray = out @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        desaturated = gray[..., None] + (out - gray[..., None]) * level
        if grade.get("accent"):
            keep = _hue_mask(rgb, grade["accent"])[..., None]
            desaturated = desaturated * (1 - keep) + out * keep
        out = desaturated
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def ramp(local: float) -> float:
    """The grade's strength ``local`` seconds into its section (eases in over RAMP_SECONDS)."""
    return min(1.0, max(0.0, local / RAMP_SECONDS)) if RAMP_SECONDS > 0 else 1.0


def dip(local: float, duration: float, *, dip_in: bool, dip_out: bool) -> float:
    """Brightness factor for a 0.4 s dip to black (half out of the previous view, half into the next)."""
    half = DIP_SECONDS / 2
    factor = 1.0
    if dip_in:
        factor = min(factor, local / half if local < half else 1.0)
    if dip_out:
        remaining = duration - local
        factor = min(factor, remaining / half if remaining < half else 1.0)
    return max(0.0, factor)


def luminance(frame: np.ndarray) -> float:
    return float((frame[::4, ::4].astype(np.float32) @ np.array([0.299, 0.587, 0.114])).mean() / 255.0)


def max_step(frames: list[np.ndarray]) -> float:
    """The largest mean-luminance change between consecutive frames (the photosensitivity guard)."""
    values = [luminance(frame) for frame in frames]
    return max((abs(b - a) for a, b in zip(values, values[1:])), default=0.0)


def transition(section_type: str, opens_time_jump: bool, after_cold_open: bool) -> str:
    """At a section boundary: "dip" after the cold open or on a time jump, else "cut"."""
    if after_cold_open or opens_time_jump:
        return "dip"
    return "cut"
