"""Tape (VHS) playback effect, applied by ffmpeg in the final encode pass (Phase 3.6-F).

``VIDEO_TAPE_EFFECT = "off" | "subtle" | "medium"`` (default ``subtle``). The
frames are rendered without captions; the encode then applies, in one ffmpeg
``-vf`` graph:

gentle horizontal gate weave → slight luminance wobble → very light chromatic
aberration (1–2 px) → grain (temporal luma noise) → faint scanlines → soft
vignette → a **tracking glitch** (horizontal tear + noise band + stronger
chroma shift, 0.3 s) only on scene cuts (and optionally the title card) →
finally the captions (``subtitles`` with an ASS file styled like the burnt-in
pill), so they stay crisp.

**Photosensitivity safety** (``check_safety``): no full-frame flash (the noise
band covers 3.5 % of the frame at low opacity), the estimated mean-luminance
change between consecutive frames stays ≤ 10 %, and glitches start at least
``MIN_GLITCH_GAP`` seconds apart (never more than 3 per second; in practice one
per scene cut). Thumbnails never get the effect.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import config

LEVELS = {
    "subtle": {"grain": 5, "chroma": 1, "scanline_alpha": 0.06, "vignette": "PI/5", "wobble": 0.008,
               "wobble_hz": 0.7, "weave": 1, "weave_hz": 0.8, "glitch_shift": 4, "glitch_noise": 14,
               "band_alpha": 0.08, "tear": 12},
    "medium": {"grain": 9, "chroma": 2, "scanline_alpha": 0.10, "vignette": "PI/4", "wobble": 0.015,
               "wobble_hz": 0.9, "weave": 2, "weave_hz": 1.0, "glitch_shift": 8, "glitch_noise": 22,
               "band_alpha": 0.12, "tear": 20},
}
GLITCH_SECONDS = 0.3
GLITCH_LEAD = 0.1                 # the glitch starts just before the cut
MIN_GLITCH_GAP = 1.0              # seconds between glitch onsets (≤ 3 per second required; 1 per second used)
BAND = (0.62, 0.035)              # noise band / tear: top and height as a fraction of the frame
MAX_LUMA_STEP = 0.10              # mean luminance change allowed between consecutive frames
MAX_FLASH_AREA = 0.25             # a lighter area must never cover more of the frame than this


class TapeSafetyError(ValueError):
    """The effect would break the photosensitivity rules."""


def intensity(value: str | None = None) -> str:
    chosen = str(value or getattr(config, "VIDEO_TAPE_EFFECT", "subtle")).strip().lower()
    return chosen if chosen in LEVELS else "off"


def glitch_windows(cuts: list[float], duration: float, *, title_card: bool = False) -> list[tuple[float, float]]:
    """0.3 s windows around each cut (and at 0 s for the title card), onsets ≥ MIN_GLITCH_GAP apart."""
    starts = ([0.2] if title_card else []) + [max(0.0, cut - GLITCH_LEAD) for cut in sorted(cuts) if 0 < cut < duration]
    windows = []
    for start in starts:
        if windows and start - windows[-1][0] < MIN_GLITCH_GAP:
            continue
        windows.append((round(start, 3), round(min(duration, start + GLITCH_SECONDS), 3)))
    return windows


def max_luma_step(level: dict, fps: float) -> float:
    """Worst mean-luminance change between two consecutive frames (0-1 of full scale)."""
    wobble = level["wobble"] * 2 * math.pi * level["wobble_hz"] / fps      # max slope of A·sin(2πft) per frame
    band = BAND[1] * level["band_alpha"]                                 # the noise band appearing at once
    return wobble + band


def check_safety(level: dict, windows: list[tuple[float, float]], fps: float) -> dict:
    """Raise TapeSafetyError when a rule is broken; return the measured values."""
    step = max_luma_step(level, fps)
    onsets = [start for start, _ in windows]
    worst = max((sum(1 for other in onsets if start <= other < start + 1.0) for start in onsets), default=0)
    report = {"max_luma_step": round(step, 4), "max_glitches_per_second": worst,
              "flash_area": BAND[1]}
    problems = []
    if step > MAX_LUMA_STEP:
        problems.append(f"luminance changes {step:.0%} between frames (max {MAX_LUMA_STEP:.0%})")
    if worst > 3:
        problems.append(f"{worst} glitches within one second (max 3)")
    if BAND[1] > MAX_FLASH_AREA:
        problems.append("the noise band would cover too much of the frame")
    if problems:
        raise TapeSafetyError("; ".join(problems))
    return report


def escape_path(path) -> str:
    """A path for a filter option (Windows drive colons and quotes escaped), single-quoted."""
    text = str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"'{text}'"


def write_scanlines(path, size: tuple[int, int], alpha: float) -> str:
    """A transparent PNG with a faint dark line every 3 px: overlaid once per frame it costs a third of
    ``drawgrid`` (measured on 1080p: +0.8 s vs +2.4 s per 6 s of video)."""
    from PIL import Image
    width, height = size
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    line = Image.new("RGBA", (width, 1), (0, 0, 0, round(255 * alpha)))
    for y in range(0, height, 3):
        image.paste(line, (0, y))
    image.save(path)
    return str(path)


def filter_graph(level_name: str, size: tuple[int, int], fps: float, windows: list[tuple[float, float]],
                 subtitles: str | None = None, fonts_dir: str | None = None, scanlines: str | None = None) -> str:
    """The -vf filter graph (tape look, glitches on ``windows``, then the captions). ``scanlines`` is a PNG
    from ``write_scanlines`` (faster); without it ``drawgrid`` draws them."""
    level = LEVELS[level_name]
    check_safety(level, windows, fps)
    width, height = size
    weave = level["weave"]
    parts = [                                            # chromashift (YUV planes): 3× cheaper than rgbashift
        f"crop=w=iw-{2 * weave}:h=ih:x='{weave}+{weave}*sin(2*PI*t*{level['weave_hz']})':y=0",
        f"pad={width}:{height}:{weave}:0",
        f"eq=brightness='{level['wobble']}*sin(2*PI*t*{level['wobble_hz']})':eval=frame",
        f"chromashift=cbh={level['chroma']}:crh=-{level['chroma']}",
        f"noise=c0s={level['grain']}:c0f=t+u",
    ]
    graph = ",".join(parts)
    if scanlines:
        graph += f",format=yuv420p[tapebase];movie={escape_path(scanlines)}[scan];[tapebase][scan]overlay=format=auto"
    else:
        graph += f",drawgrid=w=iw:h=3:t=1:c=black@{level['scanline_alpha']}"
    graph += f",vignette=angle={level['vignette']}"
    if windows:
        enable = "+".join(f"between(t,{start},{end})" for start, end in windows)
        top, band = BAND
        graph += (f",split[base][cut];[cut]crop=w=iw:h=ih*{band}:x=0:y=ih*{top}[tear];"
                  f"[base][tear]overlay=x={level['tear']}:y=main_h*{top}:enable='{enable}',"
                  f"chromashift=cbh={level['glitch_shift']}:crh=-{level['glitch_shift']}:enable='{enable}',"
                  f"noise=alls={level['glitch_noise']}:allf=t:enable='{enable}',"
                  f"drawbox=x=0:y=ih*{top}:w=iw:h=ih*{band}:color=white@{level['band_alpha']}:t=fill:enable='{enable}'")
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


def ffmpeg_params(level_name: str, *, size, fps, cuts, duration, captions=None, output_dir=None,
                  font_path=None, font_size=36, margin_bottom=90, title_card=None) -> tuple[list[str], dict]:
    """(extra ffmpeg output params for moviepy's writer, report) for the final encode."""
    title_card = bool(getattr(config, "VIDEO_TAPE_GLITCH_TITLE", False) if title_card is None else title_card)
    windows = glitch_windows(cuts, duration, title_card=title_card)
    subtitles = fonts = None
    scanlines = write_scanlines(Path(output_dir) / "tape_scanlines.png", size, LEVELS[level_name]["scanline_alpha"])         if output_dir else None
    if captions:
        subtitles = write_ass(captions, Path(output_dir) / "tape_captions.ass", size, font_path=font_path,
                              font_size=font_size, margin_bottom=margin_bottom)
        fonts = str(Path(font_path).parent) if font_path and Path(font_path).is_file() else None
    graph = filter_graph(level_name, size, fps, windows, subtitles, fonts, scanlines)
    report = check_safety(LEVELS[level_name], windows, fps)
    report.update(intensity=level_name, glitches=len(windows), captions=bool(subtitles))
    return ["-vf", graph], report
