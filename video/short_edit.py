"""The 9:16 edit of a format Short (Phase 4.3-D).

* **First frame** — the most striking image of the Short's source scenes (``first_image``: the largest face, else the
  busiest detail; never a dark or empty image), already moving, with ``on_screen_hook`` in large brand type in the
  upper third for the first 1.5 s. The first frame is never black.
* **Rhythm** — 1-2.5 s per view, cut on word starts, never more than ``MAX_HOLD`` (3 s) without a visual change. The
  views are the 9:16 reframings of the shot images (subject-centred crop, blur-fill fallback, as in
  ``video.shorts_render``) at three framings: full, medium (1.25x), detail (1.5x, only from an upscaled image or on a
  face); each view moves (a slow push or drift).
* **Captions** — big, centred, 1-3 words at a time, the spoken word highlighted on the word timings, key words in
  Antique Gold, inside the Shorts safe area (``shorts_render.caption_margins``). Burnt by ffmpeg after the film look.
* **CTA card** — the last ``short_voice.END_CARD`` seconds (not for The Line): the episode's thumbnail image blurred
  behind "Full story on the channel ↑" (the related-video link sits above the title in the Shorts player); the
  Dilemma's question above it. Never a "Part 2".
* **The Line** — the last 0.5 s return to the first frame, so the loop is seamless.
* The film look and its photosensitivity guard are the episode's (``shorts_render.video_filter``).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import config
from agents.narrative import load_json
from video import shorts_render as base

W, H, FPS = base.W, base.H, base.FPS
MIN_VIEW, TARGET_VIEW, MAX_VIEW = 1.0, 1.8, 2.5
MAX_HOLD = 3.0
SCREEN_HOOK_SECONDS = 1.5
LOOP_SECONDS = 0.5
CAPTION_WORDS = 3
CAPTION_SIZE = 100
DARK_LUMA = 0.08
FLAT_STD = 0.03
FRAMINGS = {"full": 1.0, "medium": 1.25, "detail": 1.5}
PUSH = 1.07
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "his", "her", "their", "that", "this",
        "it", "is", "was", "be", "at", "by", "from", "as", "you", "your", "he", "she", "they", "we", "i", "but", "had",
        "has", "have", "not", "no", "so", "then", "what", "who", "did", "do", "are", "were", "him", "them", "all"}


# ── Sources and the first frame ─────────────────────────────────────────────

def source_images(output_dir, shot_list: dict, scene_ids: list[str]) -> list[dict]:
    """The shot images of the cited scenes (section ids), in story order, one entry per image."""
    from agents.shot_list import resolve_image
    shots = shot_list.get("shots", [])
    wanted = {str(scene) for scene in scene_ids}
    found, seen = [], set()
    for shot in shots:
        if str(shot.get("section_id")) not in wanted and str(shot.get("scene_id")) not in wanted:
            continue
        image = resolve_image(shots, shot)
        if not image:
            continue
        path = image if os.path.isabs(image) else os.path.join(str(output_dir), image)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        upscaled = Path(output_dir) / "images" / "shots" / "upscaled" / f"{shot['shot_id']}.png"
        found.append({"shot_id": shot["shot_id"], "image": path, "upscaled": str(upscaled) if upscaled.is_file() else "",
                      "shot_type": shot.get("shot_type", "medium")})
    return found


def luma_stats(image: Image.Image) -> tuple[float, float]:
    gray = np.asarray(image.convert("L").resize((96, 54)), dtype=np.float32) / 255.0
    return float(gray.mean()), float(gray.std())


def usable(image: Image.Image) -> bool:
    """Not black, not empty: a first frame must show something."""
    mean, spread = luma_stats(image)
    return mean >= DARK_LUMA and spread >= FLAT_STD


def salience(image: Image.Image, detection: dict | None) -> float:
    """Faces first (the larger the better), then people, then the busiest detail."""
    from video.rhythm import edge_density
    people = (detection or {}).get("persons") or []
    area = lambda box: max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
    face = max((area(person["face"]["box"]) for person in people if person.get("face")), default=0.0)
    body = max((area(person["box"]) for person in people), default=0.0)
    gray = np.asarray(image.convert("L").resize((320, 180)), dtype=np.float32)
    return 10.0 * face + 2.0 * body + edge_density(gray, (0, 0, 1, 1))


def first_image(candidates: list[dict], detect: Callable | None = None) -> int:
    """Index of the most striking usable candidate (never a dark or empty one); 0 when none is usable."""
    best, best_score = None, -1.0
    for index, item in enumerate(candidates):
        with Image.open(item["image"]) as opened:
            image = opened.convert("RGB")
        if not usable(image):
            continue
        score = salience(image, detect(item["image"]) if detect else None)
        if score > best_score:
            best, best_score = index, score
    return best if best is not None else 0


# ── Views ───────────────────────────────────────────────────────────────────

def view_box(plan: dict, source: tuple[int, int], framing: float, faces: list) -> tuple[int, int, int, int] | None:
    """The pixel box of a 9:16 view at ``framing`` (1 = the full-height window); None for a fill view."""
    width, height = source
    if plan["mode"] == "fill":
        return None
    window_h = height / framing
    window_w = window_h * W / H
    if faces and framing > 1.0:
        centre_x = (min(face[0] for face in faces) + max(face[2] for face in faces)) / 2 * width
        centre_y = (min(face[1] for face in faces) + max(face[3] for face in faces)) / 2 * height
    else:
        centre_x, centre_y = plan["x"] + plan["width"] / 2, height / 2
    left = min(max(centre_x - window_w / 2, 0), width - window_w)
    top = min(max(centre_y - window_h * 0.42, 0), height - window_h)
    return int(left), int(top), int(left + window_w), int(top + window_h)


def views_for(item: dict, image: Image.Image, detection: dict | None) -> list[dict]:
    """The 9:16 framings an image allows: full always; medium and detail when the crop stays sharp enough."""
    boxes = base.main_subjects(detection)
    plan = base.crop_plan(boxes, image.size)
    faces = [person["face"]["box"] for person in (detection or {}).get("persons") or [] if person.get("face")]
    if plan["mode"] == "fill":
        return [{"framing": "full", "fill": True}]
    found = [{"framing": "full", "box": view_box(plan, image.size, 1.0, faces)}]
    found.append({"framing": "medium", "box": view_box(plan, image.size, FRAMINGS["medium"], faces)})
    if item.get("upscaled") or faces:
        found.append({"framing": "detail", "box": view_box(plan, image.size, FRAMINGS["detail"], faces)})
    return found


# ── The schedule ────────────────────────────────────────────────────────────

def cut_times(start: float, end: float, words: list[dict]) -> list[float]:
    """Cut points between ``start`` and ``end``: 1-2.5 s apart, on word starts when one is near, never a gap > 3 s."""
    onsets = sorted(float(word["start"]) for word in words if start < float(word["start"]) < end)
    cuts, cursor = [], start
    while end - cursor > MAX_VIEW:
        near = [time for time in onsets if cursor + MIN_VIEW <= time <= cursor + MAX_VIEW]
        nxt = min(near, key=lambda time: abs(time - (cursor + TARGET_VIEW))) if near else cursor + TARGET_VIEW
        if end - nxt < MIN_VIEW * 0.6:                    # a sliver at the end: split the rest evenly instead
            nxt = cursor + (end - cursor) / 2
        cuts.append(round(nxt, 3))
        cursor = nxt
    return cuts


def schedule(duration: float, words: list[dict], sources: list[dict], first: int, *, card_start: float | None,
             loop: bool = False) -> list[dict]:
    """[{"start", "end", "source", "view", "direction"}] covering [0, card_start or duration]; no piece over 3 s."""
    end = float(card_start if card_start is not None else duration)
    body_end = end - LOOP_SECONDS if loop else end
    times = [0.0] + cut_times(0.0, body_end, words) + [body_end]
    order = [first] + [index for index in range(len(sources)) if index != first]
    pairs = []
    rounds = max(len(source["views"]) for source in sources) if sources else 1
    for framing_round in range(rounds):                   # every image at its next framing before any repeats
        for index in order:
            views = sources[index]["views"]
            pairs.append((index, views[framing_round % len(views)]["framing"]))
    pieces = []
    for number, (start, stop) in enumerate(zip(times, times[1:])):
        index, framing = pairs[number % len(pairs)]
        if pieces and (pieces[-1]["source"], pieces[-1]["view"]) == (index, framing) and len(pairs) > 1:
            index, framing = pairs[(number + 1) % len(pairs)]
        pieces.append({"start": round(start, 3), "end": round(stop, 3), "source": index, "view": framing,
                       "direction": 1 if number % 2 == 0 else -1})
    if loop:
        pieces.append({"start": round(body_end, 3), "end": round(end, 3), "source": first, "view": "full",
                       "direction": 1, "loop_return": True})
    return pieces


def longest_hold(pieces: list[dict]) -> float:
    return max((piece["end"] - piece["start"] for piece in pieces), default=0.0)


# ── Frames ──────────────────────────────────────────────────────────────────

def _render_view(source: dict, view: dict, progress: float, direction: int) -> Image.Image:
    image = source["image_data"]
    if view.get("fill"):
        frame = source["fill"]
        zoom = 1.0 + (PUSH - 1.0) * progress
    else:
        left, top, right, bottom = view["box"]
        width, height = right - left, bottom - top
        zoom = 1.0 + (PUSH - 1.0) * progress
        shrink_w, shrink_h = width * (1 - 1 / zoom), height * (1 - 1 / zoom)
        drift = direction * shrink_w * (progress - 0.5) * 0.6
        box = (left + shrink_w / 2 + drift, top + shrink_h / 2, right - shrink_w / 2 + drift, bottom - shrink_h / 2)
        box = (max(0, box[0]), max(0, box[1]), min(image.width, box[2]), min(image.height, box[3]))
        return image.resize((W, H), Image.BILINEAR, box=box)
    cw, ch = round(W / zoom), round(H / zoom)
    return frame.crop(((W - cw) // 2, (H - ch) // 2, (W - cw) // 2 + cw, (H - ch) // 2 + ch)).resize((W, H), Image.BILINEAR)


def frame_at(sources: list[dict], pieces: list[dict], t: float) -> Image.Image:
    """The picture at ``t`` (no overlays). The loop return plays the first view backwards to its starting frame."""
    piece = next((item for item in pieces if t < item["end"]), pieces[-1])
    source = sources[piece["source"]]
    view = next(item for item in source["views"] if item["framing"] == piece["view"])
    length = max(piece["end"] - piece["start"], 1e-3)
    progress = min(max((t - piece["start"]) / length, 0.0), 1.0)
    if piece.get("loop_return"):
        first = pieces[0]
        start_length = max(first["end"] - first["start"], 1e-3)
        progress = (1.0 - progress) * min(1.0, LOOP_SECONDS / start_length)    # ends exactly on the first frame
        return _render_view(source, view, progress, first["direction"])
    return _render_view(source, view, progress, piece["direction"])


def loop_difference(sources: list[dict], pieces: list[dict], duration: float) -> float:
    """Mean absolute difference (0-1) between the last frame and the first: ~0 for a seamless loop."""
    first = np.asarray(frame_at(sources, pieces, 0.0).resize((108, 192)), dtype=np.float32)
    last = np.asarray(frame_at(sources, pieces, duration - 1e-4).resize((108, 192)), dtype=np.float32)
    return float(np.abs(first - last).mean() / 255.0)


# ── Captions ────────────────────────────────────────────────────────────────

def key_words(item: dict, words: list[dict]) -> set[int]:
    """Indexes of the words drawn in Antique Gold: names, the hook's content words, long words; at most 1 per group."""
    hook = {token for token in re.findall(r"[a-z']+", str(item.get("on_screen_hook", "") + " " + item.get("hook_line", "")).lower())
            if token not in STOP}
    found = set()
    for index, word in enumerate(words):
        token = re.sub(r"[^A-Za-z']", "", word["text"])
        lowered = token.lower()
        if not token or lowered in STOP:
            continue
        if (token[0].isupper() and index and not words[index - 1].get("sentence_end")) or lowered in hook or len(token) >= 8:
            found.add(index)
    return found


def fits_width(font=None) -> Callable[[str], bool]:
    """Whether a caption line (upper case, the spoken word scaled 110 %) fits inside the safe area on one line."""
    font = font or base._font(CAPTION_SIZE)
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    margins = base.caption_margins()
    room = W - margins["margin_left"] - margins["margin_right"]
    return lambda text: draw.textbbox((0, 0), str(text).upper(), font=font)[2] * 1.1 <= room


def caption_groups(words: list[dict], limit: int = CAPTION_WORDS, fits: Callable[[str], bool] | None = None) -> list[list[int]]:
    """1-3 word indexes per group, one line wide; a sentence end or a pause closes a group."""
    fits = fits or fits_width()
    groups, current = [], []
    for index, word in enumerate(words):
        wider = current and not fits(" ".join(words[item]["text"] for item in current + [index]))
        if current and (len(current) >= limit or wider or float(word["start"]) - float(words[current[-1]]["end"]) > 0.35):
            groups.append(current)
            current = []
        current.append(index)
        if word.get("sentence_end"):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def caption_events(words: list[dict], keys: set[int], *, end_limit: float, fits: Callable[[str], bool] | None = None) -> list[dict]:
    """One event per spoken word: its group on screen, the word being spoken highlighted."""
    events = []
    groups = caption_groups(words, fits=fits)
    for number, group in enumerate(groups):
        group_end = float(words[groups[number + 1][0]]["start"]) if number + 1 < len(groups) else float(words[group[-1]]["end"]) + 0.4
        group_end = min(group_end, float(words[group[-1]]["end"]) + 0.6, end_limit)
        for position, index in enumerate(group):
            start = float(words[index]["start"])
            stop = float(words[group[position + 1]]["start"]) if position + 1 < len(group) else group_end
            if stop - start < 0.04:
                continue
            events.append({"start": round(start, 3), "end": round(stop, 3), "words": [
                {"text": words[other]["text"], "current": other == index, "key": other in keys} for other in group]})
    return events


def write_captions(events: list[dict], path, font_path: str | None) -> str:
    """The ASS file: centred captions, the current word full ivory and slightly larger, the others dimmed, key words
    in Antique Gold."""
    from video.tape import _ass_colour, _ass_time, font_name
    margins = base.caption_margins()
    ivory, gold = config.COLORS["white"], config.COLORS["highlight"]
    name = font_name(font_path) if font_path else "Arial"
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
             "MarginR, MarginV, Encoding",
             f"Style: Short,{name},{CAPTION_SIZE},{_ass_colour(ivory)},{_ass_colour(ivory)},{_ass_colour((0, 0, 0))},"
             f"{_ass_colour((0, 0, 0), 80)},-1,0,0,0,100,100,0,0,1,{max(5, CAPTION_SIZE // 12)},2,2,"
             f"{margins['margin_left']},{margins['margin_right']},{margins['margin_bottom']},1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for event in events:
        parts = []
        for word in event["words"]:
            text = re.sub(r"[{}\\]", "", str(word["text"])).upper()
            colour = gold if word["key"] else ivory
            alpha = 0 if word["current"] else 0x70
            scale = 110 if word["current"] else 100
            parts.append(f"{{\\c{_ass_colour(colour)}\\alpha&H{alpha:02X}&\\fscx{scale}\\fscy{scale}}}{text}")
        lines.append(f"Dialogue: 0,{_ass_time(event['start'])},{_ass_time(event['end'])},Short,,0,0,0,,{' '.join(parts)}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def caption_box_ok(text: str, font) -> bool:
    """Whether a caption of ``text`` (current word scaled) fits inside the Shorts safe area."""
    margins = base.caption_margins()
    draw = ImageDraw.Draw(Image.new("RGB", (W, H)))
    left, top, right, bottom = draw.textbbox((0, 0), text.upper(), font=font)
    width, height = int((right - left) * 1.1), bottom - top
    box = base.caption_box(width, height, margins)
    return box[0] >= margins["margin_left"] - 1 and box[2] <= W - round(W * base.SAFE_RIGHT) and box[3] <= H * (1 - base.SAFE_BOTTOM)


# ── Overlays: the on-screen hook and the CTA card ───────────────────────────

def cta_lines(item: dict) -> list[str]:
    """The card's text: the Dilemma's question above "Full story on the channel ↑"; never a "Part 2"."""
    from video.short_formats import DEFAULT_CTA_SCREEN
    lines = []
    if item.get("format") == "dilemma" and str(item.get("question", "")).strip():
        lines.append(str(item["question"]).strip())
    lines.append(DEFAULT_CTA_SCREEN)
    return [line for line in lines if "part 2" not in line.lower()]


def thumbnail_background(output_dir, fallback: str) -> Image.Image:
    """The episode's thumbnail image (unlettered background when kept), else ``fallback``; blurred and darkened."""
    output = Path(output_dir)
    index = load_json(output / "thumbnails" / "thumbnails.json") or {}
    selected = index.get("selected")
    candidates = [output / "thumbnails" / f"background_{selected}.jpg"] if selected else []
    candidates += [output / str(item.get("path", "")) for item in index.get("variants", []) if item.get("number") == selected]
    path = next((str(candidate) for candidate in candidates if candidate.is_file()), fallback)
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    scale = max(W / image.width, H / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.BILINEAR)
    left, top = (image.width - W) // 2, (image.height - H) // 2
    image = image.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(24))
    return ImageEnhance.Brightness(image).enhance(0.42)


def draw_card(background: Image.Image, lines: list[str], alpha: float = 1.0) -> Image.Image:
    layer = background.copy()
    draw = ImageDraw.Draw(layer)
    y = int(H * 0.40)
    for number, line in enumerate(lines):
        font = base._font(78 if number < len(lines) - 1 else 70)
        wrapped, current = [], ""
        for word in line.split():
            trial = f"{current} {word}".strip()
            if current and draw.textbbox((0, 0), trial, font=font)[2] > W - 180:
                wrapped.append(current)
                current = word
            else:
                current = trial
        wrapped.append(current)
        colour = config.COLORS["highlight"] if number == len(lines) - 1 else config.COLORS["white"]
        for row in wrapped:
            width = draw.textbbox((0, 0), row, font=font)[2]
            draw.text(((W - width) // 2 + 3, y + 4), row, font=font, fill=(0, 0, 0))
            draw.text(((W - width) // 2, y), row, font=font, fill=colour)
            y += 96
        y += 50
    return Image.blend(background, layer, alpha) if alpha < 1 else layer


# ── Rendering ───────────────────────────────────────────────────────────────

def prepare_sources(candidates: list[dict], item: dict, detect: Callable | None) -> list[dict]:
    prepared = []
    for candidate in candidates:
        path = candidate.get("upscaled") or candidate["image"]
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        detection = detect(candidate["image"]) if detect else None
        views = views_for(candidate, image, detection)
        prepared.append({**candidate, "image_data": image, "views": views,
                         "fill": base.fill_frame(image) if any(view.get("fill") for view in views) else None})
    return prepared


def render(output_dir, item: dict, voice: dict, audio: dict, *, shot_list: dict | None = None, detector=None,
           progress: Callable[[str], None] = print) -> dict:
    """shorts/<id>.mp4 (+ .srt): the edit over the mixed audio."""
    from moviepy.editor import AudioFileClip, VideoClip
    from video import tape
    from video.captions import build_cues
    output = Path(output_dir)
    folder = output / "shorts"
    shot_list = shot_list if shot_list is not None else load_json(output / "shot_list.json") or {}
    candidates = source_images(output, shot_list, item.get("source_scene_ids") or [])
    if not candidates:                                    # no image of the cited scenes: any shot before the twist
        candidates = source_images(output, shot_list, [str(shot.get("section_id")) for shot in shot_list.get("shots", [])][:40])
    if not candidates:
        raise FileNotFoundError(f"{item['id']}: no shot image for scenes {item.get('source_scene_ids')}")
    detect = detector.detect if detector is not None else None
    chosen = first_image(candidates, detect)
    ordered = [candidates[chosen]] + [candidate for index, candidate in enumerate(candidates) if index != chosen]
    sources = prepare_sources(ordered[:8], item, detect)      # at most 8 images in 20-45 s
    first = 0
    duration = float(audio["plan"]["duration"])
    loop = item.get("format") == "line"
    card_start = None if loop else float(voice["duration"])
    pieces = schedule(duration, voice["words"], sources, first, card_start=card_start, loop=loop)
    card = draw_card(thumbnail_background(output, sources[first]["image"]), cta_lines(item)) if card_start is not None else None
    words = voice["words"]
    events = caption_events(words, key_words(item, words), end_limit=card_start or duration)
    font_path = next((path for path in config.FONT_PATHS["bold"] if os.path.exists(path)), None)
    ass = write_captions(events, folder / f"{item['id']}.ass", font_path)
    sentence_cues = [{"start": a / 1000, "end": b / 1000, "text": text} for a, b, text in build_cues(words, end_limit=duration)]
    srt = base.write_srt(sentence_cues, folder / f"{item['id']}.srt")
    level = tape.look()
    vf = base.video_filter(level, ass, str(Path(font_path).parent) if font_path else None, key=item["id"])
    hook = item.get("on_screen_hook", "")

    def make_frame(t):
        if card is not None and t >= card_start:
            return np.array(card)
        frame = frame_at(sources, pieces, t)
        if t < SCREEN_HOOK_SECONDS + 0.3:
            frame = base.draw_overlay(frame, hook, top=True, alpha=min(1.0, max(0.0, (SCREEN_HOOK_SECONDS + 0.3 - t) / 0.3)))
        if card is not None and t > card_start - 0.25:
            frame = Image.blend(frame, card, min(1.0, (t - (card_start - 0.25)) / 0.25))
        return np.array(frame)

    path = folder / f"{item['id']}.mp4"
    clip_audio = AudioFileClip(audio["audio"])
    clip = VideoClip(make_frame, duration=min(duration, clip_audio.duration)).set_audio(clip_audio)
    progress(f"   → {item['id']} ({item['format']}): {duration:.1f} s, {len(pieces)} views, longest {longest_hold(pieces):.1f} s, film {level}")
    try:
        clip.write_videofile(str(path), fps=FPS, codec="libx264", audio_codec="aac", threads=4, preset="veryfast",
                             ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-vf", vf],
                             verbose=False, logger=None)
    finally:
        clip_audio.close()
        clip.close()
    return {"id": item["id"], "video": str(path.relative_to(output)), "srt": str(Path(srt).relative_to(output)),
            "seconds": round(duration, 2), "views": len(pieces), "longest_hold": round(longest_hold(pieces), 2),
            "first_image": sources[first]["shot_id"], "loudness": audio["loudness"],
            "loop_difference": round(loop_difference(sources, pieces, duration), 4) if loop else None,
            "sound": {"missing_sfx": audio["plan"].get("missing", []), "music": [m.get("file") for m in audio["plan"].get("music", [])]}}
