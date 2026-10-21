"""Episode thumbnail (1280x720): the validated thumbnail_text over the strongest human shot.

The title is never repeated on the thumbnail: the thumbnail carries the short
inner-voice line chosen and validated in Phase 2.2 (<= 5 words, e.g. "Are they
watching?"). At most one word is highlighted, and only when the brief names it
(``thumbnail_highlight_word``). The background prefers the episode image with
the strongest human/emotional focal point according to its tags.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from agents.narrative import validate_thumbnail_text

TW, TH = 1280, 720
HUMAN_TERMS = {"man", "woman", "face", "portrait", "eyes", "eye", "hand", "hands", "silhouette",
               "person", "profile", "lonely", "alone", "sad", "sadness", "tears", "crying",
               "anxiety", "fear", "shadow", "mirror", "reflection", "despair", "thinking"}
CHEERFUL_TERMS = {"smile", "smiling", "happy", "fun", "party", "celebration", "cheers", "beach"}


def thumbnail_text(script: dict, brief: dict | None = None) -> str:
    return str(script.get("thumbnail_text") or (brief or {}).get("thumbnail_text") or "").strip()


def _first_path(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def choose_background(image_map: dict, output_dir: str) -> str | None:
    """Most human/emotional image by manifest tags; falls back to the first image."""
    paths = [path for value in image_map.values()
             for path in (value if isinstance(value, list) else [value]) if path]
    paths = [path for path in paths if os.path.exists(path)]
    if not paths:
        return None
    try:
        manifest = json.loads((Path(output_dir) / "asset_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    tags_by_name = {asset.get("downloaded_filename"): str(asset.get("tags", "")).lower()
                    for asset in manifest.get("assets", [])}

    def score(path):
        words = set(re.findall(r"[a-z]+", tags_by_name.get(Path(path).name, "")))
        return len(words & HUMAN_TERMS) - 3 * len(words & CHEERFUL_TERMS)

    ranked = sorted(paths, key=lambda path: -score(path))
    return ranked[0] if score(ranked[0]) > 0 else (_first_path(image_map.get(1)) or paths[0])


def _font(variant: str, size: int):
    for path in config.FONT_PATHS.get(variant, config.FONT_PATHS["regular"]):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=font)[2] > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _shadow(draw, xy, text, font, fill, radius=5):
    x, y = xy
    for dx in range(-radius, radius + 1, 2):
        for dy in range(-radius, radius + 1, 2):
            if abs(dx) + abs(dy) >= radius:
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def make_thumbnail(script: dict, image_map: dict, output_dir: str,
                   brief: dict | None = None) -> str:
    colors = config.COLORS
    text = thumbnail_text(script, brief) or str(script.get("title", ""))
    problems = validate_thumbnail_text(text, brief or {
        "psychological_theme": script.get("psychological_theme", "")})
    if problems:
        print(f"   ⚠ Thumbnail text {text!r}: " + "; ".join(item["claim"] for item in problems))
    highlight = str((brief or {}).get("thumbnail_highlight_word")
                    or script.get("thumbnail_highlight_word") or "").strip().lower()

    background = choose_background(image_map, output_dir)
    if background:
        image = Image.open(background).convert("RGB")
        # Enlarge and crop from the left so the photo's (usually central)
        # subject sits in the right third, clear of the text block.
        scale = max(TW / image.width, TH / image.height) * 1.3
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        left = max(0, min(image.width - TW, image.width // 2 - int(TW * 0.70)))
        top = (image.height - TH) // 2
        image = image.crop((left, top, left + TW, top + TH))
    else:
        image = Image.new("RGB", (TW, TH), colors["background"])

    # Left-to-right darkening keeps the text side legible and the subject visible.
    overlay = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for x in range(TW):
        od.line([(x, 0), (x, TH)], fill=(0, 0, 10, int(215 * max(0.0, 1 - x / (TW * 0.75)))))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    size = 120
    max_width = int(TW * 0.47)
    font = _font("bold", size)
    lines = _wrap(draw, text.upper(), font, max_width)
    while (len(lines) > 3 or any(draw.textbbox((0, 0), line, font=font)[2] > max_width
                                  for line in lines)) and size > 72:
        size -= 8
        font = _font("bold", size)
        lines = _wrap(draw, text.upper(), font, max_width)
    line_height = int(size * 1.08)
    top = (TH - line_height * len(lines)) // 2
    for index, line in enumerate(lines):
        x = 70
        for word in line.split():
            key = re.sub(r"[^a-z']", "", word.lower())
            fill = colors["highlight"] if highlight and key == highlight else colors["white"]
            _shadow(draw, (x, top + index * line_height), word, font, fill, radius=6)
            x += draw.textbbox((0, 0), word + " ", font=font)[2]
    draw.rectangle([70, top - 28, 230, top - 20], fill=colors["accent"])
    _shadow(draw, (70, TH - 70), config.CHANNEL_NAME.upper(), _font("bold", 30),
            colors["light"], radius=3)

    out = os.path.join(output_dir, "thumbnail.jpg")
    image.save(out, "JPEG", quality=95)
    return out
