"""Ambient animated elements (Phase 3.9-B.2): cheap, looping, palette-graded overlays.

A shot may carry one ``ambient`` element, only when the scene's own words support it (``supported``):

* ``candle`` — a warm radial light pool with gentle, irregular intensity (a lamp or candle);
* ``dust`` — motes drifting slowly through a beam of light;
* ``rain`` — thin streaks running down a window;
* ``fog`` — haze drifting across the frame;
* ``steam`` — a slow wisp rising (a kettle, a mug of tea).

Each loop is generated procedurally once at low resolution (``LOOP_SIZE``, ``LOOP_FRAMES`` at
``LOOP_FPS``), cached in ``assets/ambient/``, and blended per frame by screen (light) — the cost is a
resize and an add. Colours stay in the brand palette (Antique Gold light, Warm Ivory haze). The
photosensitivity guard: the overlay's mean-luminance change between frames stays well under the
film look's 10 % limit (``max_step``), and the candle's flicker under ±3 %.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

import config

KINDS = ("candle", "dust", "rain", "fog", "steam")
SUPPORT = {
    "candle": r"\b(?:candle\w*|lamp\w*|lantern|flame|fire(?:light|place)?|glow(?:ing|ed)?|bulb)\b",
    "dust": r"\b(?:dust\w*|sunbeam|beam of light|shaft of light|motes?)\b",
    "rain": r"\b(?:rain\w*|drizzle|downpour|wet window|storm)\b",
    "fog": r"\b(?:fog\w*|mist\w*|haze|hazy)\b",
    "steam": r"\b(?:steam\w*|kettle|tea|coffee|mug|cup of)\b",
}
LOOP_SIZE = (480, 270)
LOOP_FRAMES = 96
LOOP_FPS = 24
MAX_FLICKER = 0.03
VERSION = 1


def enabled() -> bool:
    return bool(getattr(config, "AMBIENT_ENABLED", True))


def supported(kind: str, text: str) -> bool:
    """The element is in the whitelist and the narration mentions what produces it."""
    return kind in KINDS and bool(re.search(SUPPORT[kind], str(text or ""), re.I))


def validate(kind, text: str) -> str:
    """The shot's ambient value, or "" when it is unknown or the text does not support it."""
    kind = str(kind or "").strip().lower()
    return kind if kind and supported(kind, text) else ""


def _palette(key: str) -> np.ndarray:
    return np.array(config.COLORS.get(key, (255, 255, 255)), np.float32) / 255.0


def _grain(rng, shape):
    import cv2
    return cv2.GaussianBlur(rng.random(shape).astype(np.float32), (0, 0), 6)


def generate(kind: str, size=LOOP_SIZE, frames=LOOP_FRAMES, seed: int = 7) -> np.ndarray:
    """frames × h × w × 3 float32 light to add (screen), looping seamlessly."""
    width, height = size
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    out = np.zeros((frames, height, width, 3), np.float32)
    gold, ivory = _palette("highlight"), _palette("white")
    for index in range(frames):
        phase = 2 * math.pi * index / frames                        # loops exactly
        if kind == "candle":
            intensity = 0.16 * (1 + MAX_FLICKER * 0.6 * math.sin(phase * 3) + MAX_FLICKER * 0.4 * math.sin(phase * 7 + 1))
            radius = np.hypot((xs - width * 0.5) / (width * 0.35), (ys - height * 0.6) / (height * 0.5))
            light = intensity * np.clip(1 - radius, 0, 1) ** 2
            out[index] = light[..., None] * gold
        elif kind == "dust":
            if index == 0:
                motes = rng.random((60, 3)) * [width, height, 1]
            beam = np.clip(1 - np.abs((xs - ys * 0.6) - width * 0.35) / (width * 0.18), 0, 1)
            layer = np.zeros((height, width), np.float32)
            for x, y, speed in motes:
                px = int((x + 6 * math.sin(phase + speed * 6)) % width)
                py = int((y - height * index / frames * (0.3 + speed * 0.4)) % height)
                layer[max(0, py - 1):py + 1, max(0, px - 1):px + 1] = 0.5 + 0.5 * speed
            out[index] = (layer * beam * 0.5 + beam * 0.04)[..., None] * ivory
        elif kind == "rain":
            if index == 0:
                drops = rng.random((140, 3)) * [width, height, 1]
            layer = np.zeros((height, width), np.float32)
            for x, y, speed in drops:
                py = int((y + height * (index / frames) * (2 + speed * 2)) % height)
                layer[max(0, py - 6):py, int(x)] = 0.25 + 0.2 * speed
            out[index] = layer[..., None] * ivory
        elif kind == "fog":
            if index == 0:
                base = _grain(rng, (height, width * 2))
            shift = int(width * index / frames)
            haze = base[:, shift:shift + width]
            out[index] = (0.10 * haze * np.clip(ys / height, 0.3, 1))[..., None] * ivory
        elif kind == "steam":
            if index == 0:
                base = _grain(rng, (height * 2, width))
            shift = int(height * index / frames)
            column = np.exp(-((xs - width * 0.5 - 12 * np.sin(ys / 30 + phase)) / (width * 0.06)) ** 2)
            rising = base[height * 2 - height - shift:height * 2 - shift] if shift else base[height:]
            out[index] = (0.22 * column * rising * np.clip(1 - ys / height, 0, 1) ** 0.5)[..., None] * ivory
    return out


_LOOPS: dict = {}


def loop(kind: str) -> np.ndarray:
    """The cached loop for ``kind`` (assets/ambient/<kind>_v1.npz, then memory), generated on first use."""
    if kind in _LOOPS:
        return _LOOPS[kind]
    folder = Path(getattr(config, "AMBIENT_ASSETS_DIR", None)
                  or Path(__file__).resolve().parent.parent / "assets" / "ambient")
    path = folder / f"{kind}_v{VERSION}.npz"
    if path.is_file():
        _LOOPS[kind] = np.load(path)["frames"].astype(np.float32)
        _LOOPS[kind + ":u8"] = _to_uint8(_LOOPS[kind])
        return _LOOPS[kind]
    frames = generate(kind)
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, frames=frames.astype(np.float16))
    _LOOPS[kind] = frames
    _LOOPS[kind + ":u8"] = _to_uint8(frames)
    return frames


def _to_uint8(frames: np.ndarray) -> np.ndarray:
    return np.clip(frames * 255, 0, 255).astype(np.uint8)


def loop_uint8(kind: str) -> np.ndarray:
    loop(kind)
    return _LOOPS[kind + ":u8"]


def max_step(frames: np.ndarray) -> float:
    """Worst mean-luminance change the overlay adds between consecutive frames (0-1)."""
    means = frames.mean(axis=(1, 2, 3))
    return float(np.abs(np.diff(np.append(means, means[0]))).max())


def apply(frame: np.ndarray, frames: np.ndarray, t: float) -> np.ndarray:
    """Screen the overlay at time ``t`` (s) onto ``frame`` (uint8 RGB): a + b − a·b/255, in uint8
    (measured: 56 → ~6 ms per 1080p frame against float math)."""
    import cv2
    light = frames[int(t * LOOP_FPS) % len(frames)]
    if light.dtype != np.uint8:
        light = _to_uint8(light)
    light = cv2.resize(light, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.add(frame, cv2.subtract(light, cv2.multiply(frame, light, scale=1 / 255)))
