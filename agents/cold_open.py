"""Cold open (Phase 3.9.7): the episode opens on its most dramatic moment, then goes back and builds to it.

An optional first scene of section_type ``cold_open``, before the hook scene:

* ``flash_forward`` (types A and B): 50-100 words (hard max 110) taken from the reversal or climax scene
  (``source_scene_id``). It shows the tension and stops at the instant before the payoff: it never reveals what
  is ``withheld``, the ending or the twist. The source scene later contains the same moment with one matching
  line or image (the echo, at most one sentence of overlap). The scene after the teaser re-anchors in time with
  a concrete marker ("Three weeks earlier, Leo turned twelve."), never a stock phrase.
* ``omen`` (type C): one or two lines in the tale-teller's voice that foretell the ending without explaining it;
  no jump in time, so no source scene and no return line.
* ``off``: no cold open; the old behaviour.

The brief's ``cold_open`` beat is authoritative; without one, the outline proposes a teaser marked ``proposed``
for the human review. The mode per narrative type is ``config.COLD_OPEN``.
"""
from __future__ import annotations

import re

import config

MODES = ("flash_forward", "omen", "off")
DEFAULTS = {"A": "flash_forward", "B": "flash_forward", "C": "omen"}
LETTERS = {"realistic_psychological_fiction": "A", "philosophical_thought_experiment": "B", "dark_human_parable": "C"}
WORDS = {"flash_forward": (50, 100), "omen": (12, 45)}
HARD_MAX = 110
BUDGET = {"flash_forward": 75, "omen": 30}
MOMENT_KINDS = ("discovery", "confrontation", "line", "image")
SOURCE_TYPES = ("reversal", "climax")
SECTION_TYPE = "cold_open"
CHAPTER_TITLE = "Prologue"
STOCK_RETURNS = re.compile(r"\b(?:let'?s go back|let me take you back|go back to the beginning|back to the beginning|"
                           r"it all (?:started|began)|to understand (?:how|why)|rewind|but first|our story begins|"
                           r"this is how it (?:started|began))\b", re.I)
TIME_MARKER = re.compile(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a few|several|a)\s+"
                         r"(?:minutes?|hours?|days?|weeks?|months?|years?|nights?|mornings?|summers?|winters?|birthdays?)\s+"
                         r"(?:earlier|before|ago)\b|\b(?:earlier that|that (?:morning|spring|summer|autumn|winter|year)|"
                         r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|in (?:january|february|march|april|may|"
                         r"june|july|august|september|october|november|december)|the (?:day|night|week|month|year) (?:before|"
                         r"that|it began)|years? earlier|\d{1,2}(?:st|nd|rd|th)? birthday|turned \w+)\b", re.I)
STOPWORDS = frozenset("""a an and are as at be been but by can could did do does for from had has have he her him his how i if in
into is it its me my no nor not of on or our she so than that the their them then there these they this those to too up us was
we were what when where which while who why will with would yet you your just only one over still own very about after again
all also any back down even ever every here more most much never now off out some such through under until upon""".split())


def letter(narrative_type: str | None) -> str:
    value = str(narrative_type or "").strip()
    return LETTERS.get(value, value.upper()[:1] if value.upper()[:1] in DEFAULTS else "")


def configured() -> dict:
    found = getattr(config, "COLD_OPEN", None) or {}
    return {key: found.get(key, mode) if found.get(key, mode) in MODES else mode for key, mode in DEFAULTS.items()}


def mode_for(narrative_type: str | None, beat: str = "") -> str:
    """The type's mode; the brief's cold_open beat is authoritative and turns an "off" type on."""
    mode = configured().get(letter(narrative_type), "off")
    if beat and mode == "off":
        return "flash_forward"
    return mode


def brief_beat(story_brief: dict | None) -> str:
    beats = ((story_brief or {}).get("author_brief") or story_brief or {}).get("beats") or {}
    return str(beats.get("cold_open", "")).strip() if isinstance(beats, dict) else ""


# ── Outline ──────────────────────────────────────────────────────────────────

def outline_rule(mode: str, beat: str = "") -> str:
    """For the outline stage."""
    if mode == "off":
        return ""
    low, high = WORDS[mode]
    common = (f"\nCOLD OPEN: put ONE extra scene FIRST, before the opening scene, with section_type \"cold_open\", "
              f"scene_id \"s00\", order 1 (the other scenes follow from 2), pace \"brief\", target_words {BUDGET[mode]} ({low}-{high} words; it has its "
              f"own budget and takes nothing from the climax), arc_movements [\"cold_open\"], and these extra fields: "
              f"cold_open_mode \"{mode}\", moment_kind (one of {', '.join(MOMENT_KINDS)}), withheld (what the teaser must "
              f"NOT reveal, e.g. \"what the clip shows\"). The scene after it is the real opening.")
    if mode == "omen":
        return common + (" OMEN: one or two lines in the tale-teller's voice that foretell the ending without explaining "
                         "it (\"This is the story of how Low Marrow lost its bridge twice.\"); no jump in time, no "
                         "source_scene_id (use \"\").")
    source = ("The author's brief gives the cold_open beat: use exactly that moment." if beat else
              "Choose the most dramatic instant of the reversal or climax scene.")
    return common + (f" FLASH-FORWARD: {source} source_scene_id is the later reversal/climax scene it previews. Show the "
                     "tension, not the outcome: stop at the instant before the payoff (a reaction, a discovery about to be "
                     "seen, a line that raises a question); never the resolution, the ending, the final twist or the "
                     "content of the discovery. The source scene must contain the same moment again with one matching "
                     "line or image. The first scene after the cold open re-anchors in time with a concrete marker "
                     "(\"Three weeks earlier, Leo turned twelve.\"), never a stock phrase like \"let's go back\".")


def plan(outline: dict | None) -> dict | None:
    """The cold-open scene of an outline, or None."""
    return next((scene for scene in (outline or {}).get("scenes") or [] if scene.get("section_type") == SECTION_TYPE), None)


def ensure(outline: dict, mode: str, beat: str = "") -> dict:
    """After the outline call: drop a cold open when the mode is off; otherwise put it first with its own budget, its
    movement and its status (``from_brief`` or ``proposed``), and point a flash-forward at a reversal/climax scene."""
    scenes = outline.get("scenes") or []
    teasers = [scene for scene in scenes if scene.get("section_type") == SECTION_TYPE]
    if mode == "off" or not teasers:
        outline["scenes"] = [scene for scene in scenes if scene.get("section_type") != SECTION_TYPE]
        outline.pop("cold_open", None)
        if mode != "off":                               # wanted, not planned: the review says so
            outline["cold_open"] = {"mode": mode, "scene_id": None}
        return outline
    teaser = teasers[0]
    rest = [scene for scene in scenes if scene is not teaser and scene.get("section_type") != SECTION_TYPE]
    low, high = WORDS[mode]
    teaser.update(pace="brief", cold_open_mode=mode, target_words=min(high, max(low, int(teaser.get("target_words") or BUDGET[mode]))),
                  status="from_brief" if beat else "proposed")
    teaser["arc_movements"] = ["cold_open"] + [item for item in teaser.get("arc_movements") or [] if item != "cold_open"]
    if teaser.get("moment_kind") not in MOMENT_KINDS:
        teaser["moment_kind"] = "image"
    if mode == "omen":
        teaser["source_scene_id"] = ""
    else:
        ids = {scene.get("scene_id"): scene for scene in rest}
        source = ids.get(teaser.get("source_scene_id"))
        if not source or source.get("section_type") not in SOURCE_TYPES:
            fallback = next((scene for scene in rest if scene.get("section_type") in SOURCE_TYPES), None)
            teaser["source_scene_id"] = fallback.get("scene_id") if fallback else teaser.get("source_scene_id", "")
    teaser.setdefault("withheld", "")
    outline["scenes"] = [teaser] + rest
    for order, scene in enumerate(outline["scenes"], 1):     # validate_outline wants 1, 2, 3...
        scene["order"] = order
    outline["cold_open"] = {"mode": mode, "scene_id": teaser.get("scene_id"), "source_scene_id": teaser.get("source_scene_id", ""),
                            "withheld": teaser.get("withheld", ""), "moment_kind": teaser["moment_kind"], "status": teaser["status"]}
    return outline


def screenplay_rule(outline: dict | None) -> str:
    """For the scene writer."""
    teaser = plan(outline)
    if not teaser:
        return ""
    if teaser.get("cold_open_mode") == "omen":
        return ("\nCOLD OPEN (omen): the cold_open scene is one or two lines in the tale-teller's voice that foretell the "
                "ending without explaining it. No scene, no jump in time; the next scene starts the story plainly.")
    return ("\nCOLD OPEN (flash-forward): write the cold_open scene in the present tense or a suspended past (\"Hannah "
            f"froze...\") and END ON THE QUESTION, at the instant before the payoff. Never reveal: {teaser.get('withheld') or 'the payoff'}; "
            f"never the ending or the twist. Scene {teaser.get('source_scene_id')} later reaches the same moment and repeats ONE "
            "matching line or image from the teaser (at most one sentence in common; the rest is new prose). The scene right "
            "after the cold open re-anchors in time with a concrete marker in its first sentence (\"Three weeks earlier, "
            "Leo turned twelve.\"), never \"let's go back to the beginning\".")


def review_rule(outline: dict | None) -> str:
    """For the editorial review: the semantic reveal question."""
    teaser = plan(outline)
    if not teaser:
        return ""
    return (f"\nCOLD OPEN: scene {teaser.get('scene_id')} is a cold open that previews scene {teaser.get('source_scene_id') or 'none'} "
            "by design (exempt from the time-order and spent-reversal checks). Answer in the checklist: does the cold open "
            f"reveal {teaser.get('withheld') or 'the payoff'}, the ending or the twist? If it does, report a major finding "
            "with issue_type \"cold_open_reveal\".")


# ── Checks ───────────────────────────────────────────────────────────────────

def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    return {_stem(word) for word in re.findall(r"[a-z']+", str(text or "").lower()) if len(word) >= 4 and word not in STOPWORDS}


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?…])[\"'”’]*\s+", str(text or "").strip()) if part.strip()]


def _same_sentence(first: str, second: str) -> bool:
    a, b = content_words(first), content_words(second)
    return bool(a) and bool(b) and len(a & b) / min(len(a), len(b)) >= 0.6 and min(len(a), len(b)) >= 3


def _finding(severity: str, issue_type: str, scene_ids: list, claim: str, evidence: list, recommendation: str) -> dict:
    return {"severity": severity, "issue_type": issue_type, "scene_ids": scene_ids, "claim": claim, "evidence": evidence,
            "why_it_matters": "The cold open is the episode's promise: it must pull the viewer in without spending the payoff.",
            "recommendation": recommendation, "certainty": "confirmed"}


def _section_for(sections: list[dict], outline_scene_id) -> dict | None:
    return next((section for section in sections if section.get("outline_scene_id") == outline_scene_id), None)


def findings(sections: list[dict], outline: dict | None, *, narrative_type: str | None = None, beat: str = "",
             ending_text: str = "") -> list[dict]:
    """Deterministic findings: presence, length, overlap/echo with the source, reveal of the withheld or the ending,
    the return line."""
    mode = mode_for(narrative_type, beat)
    teaser_plan = plan(outline)
    teaser = next((section for section in sections if section.get("section_type") == SECTION_TYPE), None)
    if mode == "off" and not teaser:
        return []
    if not teaser:
        # only an outline made under this contract carries "cold_open"; earlier projects are not judged by it
        if not (outline or {}).get("cold_open"):
            return []
        return [_finding("major", "cold_open_missing", [], f"This type's cold-open mode is {mode}, but the screenplay has no cold_open scene.",
                         [], "Add a cold open before the opening scene (regenerate the outline), or set this type to off in Settings.")]
    found = []
    tid = teaser.get("id")
    words = len(str(teaser.get("narration", "")).split())
    teaser_mode = (teaser_plan or {}).get("cold_open_mode") or mode
    low, high = WORDS.get(teaser_mode, WORDS["flash_forward"])
    if words > HARD_MAX or (teaser_mode == "omen" and words > high):
        found.append(_finding("major", "cold_open_length", [tid], f"The cold open has {words} words; the limit is {high if teaser_mode == 'omen' else HARD_MAX}.",
                              [f"{words} words"], "Cut it to the instant before the payoff."))
    elif words < low:
        found.append(_finding("minor", "cold_open_length", [tid], f"The cold open has {words} words; it needs at least {low}.",
                              [f"{words} words"], "Give the moment room to land."))
    if sections and sections[0] is not teaser:
        found.append(_finding("major", "cold_open_position", [tid], "The cold open is not the first scene.", [], "Move it before the opening scene."))
    text = str(teaser.get("narration", ""))
    # what the withheld text shares with the teaser's own plan (its people, its place) is fair to name
    own = content_words(" ".join(str((teaser_plan or {}).get(key, "")) for key in ("title", "summary", "setting")))
    withheld = content_words((teaser_plan or {}).get("withheld", "")) - own
    revealed = sorted(content_words(text) & withheld)
    if withheld and (len(revealed) >= 2 or len(revealed) / len(withheld) >= 0.5):
        found.append(_finding("major", "cold_open_reveal", [tid], f"The cold open names what it must withhold: {', '.join(revealed)}.",
                              [f"withheld: {(teaser_plan or {}).get('withheld')}"], "Stop before the reveal; keep it unseen."))
    ending = content_words(ending_text)
    spoiled = sorted(content_words(text) & ending)
    if ending and len(spoiled) >= 4:
        found.append(_finding("minor", "cold_open_reveal", [tid], f"The cold open shares many words with the ending: {', '.join(spoiled[:8])}.",
                              [], "Check that it does not foretell how it ends."))
    if teaser_mode == "omen":
        return found
    source = _section_for(sections, (teaser_plan or {}).get("source_scene_id"))
    if source is not None:
        shared = [line for line in sentences(text) if any(_same_sentence(line, other) for other in sentences(source.get("narration", "")))]
        if len(shared) > 1:
            found.append(_finding("major", "cold_open_overlap", [tid, source.get("id")], f"The cold open repeats {len(shared)} sentences of scene {source.get('id')}; at most one may echo.",
                                  shared[:3], "Keep one matching line; write the rest anew."))
        elif not shared and not (content_words(text) & content_words(source.get("narration", ""))):
            found.append(_finding("minor", "cold_open_echo", [tid, source.get("id")], f"Scene {source.get('id')} never returns to the cold open's moment.",
                                  [], "Repeat one line or image from the teaser when the story reaches it."))
    position = sections.index(teaser)
    if position + 1 < len(sections):
        following = sections[position + 1]
        opening = " ".join(sentences(following.get("narration", ""))[:2])
        if STOCK_RETURNS.search(opening):
            found.append(_finding("major", "cold_open_return", [following.get("id")], "The story returns from the cold open with a stock phrase.",
                                  [STOCK_RETURNS.search(opening).group(0)], "Re-anchor with a concrete time marker: \"Three weeks earlier, Leo turned twelve.\""))
        elif not TIME_MARKER.search(opening):
            found.append(_finding("major", "cold_open_return", [following.get("id")], "After the cold open, the story does not say when it is.",
                                  [opening[:160]], "Open the next scene with a concrete time marker (\"Three weeks earlier, ...\")."))
    return found


def moment(outline: dict | None) -> dict:
    """{"mode", "moment"} for the variation profile."""
    teaser = plan(outline)
    if not teaser:
        return {"mode": "off", "moment": ""}
    return {"mode": teaser.get("cold_open_mode", "flash_forward"), "moment": teaser.get("moment_kind", "")}


def teaser_text(script: dict | None) -> str:
    section = next((item for item in (script or {}).get("sections") or [] if item.get("section_type") == SECTION_TYPE), None)
    return str((section or {}).get("narration", ""))


def repeats_teaser(text: str, script: dict | None, span: int = 6) -> bool:
    """Whether ``text`` (a description hook) repeats a run of ``span`` words from the teaser."""
    teaser = re.findall(r"[a-z']+", teaser_text(script).lower())
    words = re.findall(r"[a-z']+", str(text or "").lower())
    runs = {" ".join(teaser[index:index + span]) for index in range(len(teaser) - span + 1)}
    return any(" ".join(words[index:index + span]) in runs for index in range(len(words) - span + 1))
