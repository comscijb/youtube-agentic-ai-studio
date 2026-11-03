"""Deterministic prose and chronology checks on a screenplay (Phase 3.9.2).

* **Chronology:** a scene opening the same time marker twice; a scene mentioning an event or a walk
  to a place the outline plans for a later scene ("At supper…" in the afternoon scene).
* **Screen directions:** the narrator never describes the edit ("the story cuts to black").
* **Lecture lines:** first-person-plural or abstract moralising. Major in the last two scenes, minor
  elsewhere; a line the brief authorises as narrated is exempt.
* **Signature-marker leakage:** a character's wardrobe marker in more than 40 % of their scenes, or a
  coat at an indoor table. Markers belong to the visual bible and the shot prompts.

Speech in double quotes is never linted for directions or lectures: characters may say anything.
"""
from __future__ import annotations

import re

from agents.quotes import without_speech

TIME_MARKER = re.compile(
    r"\b(?:the next (?:morning|day)|next (?:morning|day)|that (?:morning|afternoon|evening|night)|"
    r"in the (?:morning|afternoon|evening)|at (?:dawn|dusk|night|midnight)|by (?:dawn|nightfall)|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|later)\b", re.I)
OPENER = re.compile(
    r"(?:^|(?<=[.!?…])[\"'”’]?\s+|\n\s*)(?:the )?(next morning|next day|that morning|that afternoon|that evening|"
    r"that night|in the morning|in the afternoon|in the evening|at dawn|at dusk|at night)\b", re.I)
EVENTS = ("supper", "dinner", "breakfast", "lunch", "funeral", "wedding", "midnight")
EVENT_OPENER = re.compile(
    r"(?:^|(?<=[.!?…])[\"'”’]?\s+|\n\s*)(?:At|During|Over) (?:the )?(" + "|".join(EVENTS) + r")\b")
WALK_TO = re.compile(
    r"\b(?:walk|walks|walking|walked|go|goes|going|head|heads|heading|drive|drives|driving|return|returns|returning)"
    r"(?: out| back)? to (the [A-Z][a-z]+)\b")
SCREEN_DIRECTIONS = (
    r"\bcuts? (?:\w+ )?to black\b", r"\b(?:story|scene|film|video|picture) cuts\b",
    r"\bthe screen (?:goes|cuts|fades|turns|is|falls)\b", r"\bfades? to (?:black|white)\b", r"\bfade (?:out|in)\b",
    r"\b(?:the )?camera (?:pans|cuts|moves|holds|lingers|pulls|pushes|follows|tilts|stays|drifts)\b",
    r"\bscene ends\b", r"\bwe see\b",
)
_SCREEN = re.compile("|".join(SCREEN_DIRECTIONS), re.I)
LECTURES = (
    r"\bwe are left\b", r"\bwe all\b", r"\b(?:perhaps|maybe) we\b", r"\b(?:each|all|none) of us\b", r"\bwondering where\b", r"\bboundary truly\b",
    r"\bthe truth is that\b", r"\bwhat it (?:truly |really )?means to\b", r"\bperhaps that is what\b",
)
_LECTURE = re.compile("|".join(LECTURES), re.I)
SENTENCE = re.compile(r"[^.!?…\n]+[.!?…]*[\"'”’]*")
WARDROBE = ("coat", "braid", "apron", "glasses", "spectacles", "scarf", "shawl", "hat", "cap", "cardigan", "boots")
OUTDOOR_ONLY = ("coat", "scarf", "hat")
MARKER_SHARE = 0.40
# Words that make a setting or location an exterior / an interior (also the visual bible's flag, 3.9.3-F).
EXTERIOR_WORDS = ("moor", "moors", "moorland", "field", "fields", "lane", "street", "road", "sea", "shore", "beach",
                  "forest", "woods", "garden", "cemetery", "graveyard", "churchyard", "hill", "hills", "mountain",
                  "river", "lake", "park", "yard", "square", "market", "outside", "outdoors", "open-air", "bridge",
                  "harbour", "harbor", "cliff", "meadow", "farmland", "stones", "bench", "grave")
INTERIOR_WORDS = ("kitchen", "room", "bedroom", "hall", "hallway", "corridor", "bakery", "shop", "store", "office",
                  "house", "home", "attic", "cellar", "basement", "church", "school", "library", "studio", "cottage",
                  "flat", "apartment", "indoors", "inside", "interior", "bar", "pub", "cafe", "café", "hospital", "ward",
                  "stairs", "staircase", "workshop", "theatre", "restaurant", "table")


def is_exterior(text: str) -> bool | None:
    """Outdoors by keywords: True, False, or None when the text does not say. Whichever kind of word
    comes first decides ("The kitchen of the Wren home" / "Outside the bakery along the stone lane")."""
    words = re.findall(r"[a-z'-]+", str(text or "").lower())
    for word in words:
        if word in EXTERIOR_WORDS:
            return True
        if word in INTERIOR_WORDS:
            return False
    return None


def _finding(severity: str, issue_type: str, scene_ids: list, claim: str, evidence: list, recommendation: str) -> dict:
    return {"severity": severity, "issue_type": issue_type, "scene_ids": scene_ids, "claim": claim,
            "evidence": evidence, "why_it_matters": "Read aloud, this breaks the story.",
            "recommendation": recommendation, "certainty": "confirmed"}


def _plan_text(plan: dict) -> str:
    return " ".join(str(plan.get(key, "")) for key in ("title", "summary", "setting", "chronology_marker", "dramatic_action",
                                                       "initial_dramatic_situation", "consequence"))


def time_markers(text: str) -> list[str]:
    return [match.group(0).lower() for match in TIME_MARKER.finditer(without_speech(text))]


def chronology_findings(sections: list[dict], outline: dict | None) -> list[dict]:
    findings = []
    plans = (outline or {}).get("scenes") or []
    by_id = {plan.get("scene_id"): position for position, plan in enumerate(plans)}
    for section in sections:
        text = without_speech(section.get("narration", ""))
        seen: dict[str, str] = {}
        for match in OPENER.finditer(text):
            key = match.group(1).lower()
            if key in seen:
                findings.append(_finding(
                    "major", "chronology", [section.get("id")],
                    f"Scene {section.get('id')} opens the same time marker twice: \"{key}\".",
                    [f"{key}: {text[max(0, match.start() - 10):match.end() + 30].strip()}"],
                    "Keep one time marker; the scene already said when it happens."))
                break
            seen[key] = key
        position = by_id.get(section.get("outline_scene_id"))
        if position is None:
            continue
        own = _plan_text(plans[position]).lower()
        earlier = " ".join(_plan_text(plan) for plan in plans[:position])
        later = plans[position + 1:]

        def planned_later(term: str, case_sensitive: bool = False) -> dict | None:
            pattern = re.compile(rf"\b{re.escape(term)}\b", 0 if case_sensitive else re.I)
            if pattern.search(earlier) or pattern.search(_plan_text(plans[position])):
                return None
            return next((plan for plan in later if pattern.search(_plan_text(plan))), None)
        for match in EVENT_OPENER.finditer(text):
            plan = planned_later(match.group(1))
            if plan is not None:
                findings.append(_finding(
                    "major", "chronology", [section.get("id")],
                    f"Scene {section.get('id')} jumps ahead: \"{match.group(0).strip()}\" belongs to the later scene "
                    f"{plan.get('scene_id')} ({plan.get('title')}).",
                    [text[match.start():match.end() + 50].strip(), f"markers: {time_markers(text)}"],
                    "Leave this event to its own scene."))
        for match in WALK_TO.finditer(text):
            plan = planned_later(match.group(1), case_sensitive=True)
            if plan is not None:
                findings.append(_finding(
                    "major", "chronology", [section.get("id")],
                    f"Scene {section.get('id')} goes to {match.group(1)} before scene {plan.get('scene_id')} "
                    f"({plan.get('title')}) plans it.",
                    [text[max(0, match.start() - 20):match.end() + 20].strip()],
                    "Leave this movement to its own scene."))
    return findings


def _authorised(sentence: str, lines: list[str]) -> bool:
    from agents.premise_brief import mentioned
    return any(mentioned(line, sentence, 0.8) or mentioned(sentence, line, 0.8) for line in lines)


def screen_direction_findings(sections: list[dict]) -> list[dict]:
    findings = []
    for section in sections:
        text = without_speech(section.get("narration", ""))
        for sentence in SENTENCE.findall(text):
            match = _SCREEN.search(sentence)
            if match:
                findings.append(_finding(
                    "major", "screen_direction", [section.get("id")],
                    f"Scene {section.get('id')}: the narration describes the edit (\"{match.group(0)}\").",
                    [sentence.strip()], "Remove it: end the scene at that moment instead of announcing it."))
    return findings


def lecture_findings(sections: list[dict], authorised: list[str] = ()) -> tuple[list[dict], list[dict]]:
    """(major, minor) lecture lines: major in the last two scenes."""
    major, minor = [], []
    last_two = {section.get("id") for section in sections[-2:]}
    for section in sections:
        for sentence in SENTENCE.findall(without_speech(section.get("narration", ""))):
            if not _LECTURE.search(sentence) or _authorised(sentence, list(authorised)):
                continue
            severe = section.get("id") in last_two
            issue = _finding("major" if severe else "minor", "lecture", [section.get("id")],
                             f"Scene {section.get('id')}: the narrator moralises.", [sentence.strip()],
                             "Replace it with concrete action, image or dialogue.")
            (major if severe else minor).append(issue)
    return major, minor


def marker_findings(sections: list[dict], characters: list[dict]) -> list[dict]:
    """Minor: wardrobe markers that leak into the prose."""
    findings = []
    for character in characters or []:
        cid = character.get("character_id")
        source = " ".join(str(character.get(key, "")) for key in ("visual_identity", "distinctive_details")).lower()
        markers = [word for word in WARDROBE if re.search(rf"\b{word}\b", source)]
        scenes = [section for section in sections if cid in (section.get("characters_present") or [])]
        if not markers or not scenes:
            continue
        for marker in markers:
            pattern = re.compile(rf"\b{marker}\b", re.I)
            hits = [section for section in scenes if pattern.search(section.get("narration", ""))]
            if len(hits) >= 2 and len(hits) / len(scenes) > MARKER_SHARE:
                findings.append(_finding(
                    "minor", "signature_marker", [section.get("id") for section in hits],
                    f"{character.get('name', cid)}'s {marker} is mentioned in {len(hits)} of {len(scenes)} of their scenes.",
                    [f"{marker}: scenes {[section.get('id') for section in hits]}"],
                    "Markers belong to the visual bible and the shots, not to every paragraph."))
            if marker in OUTDOOR_ONLY:
                for section in hits:
                    if is_exterior(section.get("setting", "")) is False:
                        findings.append(_finding(
                            "minor", "signature_marker", [section.get("id")],
                            f"{character.get('name', cid)}'s {marker} appears in an indoor scene.",
                            [str(section.get("setting", ""))[:120]], "Drop the marker here."))
    return findings


def review(sections: list[dict], outline: dict | None, characters: list[dict], authorised: list[str] = ()) -> dict:
    """{"major": [...], "minor": [...]} for ``agents.narrative.deterministic_review``."""
    lectures_major, lectures_minor = lecture_findings(sections, authorised)
    return {"major": chronology_findings(sections, outline) + screen_direction_findings(sections) + lectures_major,
            "minor": lectures_minor + marker_findings(sections, characters)}
