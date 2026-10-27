"""Layer 2 of the visual QA: focused one-word questions about crops (Phase 3.5-A.1).

The probe showed the local VLM (qwen3vl 4B) sees the image but cannot be
trusted with one big JSON answer, and its boxes are useless. So layer 1
(``deterministic``) finds people and faces, and the VLM only answers short
questions about each relevant crop with ``yes``, ``no`` or ``unclear``:

* face markers (glasses, beard, earrings, scars, tattoos…) on each main face;
  when the expected characters are of both genders each face is also asked
  "man or woman?" so a missing marker is attributed to the right person;
* blood, wounds or red marks on each main person (skipped when the narration
  states an injury);
* visible writing on the whole frame (when OCR is not installed);
* whether an uncertain detection is a person, and whether two faces with a
  borderline similarity show the same person (the two crops side by side).

Non-face markers (rings, clothing, hair styles) are not asked: on 4B crops the
answers were noise. ``unclear`` never blocks, it becomes an advisory issue.
"""
from __future__ import annotations

import re

from PIL import Image

from video.qa import base
from video.qa import deterministic as det

FACE_MARKER = re.compile(r"glass|spectacle|eyewear|monocle|beard|moustache|mustache|eye ?patch|earring|scar|"
                         r"tattoo|piercing", re.I)
WEARABLE = re.compile(r"glass|spectacle|eyewear|monocle|eye ?patch|earring|piercing", re.I)
INJURY_WORDS = re.compile(r"\b(blood\w*|bleed\w*|wound\w*|cuts?|bruis\w*|injur\w*|scratch\w*|scars?|"
                          r"red marks?|welts?)\b", re.I)
WOMAN = re.compile(r"\b(woman|girl|lady|female|mother|wife|daughter|she|her)\b", re.I)
MAN = re.compile(r"\b(man|boy|gentleman|male|father|husband|son|he|his)\b", re.I)
CROP_SIDE = 448
ONE_WORD = "Answer with one word: yes, no or unclear."


def gender(who: str) -> str:
    text = str(who or "")
    if WOMAN.search(text):
        return "woman"
    return "man" if MAN.search(text) else ""


def face_markers(character: dict) -> list[str]:
    return [marker for marker in character.get("markers") or [] if FACE_MARKER.search(marker)]


def crop(image: Image.Image, box, pad: float = 0.25, max_side: int = CROP_SIDE) -> Image.Image:
    width, height = image.size
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
    region = (max(0, int((x0 - dx) * width)), max(0, int((y0 - dy) * height)),
              min(width, int((x1 + dx) * width)), min(height, int((y1 + dy) * height)))
    piece = image.crop(region).convert("RGB")
    piece.thumbnail((max_side, max_side), Image.LANCZOS)
    return piece


def side_by_side(first: Image.Image, second: Image.Image, height: int = 256) -> Image.Image:
    first = first.resize((max(1, first.width * height // first.height), height))
    second = second.resize((max(1, second.width * height // second.height), height))
    canvas = Image.new("RGB", (first.width + second.width + 16, height), (255, 255, 255))
    canvas.paste(first, (0, 0))
    canvas.paste(second, (first.width + 16, 0))
    return canvas


def _marker_prompt(marker: str) -> str:
    verb = "wearing" if WEARABLE.search(marker) else "shown with"
    return f"Look at this person's face. Is this person {verb} {marker}? {ONE_WORD}"


def questions(image: Image.Image, detection: dict, expected: dict, *, ocr: bool = False) -> list[base.Question]:
    persons = detection["persons"]
    found = det.roles(detection, expected)
    main = found["main"]
    asked = []
    characters = expected.get("characters") or []
    genders = {gender(item.get("who", "")) for item in characters}
    mixed = len(genders - {""}) > 1
    markers = sorted({marker for item in characters for marker in face_markers(item)})
    for index in main:
        face = persons[index].get("face")
        if not face:
            continue
        face_crop = crop(image, face["box"], pad=0.45)
        if mixed and markers:
            asked.append(base.Question(f"gender:{index}", "gender", face_crop,
                                       "Is this a man or a woman? Answer with one word: man, woman or unclear.",
                                       choices=("man", "woman", "unclear"), meta={"person": index}))
        for marker in markers:
            asked.append(base.Question(f"marker:{index}:{marker}", "marker", face_crop, _marker_prompt(marker),
                                       meta={"person": index, "marker": marker}))
    if not INJURY_WORDS.search(expected.get("narration", "")):
        for index in main:
            asked.append(base.Question(
                f"injury:{index}", "injury", crop(image, persons[index]["box"], pad=0.05),
                f"Is there blood, a wound, a cut, a bruise or red marks on this person's skin? {ONE_WORD}",
                meta={"person": index}))
    if not ocr:
        whole = image.convert("RGB").copy()
        whole.thumbnail((768, 768), Image.LANCZOS)
        asked.append(base.Question("text", "text", whole,
                                   "Is there any visible writing, letters, numbers, a signature or a watermark "
                                   f"in this picture? {ONE_WORD}"))
    for number, item in enumerate(detection.get("unsure", [])):
        asked.append(base.Question(f"person:{number}", "person", crop(image, item["box"], pad=0.1),
                                   f"Does this picture show a person? {ONE_WORD}", meta={"unsure": number}))
    for pair in detection["face_pairs"]:
        if det.DUPLICATE_UNSURE <= pair["similarity"] < det.DUPLICATE_SIM:
            first, second = pair["persons"]
            if not (persons[first].get("face") and persons[second].get("face")):
                continue
            joined = side_by_side(crop(image, persons[first]["face"]["box"], pad=0.4),
                                  crop(image, persons[second]["face"]["box"], pad=0.4))
            asked.append(base.Question(f"same:{first}:{second}", "same_face", joined,
                                       f"These are two faces from one drawing. Do they show the same person? "
                                       f"{ONE_WORD}", meta={"persons": [first, second]}))
    return asked


def apply(asked: list[base.Question], answers: dict, detection: dict, expected: dict,
          issues: list[dict]) -> tuple[list[dict], int]:
    """Layer-1 issues plus what the answers add; returns (issues, people count)."""
    persons = detection["persons"]
    by_kind = {}
    for question in asked:
        by_kind.setdefault(question.kind, []).append(question)
    answer = lambda question: answers.get(question.qid, "unclear")
    found = det.roles(detection, expected)
    issues = list(issues)
    genders = {question.meta["person"]: answer(question) for question in by_kind.get("gender", [])}
    for marker in sorted({question.meta["marker"] for question in by_kind.get("marker", [])}):
        owners = [item for item in expected.get("characters") or [] if marker in face_markers(item)]
        replies = {question.meta["person"]: answer(question) for question in by_kind["marker"]
                   if question.meta["marker"] == marker}
        for sex in sorted({gender(item.get("who", "")) for item in owners}):
            needing = [item for item in owners if gender(item.get("who", "")) == sex]
            candidates = [index for index in replies if not genders or genders.get(index) in (sex, "unclear")
                          or not sex]
            if not candidates:
                continue
            wearing = [index for index in candidates if replies[index] == "yes"]
            missing = len(needing) - len(wearing)
            if missing <= 0:
                continue
            without = sorted((index for index in candidates if replies[index] == "no"),
                             key=lambda index: -base.area(persons[index]["box"]))
            target = needing[0]["id"] if len(needing) == 1 else ""
            for index in without[:missing]:
                issues.append(base.issue("missing_marker", box=persons[index]["face"]["box"], target=target,
                                         note=f"face without {marker}", source="local_vlm"))
            if len(without) < missing:
                issues.append(base.issue("missing_marker", severity="advisory", target=target,
                                         note=f"{marker}: unclear on the visible face(s)", source="local_vlm"))
    for question in by_kind.get("injury", []):
        reply = answer(question)
        if reply in ("yes", "unclear"):
            issues.append(base.issue("unstated_injury", severity="blocking" if reply == "yes" else "advisory",
                                     note="blood, wounds or red marks the narration does not state"
                                          + ("" if reply == "yes" else " (unclear)"), source="local_vlm"))
    for question in by_kind.get("text", []):
        reply = answer(question)
        if reply in ("yes", "unclear"):
            issues.append(base.issue("text", severity="blocking" if reply == "yes" else "advisory",
                                     note="visible writing or a watermark" + ("" if reply == "yes" else " (unclear)"),
                                     source="local_vlm"))
    allowed = expected["people"] + expected.get("background", 0)
    sure = len(found["main"]) + len(found["background"]) + len(found["extras"])
    confirmed = 0
    for question in by_kind.get("person", []):
        if answer(question) != "yes":
            continue
        confirmed += 1
        if sure + confirmed > allowed:
            box = detection["unsure"][question.meta["unsure"]]["box"]
            issues.append(base.issue("extra_person", severity="advisory" if expected.get("others_present")
                                     else "blocking", box=box, note="a small figure beyond the expected people",
                                     source="local_vlm"))
    for question in by_kind.get("same_face", []):
        reply = answer(question)
        if reply == "no":
            continue
        first, second = question.meta["persons"]
        copy = min((first, second), key=lambda index: base.area(persons[index]["box"]))
        box = persons[copy]["box"]
        issues = [item for item in issues if not (item["type"] == "extra_person" and item["box"] == box)]
        issues.append(base.issue("duplicate_person", severity="blocking" if reply == "yes" else "advisory",
                                 box=box, note="two faces look like the same person"
                                               + ("" if reply == "yes" else " (unclear)"), source="local_vlm"))
    return issues, len(persons) + confirmed
