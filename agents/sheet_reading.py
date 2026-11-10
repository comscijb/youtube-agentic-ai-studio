"""Text that agrees with the approved sheet (Phase 3.9.8-B).

The visual-bible descriptor is written before any sheet exists. When the approved face sheet shows something else
("long straight red hair, brown eyes" against "shoulder-length auburn hair, hazel eyes"), every prompt pulls the model
two ways. One Gemini vision call reads the approved image; ``differences`` compares the reading with the descriptor and
the signature markers; the user accepts or edits in the GUI; ``apply`` updates the descriptor and keeps both values with
their provenance. A marker that contradicts the sheet is recorded in ``marker_conflicts`` and never reaches a prompt.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

STAGE = "sheet_reading"
FIELDS = ("hair_length", "hair_colour", "hair_style", "eye_colour", "age_range", "build", "distinctive")
HAIR_COLOURS = {"red": ("red", "auburn", "ginger", "copper", "strawberry"), "brown": ("brown", "chestnut", "brunette"),
                "black": ("black", "raven", "jet"), "blond": ("blond", "blonde", "golden", "fair", "flaxen"),
                "grey": ("grey", "gray", "silver", "white")}
EYE_COLOURS = {"brown": ("brown", "dark brown", "amber"), "hazel": ("hazel",), "blue": ("blue",), "green": ("green",),
               "grey": ("grey", "gray")}
LENGTHS = (("short", r"\b(?:short|cropped|pixie|buzz(?:ed|cut)?)\b"), ("bob", r"\b(?:bob|chin-length|jaw-length)\b"),
           ("shoulder", r"\bshoulder[- ]length\b"), ("long", r"\b(?:long|past (?:the|her|his) shoulders|waist-length)\b"))
PT = {"red": "ruivo", "brown": "castanho", "black": "preto", "blond": "loiro", "grey": "grisalho", "hazel": "avelã", "blue": "azul",
      "green": "verde", "short": "curto", "bob": "chanel", "shoulder": "na altura dos ombros", "long": "longo"}


def prompt() -> str:
    return ("You read an approved character reference sheet from an illustrated film. Describe ONLY what is visible. "
            "Return ONLY JSON with: hair_length (short | bob | shoulder | long), hair_colour (one or two words), hair_style "
            "(a few words), eye_colour (one word, or \"not visible\"), age_range (e.g. \"45-50\"), build (one or two words), "
            "distinctive (an array of at most 4 short visible features, e.g. \"deep lines around the mouth\").")


def read_sheet(path, *, generator=None, model: str | None = None) -> dict:
    """One vision call on the approved image: {field: value} plus "provenance"."""
    from agents.gemini_client import generate
    from agents.narrative import extract_json
    buffer = io.BytesIO()
    with Image.open(path) as image:
        image.convert("RGB").save(buffer, "JPEG", quality=90)
    raw = (generator or generate)(prompt(), model, stage=STAGE, temperature=0.0, response_mime_type="application/json",
                                  images=[(buffer.getvalue(), "image/jpeg")])
    value = extract_json(raw, "sheet reading")
    reading = {key: value.get(key) for key in FIELDS if value.get(key) not in (None, "")}
    reading["provenance"] = {"source": "gemini-vision", "image": Path(path).name,
                             "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    return reading


def _family(text: str, families: dict) -> str | None:
    lowered = str(text or "").lower()
    for name, words in families.items():
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words):
            return name
    return None


def _length(text: str) -> str | None:
    lowered = str(text or "").lower()
    for name, pattern in LENGTHS:
        if re.search(pattern, lowered):
            return name
    return None


def _hair_part(descriptor: str) -> str:
    """The descriptor's clause about hair (where the colour and length words live)."""
    return next((part for part in re.split(r",|;| and ", str(descriptor or "")) if "hair" in part.lower()), "")


def _age(descriptor: str) -> int | None:
    found = re.search(r"\b(\d{1,2})[- ]year[- ]old\b", str(descriptor or ""))
    return int(found.group(1)) if found else None


def differences(descriptor: str, markers: list[str], reading: dict) -> list[dict]:
    """[{field, descriptor, sheet, message}] where the descriptor or a marker says something the sheet does not show."""
    found = []
    hair = _hair_part(descriptor)
    sheet_colour = _family(reading.get("hair_colour", ""), HAIR_COLOURS)
    text_colour = _family(hair, HAIR_COLOURS)
    if sheet_colour and text_colour and sheet_colour != text_colour:
        found.append({"field": "hair_colour", "descriptor": text_colour, "sheet": reading.get("hair_colour"),
                      "message": f"cabelo — descritor: {PT.get(text_colour, text_colour)} / folha: {reading.get('hair_colour')}"})
    sheet_length, text_length = reading.get("hair_length"), _length(hair)
    if sheet_length in PT and text_length and sheet_length != text_length:
        found.append({"field": "hair_length", "descriptor": text_length, "sheet": sheet_length,
                      "message": f"comprimento — descritor: {PT[text_length]} / folha: {PT[sheet_length]}"})
    sheet_eyes = _family(reading.get("eye_colour", ""), EYE_COLOURS)
    text_eyes = _family(next((part for part in str(descriptor).split(",") if "eye" in part.lower()), ""), EYE_COLOURS)
    if sheet_eyes and text_eyes and sheet_eyes != text_eyes:
        found.append({"field": "eye_colour", "descriptor": text_eyes, "sheet": reading.get("eye_colour"),
                      "message": f"olhos — descritor: {PT.get(text_eyes, text_eyes)} / folha: {reading.get('eye_colour')}"})
    age = _age(descriptor)
    span = [int(value) for value in re.findall(r"\d{1,2}", str(reading.get("age_range", "")))]
    if age and span and not (min(span) - 3 <= age <= max(span) + 3):
        found.append({"field": "age", "descriptor": age, "sheet": reading.get("age_range"),
                      "message": f"idade — descritor: {age} / folha: {reading.get('age_range')}"})
    for marker in markers or []:
        colour, length = _family(marker, HAIR_COLOURS), _length(marker)
        if "hair" in marker.lower() and ((colour and sheet_colour and colour != sheet_colour) or
                                         (length and sheet_length in PT and length != sheet_length)):
            found.append({"field": "marker", "descriptor": marker, "sheet": f"{reading.get('hair_length', '')} {reading.get('hair_colour', '')}".strip(),
                          "message": f"marcador \"{marker}\" não confere com a folha"})
    return found


def updated_descriptor(descriptor: str, reading: dict, accepted: list[str]) -> str:
    """The descriptor with the accepted fields rewritten from the sheet. Accepting the hair colour or length rebuilds
    the whole hair clause from the sheet (it is the authority once approved)."""
    text = str(descriptor or "")
    hair = _hair_part(text)
    if hair and {"hair_colour", "hair_length"} & set(accepted):
        length = {"short": "short", "bob": "bob-length", "shoulder": "shoulder-length", "long": "long"}.get(reading.get("hair_length", ""), "")
        style = str(reading.get("hair_style", "")).replace("hair", "").strip()
        clause = " ".join(part for part in (length, style, str(reading.get("hair_colour", "")).strip(), "hair") if part)
        leading = re.match(r"\s*", hair).group(0)
        text = text.replace(hair, leading + re.sub(r"\s+", " ", clause), 1)
    if "eye_colour" in accepted and reading.get("eye_colour"):
        text = re.sub(r"\b(?:hazel|dark brown|brown|blue|green|grey|gray|amber)\s+eyes\b", f"{reading['eye_colour']} eyes", text,
                      count=1, flags=re.I)
    if "age" in accepted:
        span = [int(value) for value in re.findall(r"\d{1,2}", str(reading.get("age_range", "")))]
        if span:
            text = re.sub(r"\b\d{1,2}([- ]year[- ]old)\b", lambda match: f"{round(sum(span) / len(span))}{match.group(1)}", text, count=1)
    return text


def apply(vb: dict, character_id: str, reading: dict, accepted: list[str], *, descriptor: str | None = None) -> dict:
    """Accept differences: the new descriptor (``descriptor`` when the user edited it), the old one kept with provenance,
    the contradicting markers recorded so prompts skip them."""
    entry = next(item for item in vb.get("characters", []) if item.get("character_id") == character_id)
    old = entry.get("descriptor", "")
    new = descriptor if descriptor is not None else updated_descriptor(old, reading, accepted)
    entry.setdefault("descriptor_history", []).append({"descriptor": old, "source": entry.get("descriptor_source", "visual bible"),
                                                       "replaced_at": reading.get("provenance", {}).get("at")})
    entry["descriptor"] = new
    entry["descriptor_source"] = "reconciled with the approved sheet"
    entry["sheet_reading"] = reading
    if "marker" in accepted:
        conflicts = [item["descriptor"] for item in differences(old, entry.get("signature_markers") or [], reading) if item["field"] == "marker"]
        entry["marker_conflicts"] = sorted(set(entry.get("marker_conflicts") or []) | set(conflicts))
    return entry
