"""Render the Shorts of the plan at 1080×1920 (Phase 3.7-B.2).

Each Short reuses the episode exactly:

* **audio** — the slice of the final mix (narration with the music already
  ducked under it), cut on the plan's sentence times, faded in/out and
  normalised to −14 LUFS / −1.5 dBTP (``LOUDNESS_TARGET_LUFS`` / ``LOUDNESS_TRUE_PEAK``);
* **visuals** — the shots covering the segment, **reframed to 9:16**: a crop
  window centred on the detected people/faces (layer-1 detector) panning slowly
  in the shot's Ken Burns direction; when the subject is wider than the window,
  a blurred, darkened copy of the image fills the frame behind the 16:9 image
  (no letterbox bars). A clipped shot shorter than ``MIN_SHOT_SECONDS`` is not
  split off (its neighbour covers it); long holds get a slow push-in;
* **captions** — Shorts style: large, 2–4 words per cue, word-timed, centred in
  the lower-middle third, clear of YouTube's UI (bottom ``SAFE_BOTTOM`` and right
  ``SAFE_RIGHT`` of the frame); burnt by ffmpeg after the tape effect (same
  ``VIDEO_FILM_EFFECT`` film look and photosensitivity guard as the episode);
* ``hook_text`` in the top third for the first ~2 s, ``end_text`` card for the last ~2 s.

Output: ``shorts/short_NN.mp4`` and ``shorts/short_NN.srt`` (sentence cues, for
the caption upload).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

import config
from agents.narrative import atomic_write_json, load_json
from video import shorts, tape

W, H, FPS = 1080, 1920, 30
MIN_SHOT_SECONDS = 2.5
LONG_HOLD = 6.0
PAN_FRACTION = 0.04           # of the source width, over the shot
PUSH_IN = 1.06
SUBJECT_MARGIN = 0.12         # extra room around the subject box (fraction of its width)
MAIN_SUBJECT = 0.35           # people at least this fraction of the largest one's area are main subjects
SAFE_BOTTOM = 0.20
SAFE_RIGHT = 0.15
CAPTION_SIZE = 104
CAPTION_MAX_WORDS = 4
HOOK_SECONDS = 2.0
END_SECONDS = 2.0


# ── Reframing ───────────────────────────────────────────────────────────────

def main_subjects(detection: dict | None) -> list:
    """The boxes the 9:16 window must keep: the faces (else bodies) of the largest people. Small
    background figures do not count, so they never force the blur-fill."""
    people = (detection or {}).get("persons") or []
    if not people:
        return []
    area = lambda box: (box[2] - box[0]) * (box[3] - box[1])
    largest = max(area(person["box"]) for person in people)
    main = [person for person in people if area(person["box"]) >= MAIN_SUBJECT * largest]
    faces = [person["face"]["box"] for person in main if person.get("face")]
    return faces if len(faces) == len(main) else [person["box"] for person in main]


def crop_plan(boxes: list, source: tuple[int, int]) -> dict:
    """9:16 window over a source image: {"mode": "crop", "x": left px, "width"} centred on the subjects,
    or {"mode": "fill"} when they do not fit (blur-fill fallback)."""
    width, height = source
    window = round(height * W / H)
    if window >= width:
        return {"mode": "fill"}
    if not boxes:
        return {"mode": "crop", "x": (width - window) // 2, "width": window}
    left = min(box[0] for box in boxes) * width
    right = max(box[2] for box in boxes) * width
    margin = (right - left) * SUBJECT_MARGIN
    if right - left + 2 * margin > window:
        return {"mode": "fill"}
    centre = (left + right) / 2
    x = round(min(max(centre - window / 2, 0), width - window))
    return {"mode": "crop", "x": x, "width": window}


def pan_offset(plan: dict, source_width: int, direction: int, progress: float) -> int:
    """The crop's left edge at ``progress`` (0-1) of the shot: a slow drift, kept inside the image."""
    if plan["mode"] != "crop" or not direction:
        return plan.get("x", 0)
    travel = PAN_FRACTION * source_width
    x = plan["x"] + direction * travel * (progress - 0.5)
    return round(min(max(x, 0), source_width - plan["width"]))


def fill_frame(image: Image.Image) -> Image.Image:
    """Blurred, darkened copy as the background, the 16:9 image centred over it."""
    scale = max(W / image.width, H / image.height)
    background = image.resize((round(image.width * scale), round(image.height * scale)), Image.BILINEAR)
    left, top = (background.width - W) // 2, (background.height - H) // 2
    background = background.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(28))
    background = ImageEnhance.Brightness(background).enhance(0.45)
    front = image.resize((W, round(image.height * W / image.width)), Image.LANCZOS)
    background.paste(front, (0, (H - front.height) // 2))
    return background


def shot_pieces(shot_list: dict, output_dir, start: float, end: float) -> list[dict]:
    """The shots covering [start, end] in Short time, short edge pieces merged into their neighbour."""
    from agents.shot_list import resolve_image
    shots = shot_list.get("shots", [])
    pieces = []
    for index, shot in enumerate(shots):
        begin, finish = max(float(shot["start"]), start), min(float(shot["end"]), end)
        if finish <= begin:
            continue
        image = resolve_image(shots, shot)
        if not image:
            continue
        path = image if os.path.isabs(image) else os.path.join(output_dir, image)
        pieces.append({"shot_id": shot["shot_id"], "image": path, "shot_type": shot.get("shot_type", "medium"),
                       "index": index, "start": round(begin - start, 3), "end": round(finish - start, 3)})
    merged = []
    for piece in pieces:
        if merged and piece["end"] - piece["start"] < MIN_SHOT_SECONDS:
            merged[-1]["end"] = piece["end"]                 # too short to cut to: the previous shot holds
        elif not merged and piece["end"] - piece["start"] < MIN_SHOT_SECONDS and len(pieces) > 1:
            continue                                         # a sliver at the start: the next one starts at 0
        else:
            merged.append(dict(piece))
    if merged:
        merged[0]["start"] = 0.0
        merged[-1]["end"] = round(end - start, 3)
        for current, following in zip(merged, merged[1:]):
            current["end"] = following["start"]
    return merged


# ── Captions and overlays ───────────────────────────────────────────────────

def segment_words(words: list[dict], start: float, end: float) -> list[dict]:
    return [{**word, "start": round(word["start"] - start, 3), "end": round(word["end"] - start, 3)}
            for word in words if word["start"] >= start - 0.01 and word["end"] <= end + 0.05]


def caption_cues(words: list[dict], duration: float) -> list[dict]:
    """2-4 words per cue, word-timed (seconds)."""
    from video.captions import build_cues
    cues = build_cues(words, max_words=CAPTION_MAX_WORDS, max_chars=24, min_seconds=0.35, max_seconds=2.0,
                      end_limit=duration)
    return [{"start": start / 1000, "end": end / 1000, "text": text} for start, end, text in cues]


def caption_margins() -> dict:
    """ASS margins that keep captions centred in the lower-middle third, clear of the YouTube UI."""
    return {"margin_left": 60, "margin_right": round(W * SAFE_RIGHT) + 20, "margin_bottom": round(H * 0.30)}


def caption_box(text_width: int, text_height: int, margins: dict) -> tuple[int, int, int, int]:
    """Where a caption lands (for the safe-area check): bottom-centred between the margins."""
    left, right = margins["margin_left"], W - margins["margin_right"]
    x = left + (right - left - text_width) // 2
    bottom = H - margins["margin_bottom"]
    return x, bottom - text_height, x + text_width, bottom


def _font(size: int):
    for path in config.FONT_PATHS.get("bold", []):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_overlay(frame: Image.Image, text: str, *, top: bool, alpha: float) -> Image.Image:
    """hook_text (top third) or end_text (centre card), in the brand typography."""
    if not text or alpha <= 0:
        return frame
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(84)
    words, lines = text.upper().split(), []
    for word in words:
        trial = (lines[-1] + " " + word) if lines else word
        if lines and draw.textbbox((0, 0), trial, font=font)[2] <= W - 200:
            lines[-1] = trial
        else:
            lines.append(word)
    line_height = 100
    y = round(H * 0.16) if top else (H - line_height * len(lines)) // 2
    if not top:
        draw.rectangle([0, 0, W, H], fill=(0, 0, 0, round(150 * alpha)))
    colour = config.COLORS["highlight"] if top else config.COLORS["white"]
    for index, line in enumerate(lines):
        width = draw.textbbox((0, 0), line, font=font)[2]
        x = (W - width) // 2
        draw.text((x + 4, y + index * line_height + 5), line, font=font, fill=(0, 0, 0, round(200 * alpha)))
        draw.text((x, y + index * line_height), line, font=font, fill=(*colour, round(255 * alpha)))
    return Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")


# ── Audio ───────────────────────────────────────────────────────────────────

def audio_slice(source, destination, start: float, end: float) -> dict:
    """The exact slice of the final mix, 0.05/0.3 s fades, then loudness-normalised (two-pass)."""
    from video import music
    raw = Path(destination).with_suffix(".raw.wav")
    duration = end - start
    # -ss/-t BEFORE -i (input seeking): the slice starts at t=0 for the filters. With output seeking the
    # afade timestamps stay on the source timeline, so a slice starting after `duration - 0.3` s was faded
    # to silence (short_02/03: loudnorm measured -inf).
    result = subprocess.run([music.ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
                             "-t", f"{duration:.3f}", "-i", str(source), "-af",
                             f"afade=t=in:d=0.05,afade=t=out:st={max(0.0, duration - 0.3):.3f}:d=0.3",
                             "-ar", "44100", str(raw)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"audio slice failed: {result.stderr[-300:]}")
    try:
        report = music.normalize_loudness(str(raw), str(destination))
    finally:
        raw.unlink(missing_ok=True)
    report["seconds"] = round(duration, 3)
    return report


# ── Rendering ───────────────────────────────────────────────────────────────

def _prepare(pieces: list[dict], detector) -> list[dict]:
    from video.creator import shot_motion
    prepared = []
    for piece in pieces:
        with Image.open(piece["image"]) as opened:
            image = opened.convert("RGB")
        detection = detector.detect(piece["image"]) if detector else None
        plan = crop_plan(main_subjects(detection), image.size)
        _, _, pan = shot_motion(piece["shot_type"], piece["index"])
        prepared.append({**piece, "image_data": image, "plan": plan, "direction": pan[0],
                         "fill": fill_frame(image) if plan["mode"] == "fill" else None})
    return prepared


def frame_at(prepared: list[dict], t: float) -> Image.Image:
    piece = next((item for item in prepared if t < item["end"]), prepared[-1])
    duration = max(piece["end"] - piece["start"], 0.001)
    progress = min(max((t - piece["start"]) / duration, 0.0), 1.0)
    zoom = 1 + (PUSH_IN - 1) * progress if duration > LONG_HOLD or not piece["direction"] else 1.0
    if piece["plan"]["mode"] == "fill":
        frame = piece["fill"]
    else:
        image = piece["image_data"]
        x = pan_offset(piece["plan"], image.width, piece["direction"], progress)
        frame = image.crop((x, 0, x + piece["plan"]["width"], image.height)).resize((W, H), Image.BILINEAR)
    if zoom > 1.001:
        cw, ch = round(W / zoom), round(H / zoom)
        frame = frame.crop(((W - cw) // 2, (H - ch) // 2, (W - cw) // 2 + cw, (H - ch) // 2 + ch)).resize((W, H), Image.BILINEAR)
    return frame


def video_filter(level: str, ass_path: str, fonts_dir: str | None, key: str = "") -> str:
    """The encode's -vf: the film look (same setting as the episode, 9:16) then the captions."""
    if level == "off":
        return f"subtitles=filename={tape.escape_path(ass_path)}" + (
            f":fontsdir={tape.escape_path(fonts_dir)}" if fonts_dir else "")
    return tape.filter_graph(level, (W, H), FPS, ass_path, fonts_dir, tape.dust_layer(level, (W, H), FPS),
                             tape.start_offset(key))


def write_srt(cues: list[dict], path) -> str:
    def stamp(seconds):
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    blocks = [f"{index}\n{stamp(cue['start'])} --> {stamp(cue['end'])}\n{cue['text']}\n"
              for index, cue in enumerate(cues, 1)]
    Path(path).write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


def render_short(output_dir, segment: dict, *, words: list[dict], shot_list: dict, detector=None,
                 progress: Callable[[str], None] = print) -> dict:
    from moviepy.editor import AudioFileClip, VideoClip
    from video.captions import build_cues
    output = Path(output_dir)
    folder = output / shorts.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    start, end = float(segment["start"]), float(segment["end"])
    duration = round(end - start, 3)
    source = next((output / name for name in ("narration_mixed.mp3", "narration.mp3") if (output / name).is_file()), None)
    if source is None:
        raise FileNotFoundError("no narration audio")
    audio_path = folder / f"{segment['id']}.mp3"
    loudness = audio_slice(source, audio_path, start, end)
    pieces = shot_pieces(shot_list, output, start, end)
    if not pieces:
        raise FileNotFoundError(f"{segment['id']}: no shot image covers {start:.0f}-{end:.0f} s")
    prepared = _prepare(pieces, detector)
    local_words = segment_words(words, start, end)
    cues = caption_cues(local_words, duration)
    margins = caption_margins()
    font_path = next((path for path in config.FONT_PATHS["bold"] if os.path.exists(path)), None)
    ass = tape.write_ass(cues, folder / f"{segment['id']}.ass", (W, H), font_path=font_path, font_size=CAPTION_SIZE,
                         margin_bottom=margins["margin_bottom"], margin_left=margins["margin_left"],
                         margin_right=margins["margin_right"], box=False)
    sentence_cues = [{"start": a / 1000, "end": b / 1000, "text": text}
                     for a, b, text in build_cues(local_words, end_limit=duration)]
    srt = write_srt(sentence_cues, folder / f"{segment['id']}.srt")
    level = tape.look()
    vf = video_filter(level, ass, str(Path(font_path).parent) if font_path else None, key=segment["id"])

    def make_frame(t):
        import numpy as np
        frame = frame_at(prepared, t)
        if t < HOOK_SECONDS + 0.4:
            frame = draw_overlay(frame, segment.get("hook_text", ""), top=True,
                                 alpha=min(1.0, max(0.0, (HOOK_SECONDS + 0.4 - t) / 0.4)))
        if t > duration - END_SECONDS:
            frame = draw_overlay(frame, segment.get("end_text", ""), top=False,
                                 alpha=min(1.0, (t - (duration - END_SECONDS)) / 0.3))
        return np.array(frame)

    path = folder / f"{segment['id']}.mp4"
    audio = AudioFileClip(str(audio_path))
    clip = VideoClip(make_frame, duration=min(duration, audio.duration)).set_audio(audio)
    progress(f"   → {segment['id']}: {duration:.0f} s, {len(prepared)} shot(s) "
             f"({sum(1 for item in prepared if item['plan']['mode'] == 'fill')} blur-filled), tape {level}")
    try:
        clip.write_videofile(str(path), fps=FPS, codec="libx264", audio_codec="aac", threads=4, preset="veryfast",
                             ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-vf", vf],
                             verbose=False, logger=None)
    finally:
        audio.close()
        clip.close()
    return {"id": segment["id"], "video": str(path.relative_to(output)), "srt": str(Path(srt).relative_to(output)),
            "seconds": duration, "loudness": loudness, "shots": [item["shot_id"] for item in prepared],
            "blur_filled": [item["shot_id"] for item in prepared if item["plan"]["mode"] == "fill"]}


def render_all(output_dir, *, detector=None, progress: Callable[[str], None] = print, only: str | None = None) -> list[dict]:
    """Every included segment of the plan; results recorded in the plan."""
    output = Path(output_dir)
    plan = shorts.load(output)
    if not plan or not plan.get("segments"):
        raise FileNotFoundError("no Shorts plan: propose the cuts first")
    shot_list = load_json(output / "shot_list.json") or {}
    words = shorts.word_timeline(output)
    if detector is None:
        from video.qa import fixes
        detector = fixes._detector()
    done = []
    for segment in plan["segments"]:
        if not segment.get("include", True) or (only and segment["id"] != only):
            continue
        record = render_short(output, segment, words=words, shot_list=shot_list, detector=detector, progress=progress)
        segment["render"] = record
        done.append(record)
        atomic_write_json(output / shorts.FOLDER / shorts.PLAN_FILE, plan)
    return done


def estimate_seconds(plan: dict | None) -> float:
    """Render time estimate for the preflight: ~1.2 s of rendering per second of Short."""
    return round(sum(float(item.get("seconds") or 0) for item in (plan or {}).get("segments", [])
                     if item.get("include", True)) * 1.2, 1)
