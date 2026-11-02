"""Continuous film look, applied by ffmpeg in the final encode pass (Phase 3.8-D, replaces the 3.6-F tape glitch).

``VIDEO_FILM_EFFECT = "off" | "grain" | "super8" | "super8_strong"`` (default ``super8``; the old
``VIDEO_TAPE_EFFECT`` is still read: ``subtle`` → ``super8``, ``medium`` → ``super8_strong``).

The look is the same on every frame: nothing is tied to scene cuts, so cuts stay clean (the
crossfade or hard cut is untouched) and grain and dust keep running across them.

* ``grain`` — cinema grain and a soft vignette.
* ``super8`` — animated grain slightly coarser than pixel noise (ffmpeg ``noise`` on a neutral layer
  at half resolution, upscaled and added with ``grainmerge``, so it shows in the shadows too); gentle, irregular exposure flicker
  (a sum of three sines at incommensurate frequencies, ±2.5 %); a slight gate weave (1 px, slow);
  sparse dust specks (1–2 frames, about one a second) and a thin vertical scratch (≤ 0.5 s, once
  per 10 s loop); lifted blacks, a slight warm tint in the brand's ivory/gold range, a soft
  vignette. No scanlines, no chroma shift.
* ``super8_strong`` — the same, a little stronger.

The vignette, dust and scratches come from a short lossless half-resolution layer rendered once from
a fixed seed and cached in ``assets/film/`` (``dust_layer``); it loops with a per-video start offset,
so the cost stays constant with video length. Measured on the render's encode path (moviepy frames
piped to ffmpeg): grain +10 %, super8 +13 %, super8_strong +15 % encode time. The captions are burnt after the effect (``subtitles``) and stay crisp.
The same graph serves the episode (1920×1080) and the Shorts (1080×1920). Thumbnails never get it.

**Photosensitivity** (``check_safety``): the mean-luminance change between consecutive frames
stays ≤ 10 % (flicker slope plus the brightest dust event) and there are no flashes.
"""
from __future__ import annotations

import hashlib
import math
import re
import subprocess
from pathlib import Path

import config

LOOKS = {
    "grain": {"grain": 14, "flicker": (), "weave": 0, "dust": None, "vignette": 34,
              "lift": 0.0, "warm": 0.0},
    "super8": {"grain": 20, "flicker": ((0.012, 2.3), (0.008, 3.7), (0.005, 5.9)), "weave": 1,
               "weave_hz": (0.45, 0.7), "dust": {"specks_per_second": 0.9, "scratch_seconds": 0.4},
               "vignette": 40, "lift": 0.035, "warm": 0.03},
    "super8_strong": {"grain": 28, "flicker": ((0.014, 2.3), (0.010, 3.7), (0.006, 5.9)), "weave": 2,
                      "weave_hz": (0.5, 0.8), "dust": {"specks_per_second": 1.3, "scratch_seconds": 0.5},
                      "vignette": 48, "lift": 0.05, "warm": 0.04},
}
ALIASES = {"subtle": "super8", "medium": "super8_strong", "on": "super8", "film": "super8"}
DUST_VERSION = 1
DUST_SECONDS = 10
DUST_SEED = 8_388_608
MAX_LUMA_STEP = 0.10              # mean luminance change allowed between consecutive frames
SPECK_RADIUS = (1, 3)             # px at the layer's half resolution
_warned: set = set()


class TapeSafetyError(ValueError):
    """The effect would break the photosensitivity rules."""


def look(value: str | None = None) -> str:
    """The chosen look: VIDEO_FILM_EFFECT (or the 3.6 VIDEO_TAPE_EFFECT) with old values mapped."""
    raw = value if value is not None else getattr(config, "VIDEO_FILM_EFFECT", None) \
        or getattr(config, "VIDEO_TAPE_EFFECT", None) or "super8"
    chosen = str(raw or "off").strip().lower()
    if chosen in ALIASES:
        mapped = ALIASES[chosen]
        if chosen not in _warned:
            _warned.add(chosen)
            print(f"   ⚠ film effect {chosen!r} is from the tape effect (3.6); using {mapped!r}")
        return mapped
    return chosen if chosen in LOOKS else "off"


intensity = look                  # 3.6 name


# ── Photosensitivity ─────────────────────────────────────────────────────────

def flicker_expression(spec: dict) -> str:
    """Brightness offset over time: a smooth irregular curve (sum of sines), never square steps."""
    terms = [f"{amp}*sin(2*PI*{hz}*t+{phase})" for (amp, hz), phase in zip(spec["flicker"], (0, 1.3, 2.1, 3.4))]
    return "+".join(terms) or "0"


def flicker_amplitude(spec: dict) -> float:
    return sum(amp for amp, _ in spec["flicker"])


def max_luma_step(spec: dict, fps: float) -> float:
    """Worst mean-luminance change between two consecutive frames (0-1 of full scale): the flicker's
    steepest slope plus the brightest dust event (a scratch appearing over its whole height)."""
    flicker = sum(amp * 2 * math.pi * hz / fps for amp, hz in spec["flicker"])
    dust = spec.get("dust")
    scratch = (2 / 960) * 0.35 if dust else 0.0        # a 2-px line at 35 % contrast on the half-res layer
    return flicker + scratch


def check_safety(spec: dict, fps: float) -> dict:
    """Raise TapeSafetyError when a rule is broken; return the measured values."""
    step = max_luma_step(spec, fps)
    report = {"max_luma_step": round(step, 4), "flicker_amplitude": round(flicker_amplitude(spec), 4)}
    if step > MAX_LUMA_STEP:
        raise TapeSafetyError(f"luminance changes {step:.0%} between frames (max {MAX_LUMA_STEP:.0%})")
    return report


# ── The dust / scratch layer (rendered once, cached) ─────────────────────────

def film_dir() -> Path:
    return Path(getattr(config, "FILM_ASSETS_DIR", Path(__file__).resolve().parent.parent / "assets" / "film"))


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def vignette_offsets(size: tuple[int, int], strength: float):
    """A static additive darkening (≤ 0) towards the corners: the vignette, baked into the film layer so
    it costs nothing per frame (measured: ffmpeg's vignette filter alone added ~75 % to a 1080p encode)."""
    import numpy as np
    width, height = size
    ys, xs = np.mgrid[0:height, 0:width]
    radius = np.sqrt(((xs - width / 2) / (width / 2)) ** 2 + ((ys - height / 2) / (height / 2)) ** 2) / math.sqrt(2)
    ramp = np.clip((radius - 0.35) / 0.65, 0, 1)
    return -(strength * ramp * ramp * (3 - 2 * ramp))          # smoothstep, 0 in the centre


def dust_frames(size: tuple[int, int], fps: float, spec: dict | None, seed: int = DUST_SEED, vignette: float = 0.0):
    """Yield yuv420p frames (bytes) of the film layer: neutral grey (Y = 128, added with ``grainmerge``)
    with the static vignette and, when ``spec`` has dust, sparse light and dark specks and one thin
    vertical scratch from a fixed seed. Without dust a single frame is enough (it loops)."""
    import random
    import numpy as np
    width, height = size
    base = 128 + (vignette_offsets(size, vignette) if vignette else np.zeros((height, width)))
    chroma = bytes([128]) * (2 * (width // 2) * (height // 2))
    if not spec:
        yield np.clip(np.round(base), 0, 255).astype(np.uint8).tobytes() + chroma
        return
    rng = random.Random(seed)
    total = int(DUST_SECONDS * fps)
    specks = {}
    for _ in range(round(spec["specks_per_second"] * DUST_SECONDS)):
        start = rng.randrange(total)
        speck = (rng.randrange(width), rng.randrange(height), rng.randint(*SPECK_RADIUS),
                 rng.choice((-1, 1)) * rng.randint(40, 90))
        for frame in range(start, min(total, start + rng.choice((1, 2)))):
            specks.setdefault(frame, []).append(speck)
    scratch_start = rng.randrange(0, total - int(spec["scratch_seconds"] * fps))
    scratch_frames = range(scratch_start, scratch_start + int(spec["scratch_seconds"] * fps))
    scratch_x = rng.randrange(width // 5, 4 * width // 5)
    ys, xs = np.mgrid[0:height, 0:width]
    for frame in range(total):
        luma = base.copy()
        for x, y, radius, delta in specks.get(frame, []):
            luma[(xs - x) ** 2 + (ys - y) ** 2 <= radius * radius] += delta
        if frame in scratch_frames:
            drift = int(round(2 * math.sin(frame / 3)))
            column = min(width - 2, max(0, scratch_x + drift))
            luma[:, column:column + 1] += 70
        yield np.clip(np.round(luma), 0, 255).astype(np.uint8).tobytes() + chroma


def dust_layer(look_name: str, size: tuple[int, int], fps: float) -> Path:
    """The cached lossless film layer (half resolution) for this look and frame size: the vignette and,
    for the Super 8 looks, a 10-s loop of dust and scratches. Rendered once, then reused."""
    spec = LOOKS[look_name]
    half = (size[0] // 2 // 2 * 2, size[1] // 2 // 2 * 2)
    path = film_dir() / f"film_{look_name}_{half[0]}x{half[1]}_{int(fps)}fps_v{DUST_VERSION}.mkv"
    if path.is_file() and path.stat().st_size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial.mkv")
    process = subprocess.Popen(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
         "-s", f"{half[0]}x{half[1]}", "-r", str(fps), "-i", "-", "-c:v", "ffv1", "-level", "3", str(partial)],
        stdin=subprocess.PIPE)
    try:
        for frame in dust_frames(half, fps, spec.get("dust"), vignette=spec["vignette"]):
            process.stdin.write(frame)
    finally:
        process.stdin.close()
        process.wait()
    if process.returncode:
        raise RuntimeError(f"could not render the film layer ({process.returncode})")
    partial.replace(path)
    return path


def start_offset(key: str) -> float:
    """A per-video start in the dust loop (seeded by the output path), so videos don't share dust."""
    digest = int(hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:8], 16)
    return round(digest % (DUST_SECONDS * 100) / 100, 2)


# ── The filter graph ─────────────────────────────────────────────────────────

def escape_path(path) -> str:
    """A path for a filter option (Windows drive colons and quotes escaped), single-quoted."""
    text = str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"'{text}'"


def filter_graph(look_name: str, size: tuple[int, int], fps: float, subtitles: str | None = None,
                 fonts_dir: str | None = None, dust: str | Path | None = None, offset: float = 0.0) -> str:
    """The -vf graph: the film look on every frame, then the captions. ``dust`` is ``dust_layer``'s file
    (vignette, dust, scratches); without it the look has grain only.

    Cost-conscious (measured on 1080p): lifted blacks and the warm tint are one ``lutyuv`` (curves +
    colorbalance cost 2× more), the vignette is baked into the layer, and the grain is generated at
    half resolution and added to the layer before one full-size ``grainmerge``."""
    spec = LOOKS[look_name]
    check_safety(spec, fps)
    width, height = size
    half = (width // 2 // 2 * 2, height // 2 // 2 * 2)
    parts = []
    weave = spec.get("weave") or 0
    if weave:
        hx, hy = spec["weave_hz"]
        parts += [f"crop=w=iw-{2 * weave}:h=ih-{2 * weave}:x='{weave}+{weave}*sin(2*PI*t*{hx})'"
                  f":y='{weave}+{weave}*sin(2*PI*t*{hy}+0.9)'",
                  f"pad={width}:{height}:{weave}:{weave}"]
    if spec["flicker"]:
        parts.append(f"eq=brightness='{flicker_expression(spec)}':eval=frame")
    parts.append("format=yuv420p")
    if spec["lift"] or spec["warm"]:                  # lifted blacks + warm tint (ivory/gold), one LUT
        shift = round(spec["warm"] * 100)
        parts.append(f"lutyuv=y='val+{spec['lift']}*(235-val)':u='val-{shift}':v='val+{shift}'")
    graph = ",".join(parts) + "[base];"
    graph += (f"color=c=black:s={half[0]}x{half[1]}:r={fps},format=yuv420p,lutyuv=y=128:u=128:v=128,"
              f"noise=c0s={spec['grain']}:c0f=t+u")
    if dust:
        graph += (f"[grain];movie={escape_path(dust)}:loop=0:seek_point={offset},setpts=N/({fps}*TB),"
                  f"format=yuv420p[layer];[grain][layer]blend=all_mode=grainmerge")
    graph += (f",scale={width}:{height}:flags=bilinear[film];"
              f"[base][film]blend=all_mode=grainmerge:shortest=1")
    if subtitles:
        graph += f",subtitles=filename={escape_path(subtitles)}" + (f":fontsdir={escape_path(fonts_dir)}" if fonts_dir else "")
    return graph


# ── Captions as ASS (the same pill look as the Python-drawn captions) ─────────

def _ass_time(seconds: float) -> str:
    centis = int(round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def _ass_colour(rgb, alpha: int = 0) -> str:
    r, g, b = rgb[:3]
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def font_name(path: str) -> str:
    try:
        from PIL import ImageFont
        return ImageFont.truetype(path, 12).getname()[0]
    except (OSError, ImportError):
        return "Arial"


def write_ass(captions: list[dict], path, size: tuple[int, int], *, font_path: str | None, font_size: int,
              margin_bottom: int, background=(0, 0, 0, 160), foreground=(255, 255, 255),
              margin_left: int = 100, margin_right: int = 100, box: bool = True) -> str:
    """captions [{start, end, text}] → an ASS file, bottom-centred between the margins: an opaque box
    (BorderStyle 3, the episode pill) or, with ``box=False``, a thick black outline (the Shorts look)."""
    width, height = size
    border, outline = (3, 10) if box else (1, max(4, font_size // 12))
    back_alpha = 255 - int(background[3]) if len(background) > 3 else 0
    name = font_name(font_path) if font_path else "Arial"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Caption,{name},{font_size},{_ass_colour(foreground)},{_ass_colour(foreground)},"
        f"{_ass_colour(background, back_alpha if box else 0)},{_ass_colour(background, back_alpha)},-1,0,0,0,100,100,0,0,"
        f"{border},{outline},0,2,{margin_left},{margin_right},{margin_bottom},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in captions:
        text = re.sub(r"\s+", " ", str(cue["text"])).replace("{", "(").replace("}", ")").strip()
        if text:
            lines.append(f"Dialogue: 0,{_ass_time(cue['start'])},{_ass_time(cue['end'])},Caption,,0,0,0,,{text}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def ffmpeg_params(look_name: str, *, size, fps, captions=None, output_dir=None, font_path=None, font_size=36,
                  margin_bottom=90, key: str = "") -> tuple[list[str], dict]:
    """(extra ffmpeg output params for moviepy's writer, report) for the final encode."""
    subtitles = fonts = None
    if captions:
        subtitles = write_ass(captions, Path(output_dir) / "tape_captions.ass", size, font_path=font_path,
                              font_size=font_size, margin_bottom=margin_bottom)
        fonts = str(Path(font_path).parent) if font_path and Path(font_path).is_file() else None
    dust = dust_layer(look_name, size, fps)
    offset = start_offset(key or str(output_dir))
    graph = filter_graph(look_name, size, fps, subtitles, fonts, dust, offset)
    report = check_safety(LOOKS[look_name], fps)
    report.update(look=look_name, dust=bool(dust), dust_offset=offset, captions=bool(subtitles))
    return ["-vf", graph], report
