"""A staging sheet per scene: blocking and continuity (Phase 3.9.9-B).

One Gemini call per scene reads ONLY the narration and says, moment by moment (a sentence range), where each character
is (location and position: "driver's seat", "behind the kitchen island"), seated or standing, what they hold, who is
not there, and how they relate to large objects. Every entry carries an evidence quote that must be in the narration.

The prompt composer turns a shot's moment into physically clear phrases ("Hannah in the driver's seat, hands on the
wheel; Leo in the front passenger seat beside her, both inside the car"; "standing behind the desk, the desk between her
and the viewer"), because "at the desk" let the model draw a person inside a table. Consecutive shots of one moment keep
one staging (``continuity``).
"""
from __future__ import annotations

import re
from pathlib import Path

from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash

FILE_NAME = "staging.json"
VERSION = 1
POSTURES = ("seated", "standing", "lying", "kneeling", "walking", "other")
SIDES = ("behind", "in_front_of", "beside", "on", "in", "at")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", str(text or "").lower().replace("’", "'"))).strip()


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?…])[\"'”’]*\s+", str(text or "").strip()) if part.strip()]


def contract(cast: list[dict], locations: list[dict]) -> str:
    return (f"""You are the staging director of an illustrated film. From the numbered NARRATION SENTENCES of ONE scene, write
its staging sheet: for each moment (a contiguous sentence range), where each character is and how. Use ONLY what the
narration says or plainly implies; quote it.
Cast (use these ids): {[{"id": item["character_id"], "who": item.get("descriptor", "")[:60]} for item in cast]}
Known locations (use these ids, or a short new id like "car_interior" when the narration goes elsewhere):
{[{"id": item["location_id"], "what": item.get("descriptor", "")[:80]} for item in locations]}
Return ONLY JSON {{"moments": [{{"sentences": [first, last], "location_id": "...", "evidence": "quote placing the moment",
 "characters": [{{"id": "...", "position": "driver's seat | behind the kitchen island | in the doorway | ...", "posture": one of
 {list(POSTURES)}, "holding": "" or what they hold, "evidence": "quote"}}],
 "absent": [ids of cast members NOT there],
 "relations": [{{"character": "...", "object": "kitchen island | desk | table | car seat | ...", "side": one of {list(SIDES)},
 "evidence": "quote"}}]}}]}}. Every evidence is copied word for word from the narration.""")


def validate(value: dict, narration: str, cast_ids: list[str]) -> dict:
    """Keep only entries whose evidence is in the narration; reject a structurally broken sheet."""
    moments = value.get("moments") if isinstance(value, dict) else None
    if not isinstance(moments, list) or not moments:
        raise NarrativeValidationError("staging sheet needs a nonempty moments array")
    count = len(sentences(narration))
    text = _norm(narration)
    found = lambda quote: bool(str(quote or "").strip()) and _norm(quote) in text
    kept = []
    for moment in moments:
        span = moment.get("sentences") if isinstance(moment, dict) else None
        if not (isinstance(span, list) and len(span) == 2 and all(isinstance(item, int) for item in span)):
            raise NarrativeValidationError("each moment needs sentences [first, last] as integers")
        first, last = max(0, min(span)), min(count - 1, max(span))
        characters = [item for item in moment.get("characters") or [] if isinstance(item, dict) and item.get("id") in cast_ids
                      and found(item.get("evidence")) and item.get("posture") in POSTURES]
        relations = [item for item in moment.get("relations") or [] if isinstance(item, dict) and item.get("character") in cast_ids
                     and item.get("side") in SIDES and found(item.get("evidence")) and str(item.get("object", "")).strip()]
        kept.append({"sentences": [first, last], "location_id": str(moment.get("location_id") or ""),
                     "evidence": moment.get("evidence") if found(moment.get("evidence")) else "",
                     "characters": [{key: str(item.get(key) or "") for key in ("id", "position", "posture", "holding", "evidence")} for item in characters],
                     "absent": [cid for cid in moment.get("absent") or [] if cid in cast_ids],
                     "relations": [{key: str(item.get(key) or "") for key in ("character", "object", "side", "evidence")} for item in relations]})
    return {"version": VERSION, "moments": sorted(kept, key=lambda item: item["sentences"][0])}


def scene_key(section: dict) -> str:
    """The scene id the shot list uses for a script section."""
    return str(section.get("outline_scene_id") or f"s{int(section.get('id', 0)):02d}")


def narrations(script: dict) -> dict:
    return {scene_key(section): str(section.get("narration", "")) for section in script.get("sections", []) if str(section.get("narration", "")).strip()}


def build(script: dict, vb: dict, output_dir, *, generator=None, force: bool = False, progress=print) -> dict:
    """staging.json: {scene_id: sheet}, one call per scene, cached on the scene's narration and cast."""
    from agents.gemini_client import generate
    from agents.structured import generate_json
    output = Path(output_dir)
    path = output / FILE_NAME
    saved = load_json(path) or {}
    cast = [item for item in vb.get("characters", []) if item.get("presence", "on_screen") != "none"]
    cast_ids = [item["character_id"] for item in cast]
    changed = False
    for index, section in enumerate(script.get("sections", []), 1):
        narration = str(section.get("narration", "")).strip()
        if not narration:
            continue
        scene_id = scene_key(section)
        key = stable_hash({"narration": narration, "cast": cast_ids, "version": VERSION})
        if not force and (saved.get(scene_id) or {}).get("key") == key:
            continue
        numbered = "\n".join(f"{number}: {sentence}" for number, sentence in enumerate(sentences(narration)))
        progress(f"   -> staging sheet {scene_id}")
        sheet = generate_json(contract(cast, vb.get("locations", [])), "staging sheet",
                              lambda value: validate(value, narration, cast_ids), generator=generator or generate,
                              context=f"NARRATION SENTENCES:\n{numbered}", stage=f"staging-{scene_id}",
                              temperature_key="editorial_review", run_dir=str(output))
        saved[scene_id] = {"key": key, **sheet}
        changed = True
    if changed:
        atomic_write_json(path, saved)
    return saved


def moment_for(shot: dict, sheet: dict | None, narration: str) -> dict | None:
    """The moment whose sentences the shot's excerpt covers (the last one when it covers several)."""
    moments = (sheet or {}).get("moments") or []
    if not moments:
        return None
    excerpt = _norm(shot.get("narration_excerpt", ""))
    covered = [index for index, sentence in enumerate(sentences(narration)) if _norm(sentence) and _norm(sentence) in excerpt]
    if not covered:
        return None
    hits = [moment for moment in moments if moment["sentences"][0] <= covered[-1] and moment["sentences"][1] >= covered[0]]
    return hits[-1] if hits else max((moment for moment in moments if moment["sentences"][0] <= covered[0]),
                                     key=lambda moment: moment["sentences"][0], default=None)


def attach(shot: dict, sheet: dict | None, narration: str) -> None:
    """Store the staging of the shot's moment on the shot (only its own characters)."""
    moment = moment_for(shot, sheet, narration)
    if not moment:
        shot.pop("staging", None)
        return
    listed = set(shot.get("characters") or [])
    shot["staging"] = {"moment": moment["sentences"], "location_id": moment.get("location_id", ""),
                       "characters": [item for item in moment["characters"] if item["id"] in listed],
                       "relations": [item for item in moment["relations"] if item["character"] in listed],
                       "absent": moment.get("absent", [])}


# ── Phrases ─────────────────────────────────────────────────────────────────

def _position(position: str) -> str:
    position = str(position or "").strip().rstrip(".")
    if not position:
        return ""
    if re.match(r"^(?:in|on|at|behind|beside|next to|near|by|under|inside|outside|across)\b", position, re.I):
        return position
    if re.search(r"\b(?:seat|doorway|corner|bed|chair|sofa|couch|car|room|kitchen|hallway)\b", position, re.I):
        return f"in the {position}" if not position.lower().startswith("the ") else f"in {position}"
    return position


FURNITURE = re.compile(r"\b(?:table|desk|island|counter|bench|sofa|couch|bed|cabinet|shelf|shelves|bar|workbench|piano|dresser|chair)\b", re.I)


def relation_phrase(relation: dict, pronoun: str, posture: str = "") -> str:
    """Which side of a large object a character is on, and what overlaps what."""
    obj, side = str(relation.get("object", "")).strip(), relation.get("side")
    posed = {"seated": "seated", "standing": "standing", "kneeling": "kneeling"}.get(posture, "")
    lead = f"{posed} " if posed else ""
    if side == "behind":
        return f"{lead}behind the {obj}, the {obj} between {pronoun} and the viewer"
    if side == "in_front_of":
        return f"{lead}in front of the {obj}, the {obj} behind {pronoun}"
    if side == "beside":
        return f"{lead}beside the {obj}, {pronoun} body clear of it"
    if side == "at":
        return f"seated on a chair at the {obj}" if posture == "seated" else f"standing at the edge of the {obj}, the {obj} in front of {pronoun} waist"
    if side == "on":
        return f"{lead}on the {obj}"
    return f"{lead}inside the {obj}"


def phrase(staging: dict | None, names: dict, nouns: dict, location: dict | None = None) -> str:
    """"Hannah in the driver's seat, hands on the wheel; Leo in the passenger seat beside her, both inside the car." """
    if not staging or not staging.get("characters"):
        return ""
    parts = []
    for item in staging["characters"]:
        cid = item["id"]
        pronoun = {"woman": "her", "man": "him"}.get(nouns.get(cid, ""), "them")
        possessive = {"woman": "her", "man": "his"}.get(nouns.get(cid, ""), "their")
        bits = [names.get(cid, cid)]
        relation = next((rel for rel in staging.get("relations") or [] if rel["character"] == cid
                         and FURNITURE.search(rel.get("object", ""))), None)
        if re.search(r"\bseat\b", str(item.get("position", "")), re.I):
            relation = None                               # a seat says it all: "in the driver's seat", never "on a chair"
        if relation:
            bits.append(relation_phrase(relation, pronoun if relation.get("side") != "at" else possessive, item.get("posture", "")))
        else:
            where = _position(item.get("position", ""))
            posture = item.get("posture") if item.get("posture") in ("seated", "standing", "kneeling", "lying") else ""
            bits.append(" ".join(word for word in (posture, where) if word))
        if "driver" in str(item.get("position", "")).lower() and not item.get("holding"):
            bits.append(f"{possessive} hands on the wheel")
        if item.get("holding"):
            bits.append(f"holding {item['holding']}")
        parts.append(", ".join(bit for bit in bits if bit))
    text = "; ".join(parts)
    if location and location.get("enclosed"):
        count = len(staging["characters"])
        place = re.sub(r"\s+interior$", "", str(location.get("name") or "car").lower()).strip() or "car"
        text += f", {'both ' if count == 2 else 'all ' if count > 2 else ''}inside the {place}"
    return text[0].upper() + text[1:] + "." if text else ""


CONTACT_FIXES = ((re.compile(r"\bleaning into the (\w+(?: \w+)?)", re.I), r"leaning on the edge of the \1"),
                 (re.compile(r"\b(?:sitting|sits|seated) at the (\w+(?: \w+)?)(?! on a chair)", re.I), r"seated on a chair at the \1"))


def contact_wording(action: str) -> str:
    """"leaning into the table" / "sitting at the desk" without a seat drew people inside furniture."""
    for pattern, replacement in CONTACT_FIXES:
        action = pattern.sub(replacement, str(action or ""))
    return action


def continuity(shots: list[dict]) -> list[dict]:
    """Adjacent shots of one staging moment must keep its location and positions."""
    found = []
    for first, second in zip(shots, shots[1:]):
        a, b = first.get("staging"), second.get("staging")
        if not a or not b or first.get("scene_id") != second.get("scene_id") or a.get("moment") != b.get("moment"):
            continue
        if first.get("location") != second.get("location"):
            found.append({"shots": [first["shot_id"], second["shot_id"]],
                          "problem": f"same moment, two locations: {first.get('location')} / {second.get('location')}"})
        seats = {item["id"]: (item.get("position"), item.get("posture")) for item in a.get("characters", [])}
        for item in b.get("characters", []):
            if item["id"] in seats and seats[item["id"]] != (item.get("position"), item.get("posture")):
                found.append({"shots": [first["shot_id"], second["shot_id"]],
                              "problem": f"{item['id']} moves within one moment: {seats[item['id']]} / {(item.get('position'), item.get('posture'))}"})
    return found
