"""Thumbnail variants (Phase 3.6-A): 3 concepts, each generated, graded, lettered and checked.

Evidence (episode 1): uniformly mid-grey, the text over the guests' faces, the
protagonist's head cropped, a "PRIVATE GRAVITY" wordmark taking space. Each
variant now:

1. is generated with the character references (``agents.packaging`` concept →
   a shot composed like every other, ``compose_shot``);
2. gets a **thumbnail-only grade**: midtones lifted, local contrast and clarity,
   a darker clean background, one saturated accent (Antique Gold or Dusty
   Crimson) glowing on the focal element;
3. gets ≤ 4 words of very large text (cap height ≥ ``CAP_HEIGHT`` of the frame)
   on a clean dark side with a soft shadow, never over a face;
4. is checked: text/background contrast ≥ 4.5 (WCAG), head inside the frame
   (layer-1 detector), text clear of faces; a 168×94 preview shows how it reads
   in a phone feed.

No wordmark (``THUMBNAIL_LOGO`` adds a tiny corner mark, default off). The user
picks the variant that is uploaded (``select``); the others are kept for
YouTube Studio's Test & compare (manual upload there — not available in the API).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

import config
from agents import packaging
from agents.narrative import atomic_write_json, load_json

TW, TH = 1280, 720
CAP_HEIGHT = 0.18                    # text cap height ≥ 18 % of the image height
MIN_CONTRAST = 4.5
MOBILE = (168, 94)
FOLDER = "thumbnails"
INDEX = "thumbnails.json"
ACCENTS = {"gold": "highlight", "crimson": "red"}


# ── Grading ──────────────────────────────────────────────────────────────────

def grade(image: Image.Image, focal_box=None, accent: str = "gold") -> Image.Image:
    """Thumbnail-only look: lifted midtones, local contrast, darker edges, one saturated accent."""
    image = image.convert("RGB")
    lifted = image.point(lambda value: round(255 * (value / 255) ** 0.82))           # midtones up
    clarity = lifted.filter(ImageFilter.UnsharpMask(radius=24, percent=60, threshold=2))
    crisp = clarity.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    graded = ImageEnhance.Contrast(crisp).enhance(1.12)
    width, height = graded.size
    # Darker clean background: a vignette that spares the focal element.
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy, rx, ry = width / 2, height / 2, width * 0.62, height * 0.75
    if focal_box:
        cx, cy = (focal_box[0] + focal_box[2]) / 2 * width, (focal_box[1] + focal_box[3]) / 2 * height
        rx = max(rx * 0.6, (focal_box[2] - focal_box[0]) * width * 1.1)
        ry = max(ry * 0.6, (focal_box[3] - focal_box[1]) * height * 1.1)
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(width, height) / 8))
    darker = ImageEnhance.Brightness(graded).enhance(0.55)
    graded = Image.composite(graded, darker, mask)
    # One saturated accent on the focal element: a warm glow in the brand accent colour.
    colour = config.COLORS[ACCENTS.get(accent, "highlight")]
    glow = Image.new("L", (width, height), 0)
    gx, gy, gr = cx, cy, max(40, min(rx, ry) * 0.55)
    ImageDraw.Draw(glow).ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(gr / 1.5))
    tint = ImageChops.screen(graded, Image.new("RGB", (width, height), colour))
    graded = Image.composite(tint, graded, glow)
    return ImageEnhance.Color(graded).enhance(1.08)


# ── Text ─────────────────────────────────────────────────────────────────────

def _font(size: int):
    for path in config.FONT_PATHS.get("bold", []):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def cap_height(font) -> int:
    box = font.getbbox("H")
    return box[3] - box[1]


def font_for_cap(target: int):
    """The bold font at the size whose capital H is at least ``target`` px tall."""
    size = target
    font = _font(size)
    while cap_height(font) < target and size < 600:
        size += 4
        font = _font(size)
    return font


def relative_luminance(colour) -> float:
    def channel(value):
        value = value / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = colour[:3]
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(first, second) -> float:
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _intersection(first, second) -> float:
    return max(0.0, min(first[2], second[2]) - max(first[0], second[0])) *         max(0.0, min(first[3], second[3]) - max(first[1], second[1]))


def _overlaps(first, second) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def layout_text(image: Image.Image, text: str, side: str, avoid: list | None = None) -> dict:
    """Place ``text`` (upper case) on ``side`` at cap height ≥ CAP_HEIGHT; darken behind it until the
    WCAG contrast is ≥ MIN_CONTRAST. Returns {"image", "box", "contrast", "cap_height", "side", "lines"}."""
    width, height = image.size
    font = font_for_cap(round(CAP_HEIGHT * height))
    draw = ImageDraw.Draw(image)
    words, lines = text.upper().split(), []
    max_width = int(width * 0.56)
    for word in words:                                           # wrap
        trial = " ".join(lines[-1:] + [word]) if lines else word
        if lines and draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            lines[-1] = trial
        else:
            lines.append(word)
    line_height = round(cap_height(font) * 1.32)
    block_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
    block_h = line_height * len(lines)
    margin = round(width * 0.045)
    sides = [side] + [other for other in packaging.SIDES if other != side]
    y = max(margin, (height - block_h) // 2)

    def placement(current):
        x = margin if current == "left" else width - margin - block_w
        box = [x / width, y / height, (x + block_w) / width, (y + block_h) / height]
        covered = sum(_intersection(box, face) for face in avoid or [])
        return covered, x, box

    # the concept's side unless it covers a face; then the side that covers the least face area
    options = [(placement(current), current) for current in sides]
    (covered, x, box), current = options[0] if options[0][0][0] == 0 else min(options, key=lambda item: item[0][0])
    colour = config.COLORS["white"]
    result = image.convert("RGB")
    strength = 0.55
    extent = ((x + block_w) / width if current == "left" else (width - x) / width) + 0.03
    for _ in range(8):
        shaded = _shade(result, current, strength, extent)
        region = shaded.crop((x, y, x + block_w, y + block_h))
        background = tuple(round(value) for value in ImageStat.Stat(region).mean)
        ratio = contrast_ratio(colour, background)
        if ratio >= MIN_CONTRAST or strength >= 0.95:
            break
        strength = min(0.95, strength + 0.1)
    draw = ImageDraw.Draw(shaded)
    shadow = Image.new("L", shaded.size, 0)
    shadow_draw = ImageDraw.Draw(shadow)
    for index, line in enumerate(lines):
        line_x = x if current == "left" else x + block_w - draw.textbbox((0, 0), line, font=font)[2]
        shadow_draw.text((line_x + 6, y + index * line_height + 8), line, font=font, fill=200,
                         anchor="la")
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    shaded = Image.composite(Image.new("RGB", shaded.size, (0, 0, 0)), shaded, shadow)
    draw = ImageDraw.Draw(shaded)
    for index, line in enumerate(lines):
        line_x = x if current == "left" else x + block_w - draw.textbbox((0, 0), line, font=font)[2]
        draw.text((line_x, y + index * line_height), line, font=font, fill=colour, anchor="la")
    return {"image": shaded, "box": [round(value, 4) for value in box], "contrast": round(ratio, 2),
            "cap_height": round(cap_height(font) / height, 3), "side": current, "lines": lines}


def _shade(image: Image.Image, side: str, strength: float, extent: float = 0.6) -> Image.Image:
    """A dark area on the text side: full ``strength`` over the text block (``extent`` of the width),
    then a soft fade over 18 % of the width (clean dark area, no hard edge)."""
    width, height = image.size
    fade = 0.18 * width

    def value(x):
        distance = x if side == "left" else width - 1 - x
        over = max(0.0, distance - extent * width)
        return round(255 * strength * max(0.0, 1 - over / fade) ** 1.5)
    row = [value(x) for x in range(width)]
    gradient = Image.new("L", (width, 1))
    gradient.putdata(row)
    gradient = gradient.resize((width, height))
    return Image.composite(Image.new("RGB", (width, height), config.COLORS["background"]), image, gradient)


def logo_mark(image: Image.Image) -> Image.Image:
    """Optional tiny corner mark (THUMBNAIL_LOGO), instead of the old wordmark."""
    font = _font(max(12, image.height // 40))
    draw = ImageDraw.Draw(image)
    text = "".join(word[0] for word in config.CHANNEL_NAME.split()).upper()
    draw.text((image.width - 24, image.height - 20), text, font=font, fill=config.COLORS["light"], anchor="rs")
    return image


# ── Checks ───────────────────────────────────────────────────────────────────

def head_in_frame(detection: dict | None) -> bool | None:
    """The largest person's head (face box, else the top of the person box) is fully inside the frame."""
    if not detection or not detection.get("persons"):
        return None
    person = max(detection["persons"], key=lambda item: (item["box"][2] - item["box"][0]) * (item["box"][3] - item["box"][1]))
    face = (person.get("face") or {}).get("box")
    if face:
        return face[1] > 0.015 and face[0] > 0.005 and face[2] < 0.995
    return person["box"][1] > 0.015


def checks(layout: dict, detection: dict | None) -> dict:
    faces = [person["face"]["box"] for person in (detection or {}).get("persons", []) if person.get("face")]
    found = {"contrast": layout["contrast"], "contrast_ok": layout["contrast"] >= MIN_CONTRAST,
             "cap_height": layout["cap_height"], "cap_height_ok": layout["cap_height"] >= CAP_HEIGHT - 0.005,
             "text_clear_of_faces": not any(_overlaps(layout["box"], face) for face in faces),
             "head_in_frame": head_in_frame(detection)}
    found["passed"] = all(value is not False for key, value in found.items()
                          if key.endswith("_ok") or key in ("text_clear_of_faces", "head_in_frame"))
    return found


def mobile_preview(image: Image.Image, path) -> str:
    image.resize(MOBILE, Image.LANCZOS).save(path, "PNG")
    return str(path)


# ── Variants ─────────────────────────────────────────────────────────────────

def concept_shot(concept: dict, vb: dict, brief: dict, shot_list: dict | None = None) -> dict:
    """A concept as a shot for ``compose_shot``: protagonist (+ the other character for two_person)."""
    from video import shot_generation as gen
    base = gen.thumbnail_shot(vb, brief, shot_list)
    protagonist = base["characters"][:1]
    characters = protagonist + ([concept["other_character"]] if concept["composition"] == "two_person"
                                and concept.get("other_character") else [])
    names = {item["character_id"]: item.get("descriptor", "the protagonist") for item in vb.get("characters", [])}
    template = packaging.COMPOSITIONS[concept["composition"]][1]
    action = template.format(protagonist=f"the {names.get(protagonist[0], 'protagonist').split(',')[0]}" if protagonist
                             else "the protagonist",
                             other=names.get(concept.get("other_character", ""), "the other character").split(",")[0],
                             emotion=concept["emotion"], focal_element=concept["focal_element"],
                             text_side=concept["text_side"])
    shot_type = {"big_face": "close_up", "small_vs_presence": "wide", "telling_object": "insert",
                 "two_person": "medium"}[concept["composition"]]
    return {**base, "characters": characters, "action": action, "shot_type": shot_type,
            "others_present": concept["composition"] == "small_vs_presence"}


def render_variant(background: Image.Image, concept: dict, detection: dict | None, out_path,
                   accent: str = "gold") -> dict:
    """Grade, letter and check one generated background; writes the JPEG and its mobile preview."""
    image = background.convert("RGB").resize((TW, TH), Image.LANCZOS)
    people = (detection or {}).get("persons", [])
    focal = max((person["box"] for person in people), default=None,
                key=lambda box: (box[2] - box[0]) * (box[3] - box[1]))
    if concept["composition"] == "big_face":
        faces = [person["face"]["box"] for person in people if person.get("face")]
        focal = faces[0] if faces else focal
    graded = grade(image, focal, accent)
    faces = [person["face"]["box"] for person in people if person.get("face")]
    layout = layout_text(graded, concept["text"], concept["text_side"], avoid=faces or ([focal] if focal else []))
    final = logo_mark(layout["image"]) if getattr(config, "THUMBNAIL_LOGO", False) else layout["image"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path, "JPEG", quality=94)
    preview = mobile_preview(final, out_path.with_name(out_path.stem + "_mobile.png"))
    return {"path": str(out_path), "preview": preview, "checks": checks(layout, detection),
            "text_box": layout["box"], "side": layout["side"], "accent": accent}


def generate_variants(output_dir, generator, *, concepts: dict | None = None, detector=None, qa=None,
                      progress: Callable[[str], None] = print, generator_concepts=None) -> dict:
    """Concepts (Gemini, cached) → one ComfyUI background each with the references → graded,
    lettered, checked variants in thumbnails/. Variant 1 is selected until the user picks."""
    from agents import shot_list as shots_module
    from agents import visual_bible as vbible
    from video import shot_generation as gen
    from video.imagegen_comfyui import ImageJob
    output = Path(output_dir)
    vb = load_json(output / vbible.FILE_NAME) or {}
    brief = load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    script = load_json(output / "script.json") or {}
    shot_list = load_json(output / shots_module.FILE_NAME)
    if concepts is None:
        concepts = packaging.generate_thumbnail_concepts(output, brief=brief, script=script, vb=vb,
                                                         generator=generator_concepts)
    if detector is None:
        from video.qa import fixes
        detector = fixes._detector()
    previous = load_json(output / FOLDER / INDEX) or {}
    (output / FOLDER).mkdir(parents=True, exist_ok=True)
    variants = []
    for number, concept in enumerate(concepts["concepts"], 1):
        shot = concept_shot(concept, vb, brief, shot_list)
        if shot["characters"] and not vbible.approved_reference(vb, "character", shot["characters"][0], output):
            raise gen.ApprovalRequired(f"Approve {shot['characters'][0]}'s character sheet before the thumbnails.")
        composed = shots_module.compose_shot(shot, vb, output)
        background = output / FOLDER / f"background_{number}.jpg"
        job = ImageJob(job_id=f"thumbnail_v{number}", prompt=composed["prompt"], seed=composed["seed"] + 7919 * number,
                       output_path=str(background), kind=composed["kind"], references=composed["references"],
                       size=gen.native_size(), final_size=(TW, TH))
        progress(f"   → Thumbnail {number}/{len(concepts['concepts'])}: {concept['composition']} — {concept['text']!r}")
        record = generator.generate_with_retry(job, new_seeds=1)
        detection = detector.detect(background) if detector else None
        with Image.open(background) as opened:
            variant = render_variant(opened, concept, detection, output / FOLDER / f"variant_{number}.jpg",
                                     accent="crimson" if number == 2 else "gold")
        variant.update(number=number, concept=concept, seed=record.get("seed"), prompt=composed["prompt"],
                       background=str(background.relative_to(output)))
        variant["path"] = str(Path(variant["path"]).relative_to(output))
        variant["preview"] = str(Path(variant["preview"]).relative_to(output))
        variants.append(variant)
    if qa is not None:                                   # the existing QA, advisory: hints only
        results = qa.review_batch([(output / item["background"], _expected(vb, item["concept"])) for item in variants])
        for item, result in zip(variants, results):
            item["qa"] = {"issues": result.get("issues", []), "layers": result.get("layers", [])}
    index = {"variants": variants, "selected": previous.get("selected") if previous.get("selected") in
             range(1, len(variants) + 1) else 1, "concepts_key": concepts.get("key")}
    atomic_write_json(output / FOLDER / INDEX, index)
    select(output, index["selected"])
    return index


def _expected(vb: dict, concept: dict) -> dict:
    from agents import visual_bible as vbible
    characters = vbible.on_screen(vb)[:1]
    if concept["composition"] == "two_person":
        characters += [item for item in vbible.on_screen(vb) if item["character_id"] == concept.get("other_character")]
    return {"people": len(characters), "background": 0, "others_present": concept["composition"] == "small_vs_presence",
            "characters": [{"id": item["character_id"], "who": item.get("descriptor", ""),
                            "markers": vbible.signature_markers(vb, item["character_id"])} for item in characters],
            "narration": ""}


def select(output_dir, number: int) -> str:
    """The chosen variant becomes thumbnail.jpg (the one the API uploads)."""
    output = Path(output_dir)
    index = load_json(output / FOLDER / INDEX) or {}
    variant = next((item for item in index.get("variants", []) if item["number"] == int(number)), None)
    if variant is None or not (output / variant["path"]).is_file():
        raise FileNotFoundError(f"no thumbnail variant {number}")
    shutil.copy2(output / variant["path"], output / "thumbnail.jpg")
    index["selected"] = int(number)
    atomic_write_json(output / FOLDER / INDEX, index)
    return str(output / "thumbnail.jpg")
