"""Reliable region edits: crop → edit → composite (Phase 3.5.3).

Evidence (shot s01_06, 19 versions): a box ~3 % of the frame wide is 2–3 cells of
Klein's /16 latent, so ``<obj_1>`` cannot be grounded and "remove the figure"
fell on the most salient figure — Elena — while the silhouette stayed; and the
edit redrew the whole frame. Now every boxed edit:

1. **crops** a window around the box(es): the box enlarged ×``CONTEXT`` on its narrow
   side (×``SPAN`` at least on the other), at least ``MIN_FRACTION`` of the frame's
   shorter side, 16:9, clamped to the image; the
   crop is upscaled to ~1 MP and the boxes re-expressed in crop coordinates, so
   the target is now large enough to ground;
2. edits **the crop only** (Klein region edit, same template);
3. **composites** the edited crop back through a feathered mask equal to the box
   expanded by ``GROW`` — every pixel outside the mask is the source pixel, so a
   character outside the box cannot change, by construction — after matching
   the edit's colour/tone to the source in a ring around the mask;
4. **verifies**: outside the mask identical (asserted), inside changed by more
   than ``MIN_CHANGE`` (else "edição sem efeito", nothing saved), and, when the
   person detector is available, fewer people inside the mask for a removal.

Instruction-only edits (no box) keep the whole-image path. Both get explicit
keep clauses (``keep_clause``); figure removals name their target from its
position and look (``describe_figure``).
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from agents import visual_bible as vbible
from video.qa import base

CONTEXT = 3.0              # crop = box enlarged ×3 on its narrow side …
SPAN = 1.5                 # … at least ×1.5 on the other (the grown mask and some context fit) …
MIN_FRACTION = 0.25        # … and at least 25 % of the shorter side of the frame
RATIO = 16 / 9
CROP_PIXELS = 1024 * 1024  # the crop is edited at ~1 MP
GROW = 0.15                # mask = box expanded by 15 % (of the box size, per side)
FEATHER = 0.02             # feather radius, fraction of the crop side
RING = 0.06                # tone-match ring width, fraction of the crop side
MIN_CHANGE = 6.0           # mean abs difference (0-255) inside the mask below which nothing happened
MAX_CROP_FRACTION = 0.8    # a crop wider than this is no better than the whole frame
OVERLAP_WARN = 0.20        # a box covering > 20 % of a main character's box gets a warning


class NoEffect(RuntimeError):
    """The edit did not change the masked region."""


def union(boxes: list) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def crop_window(box, size) -> tuple[int, int, int, int]:
    """Pixel rectangle around the normalized ``box``, 16:9, shifted inside the image: CONTEXT× the box
    on its narrow side, at least SPAN× it on the other (room for the grown mask), and at least
    MIN_FRACTION of the frame's shorter side. A standing figure is tall and thin, so its width is
    what grows: the 3 %-wide doorway silhouette of s01_06 becomes ~7 % of the edited canvas."""
    width, height = size
    x0, y0, x1, y1 = box[0] * width, box[1] * height, box[2] * width, box[3] * height
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bw, bh = x1 - x0, y1 - y0
    w = max(bw * CONTEXT if bw / RATIO <= bh else bw * SPAN, bh * (SPAN if bw / RATIO <= bh else CONTEXT) * RATIO,
            MIN_FRACTION * min(width, height) * RATIO)
    w = min(w, width, height * RATIO)
    h = w / RATIO
    left = min(max(cx - w / 2, 0), width - w)
    top = min(max(cy - h / 2, 0), height - h)
    return round(left), round(top), round(left + w), round(top + h)


def to_crop(box, crop, size) -> list[float]:
    """``box`` (normalized to the frame) in normalized crop coordinates."""
    width, height = size
    left, top, right, bottom = crop
    cw, ch = right - left, bottom - top
    return [round(min(max((box[0] * width - left) / cw, 0.0), 1.0), 4),
            round(min(max((box[1] * height - top) / ch, 0.0), 1.0), 4),
            round(min(max((box[2] * width - left) / cw, 0.0), 1.0), 4),
            round(min(max((box[3] * height - top) / ch, 0.0), 1.0), 4)]


def edit_size(crop) -> tuple[int, int]:
    """The ~1 MP size the crop is edited at (multiples of 16, the crop's ratio)."""
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    scale = (CROP_PIXELS / (cw * ch)) ** 0.5
    return max(16, round(cw * scale / 16) * 16), max(16, round(ch * scale / 16) * 16)


def mask_for(boxes: list, crop, size) -> tuple[Image.Image, Image.Image, Image.Image]:
    """(feathered mask, hard mask, tone ring) in crop pixels. The hard mask is every box expanded by
    GROW; the feather fades inwards so the soft edge stays inside it; the ring is a band just outside."""
    width, height = size
    left, top, right, bottom = crop
    cw, ch = right - left, bottom - top
    feather = max(1, round(FEATHER * max(cw, ch)))
    band = max(2, round(RING * max(cw, ch)))
    hard, inner, outer = (Image.new("L", (cw, ch), 0) for _ in range(3))
    for box in boxes:
        bw, bh = (box[2] - box[0]) * width, (box[3] - box[1]) * height
        rect = [box[0] * width - left - bw * GROW, box[1] * height - top - bh * GROW,
                box[2] * width - left + bw * GROW, box[3] * height - top + bh * GROW]
        ImageDraw.Draw(hard).rectangle([round(v) for v in rect], fill=255)
        shrunk = [rect[0] + feather, rect[1] + feather, rect[2] - feather, rect[3] - feather]
        if shrunk[2] > shrunk[0] and shrunk[3] > shrunk[1]:
            ImageDraw.Draw(inner).rectangle([round(v) for v in shrunk], fill=255)
        ImageDraw.Draw(outer).rectangle([round(rect[0] - band), round(rect[1] - band),
                                         round(rect[2] + band), round(rect[3] + band)], fill=255)
    soft = ImageChops.multiply(inner.filter(ImageFilter.GaussianBlur(feather / 2)), hard) if inner.getbbox() else hard
    ring = ImageChops.subtract(outer, hard)
    return soft, hard, ring


def tone_match(edited: Image.Image, source: Image.Image, ring: Image.Image) -> Image.Image:
    """Per-channel mean/std of ``edited`` matched to ``source`` in ``ring`` (seams and colour casts)."""
    if not ring.getbbox():
        return edited
    want, have = ImageStat.Stat(source, mask=ring), ImageStat.Stat(edited, mask=ring)
    bands = []
    for band, mean_s, std_s, mean_e, std_e in zip(edited.split(), want.mean, want.stddev, have.mean, have.stddev):
        gain = min(max(std_s / std_e if std_e > 1e-3 else 1.0, 0.7), 1.4)
        offset = mean_s - mean_e * gain
        bands.append(band.point(lambda value, gain=gain, offset=offset: max(0, min(255, round(value * gain + offset)))))
    return Image.merge("RGB", bands)


def composite(source: Image.Image, edited_crop: Image.Image, crop, soft: Image.Image,
              ring: Image.Image | None = None) -> Image.Image:
    """The source with the edited crop pasted through the feathered mask (tone-matched in ``ring``)."""
    left, top, right, bottom = crop
    region = source.crop(crop)
    edited = edited_crop.convert("RGB").resize((right - left, bottom - top), Image.LANCZOS)
    if ring is not None:
        edited = tone_match(edited, region, ring)
    blended = Image.composite(edited, region, soft)
    result = source.copy()
    result.paste(blended, (left, top))
    return result


def check(source: Image.Image, result: Image.Image, crop, hard: Image.Image) -> float:
    """Assert nothing changed outside the mask; return the mean abs difference inside it."""
    outside = Image.new("L", source.size, 255)
    outside.paste(ImageChops.invert(hard), crop[:2])
    diff = ImageChops.difference(source, result).convert("L")
    leaked = ImageChops.multiply(diff, outside).getbbox()
    if leaked:
        raise AssertionError(f"composite changed pixels outside the mask at {leaked}")
    inside = Image.new("L", source.size, 0)
    inside.paste(hard, crop[:2])
    return ImageStat.Stat(diff, mask=inside).mean[0] if inside.getbbox() else 0.0


def persons_inside(detection: dict, boxes: list, size=None) -> int:
    """People whose box centre lies inside one of the (expanded) edit boxes."""
    count = 0
    for person in detection.get("persons", []):
        x0, y0, x1, y1 = person["box"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for box in boxes:
            bw, bh = (box[2] - box[0]) * GROW, (box[3] - box[1]) * GROW
            if box[0] - bw <= cx <= box[2] + bw and box[1] - bh <= cy <= box[3] + bh:
                count += 1
                break
    return count


# ── B: descriptive targets and keep clauses ──────────────────────────────────

def describe_figure(image: Image.Image, box) -> str:
    """"the small dark silhouette standing far back against the bright background on the right" — from the
    box's size, position and its luminance against the surroundings."""
    width, height = image.size
    area = base.area(box)
    size = "small " if area < 0.02 else ("" if area < 0.12 else "large ")
    grey = image.convert("L")
    pixels = [round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height)]
    inner = ImageStat.Stat(grey.crop(pixels)).mean[0]
    pad_x, pad_y = (pixels[2] - pixels[0]), (pixels[3] - pixels[1])
    around = grey.crop((max(0, pixels[0] - pad_x), max(0, pixels[1] - pad_y // 2),
                        min(width, pixels[2] + pad_x), min(height, pixels[3] + pad_y // 2)))
    surround = ImageStat.Stat(around).mean[0]
    if inner < surround - 25:
        look, behind = "dark silhouette", " against the bright background"
    elif inner > surround + 25:
        look, behind = "pale figure", " against the dark background"
    else:
        look, behind = "figure", ""
    centre = (box[0] + box[2]) / 2
    side = "on the left" if centre < 1 / 3 else ("on the right" if centre > 2 / 3 else "in the middle")
    depth = " far back" if area < 0.05 else ""
    return f"the {size}{look} standing{depth}{behind} {side}"


def keep_clause(vb: dict, shot: dict, character_ids: list | None = None) -> str:
    """"Keep the woman in the ivory silk shirt and the man in the charcoal tailored suit exactly as they
    are" for the listed characters (all of them, or ``character_ids``)."""
    from agents import shot_list as shots_module
    ids = list(character_ids if character_ids is not None else shot.get("characters") or [])
    people = []
    for cid in ids:
        entry = vbible.character(vb, cid)
        if not entry:
            continue
        noun = shots_module._noun(entry.get("descriptor", "")) or "person"
        outfit = vbible.wardrobe(vb, cid, shot.get("scene_id", ""))
        people.append(f"the {noun}" + (f" in the {_strip_article(outfit)}" if outfit else ""))
    if not people:
        return ""
    joined = people[0] if len(people) == 1 else ", ".join(people[:-1]) + " and " + people[-1]
    return f"Keep {joined} exactly as they are."


def _strip_article(text: str) -> str:
    text = str(text).strip()
    for article in ("a ", "an ", "the "):
        if text.lower().startswith(article):
            return text[len(article):]
    return text


def overlap_warnings(detection: dict, shot: dict, box) -> list[dict]:
    """Main characters whose detected box is covered > OVERLAP_WARN by the user's box. The pairing
    of characters to detected people is a guess, so every candidate is named when it is ambiguous."""
    listed = list(shot.get("characters") or [])
    people = sorted(detection.get("persons", []), key=lambda person: -base.area(person["box"]))[:len(listed)]
    found = []
    for person in people:
        covered = _intersection(person["box"], box) / max(base.area(person["box"]), 1e-9)
        if covered > OVERLAP_WARN:
            found.append({"box": person["box"], "covered": round(covered, 2),
                          "candidates": listed if len(listed) > 1 else listed[:1]})
    return found


def _intersection(first, second) -> float:
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)
