"""Visual bible: the immutable look of characters, wardrobe per scene and recurring locations.

One Gemini call (contract first, JSON mode, validated, cached by source hash)
turns the Character Bible, outline and screenplay into ``visual_bible.json``:

* ``global_style`` — recorded from ``agents/identity.py`` (the single source of
  the visual style); prompts always use the live identity style, not this copy;
* ``characters`` — one immutable visual descriptor each (apparent age matching
  the bible, ethnicity as stated or chosen once, build, face, hair, features)
  and the wardrobe per scene, consistent with the screenplay;
* ``locations`` — a reusable descriptor per recurring set and the scenes set there;
* ``thumbnail_shot`` — the episode's strongest emotional image, with negative
  space for the thumbnail text.

Every character has a ``presence`` decided in code from the premise
(``on_screen`` | ``voice_only`` | ``none``) and, when not on screen, a
``visual_proxy``. A ``none`` character (an AI with no body) never gets a portrait
or a person descriptor; frames show its proxy (the phone's glow). A
``voice_only`` character (a voice on the phone) never appears in a shot; the
frame shows who is listening. Shots, references and head counts use only
``on_screen`` characters.

Seeds per character and location are derived deterministically in code.
Approved reference images (character portraits, location establishing shots)
live under ``references`` and survive regeneration while their ids still exist.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import config
from agents import structured
from agents.gemini_client import generate
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash
from agents.structured import compact_json

VISUAL_BIBLE_VERSION = 4   # 2: identity-only descriptors, clothing-only outfits (3.3.2); 3: signature markers, scene states (3.4); 4: presence
PRESENCES = ("on_screen", "voice_only", "none")
# What the premise says about a character's body: "no body", "never drawn" -> none; "only a voice" -> voice_only.
_NO_BODY = re.compile(
    r"\b(?:no|without|lacking(?: any)?|has no)\s+(?:a\s+)?(?:physical\s+)?(?:body|form|avatar)\b|"
    r"\bnever (?:drawn|shown|depicted|seen)\b|\bbodiless\b|\bdisembodied\b|\bnon-?corporeal\b", re.I)
_VOICE_ONLY = re.compile(
    r"\bonly (?:as )?a voice\b|\bjust a voice\b|\bonly heard\b|\b(?:a )?voice on the (?:phone|line)\b|"
    r"\bnever (?:appears|seen) on screen\b|\boff-?screen\b", re.I)
# A person described: age, gender, body. Never allowed for a "none" character, nor in a shot of an absent one.
PERSON_TERMS = re.compile(
    r"\b\d+-year-old\b|\b(?:man|woman|girl|boy|lady|gentleman|person|robot|hologram|android|humanoid|"
    r"avatar)\b", re.I)
FILE_NAME = "visual_bible.json"
# Character reference sheets: bump when the portrait prompt changes; approvals of an older
# sheet version are not returned, so every character must be re-approved (locations stay valid).
CHARACTER_SHEET_VERSION = 2   # 2: neutral head-and-shoulders sheets, plain backdrop and top

# Moods, states and grooming disorder belong to single moments (shot appearance), never to the
# immutable descriptor; dirt, marks and body states never belong to an outfit.
_DESCRIPTOR_STATES = re.compile(
    r"\b(?:weary|tired|exhausted|sad|sorrowful|tearful|teary|anxious|worried|nervous|haunted|"
    r"dishevell?ed|messy|unkempt|tousled|escaping wisps|stray wisps|bruised|bloodied|bloody|"
    r"wounded|injured|scarred|dirty|grimy)\b")
_SCENE_ID = re.compile(r"\bs\d{2}\b", re.I)
_OUTFIT_BODY = re.compile(
    r"\b(?:soil|dirt|dirty|stains?|stained|blood\w*|marks?|bruis\w*|scratch\w*|wounds?|skin|"
    r"hands?|knuckles?|fingers?|fingertips?|traces? of|smudge\w*)\b")


def seed_for(kind: str, identifier: str) -> int:
    """Stable 31-bit seed per character/location id (reproducible across runs)."""
    digest = hashlib.sha256(f"private-gravity|{kind}|{identifier}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) & 0x7FFFFFFF


def _author_text(name: str, brief: dict | None) -> str:
    """The author brief's line about this character ("Ada, the AI companion app: ...")."""
    first = str(name or "").split()[0].lower() if str(name or "").split() else ""
    author = (brief or {}).get("author_brief") or {}
    for text in [author.get("protagonist", "")] + list(author.get("other_characters") or []):
        lead = re.match(r"\s*([A-Za-z][\w'-]*)", str(text))
        if first and lead and lead.group(1).lower() == first:
            return str(text)
    return ""


def deduce_presence(character: dict, brief: dict | None = None) -> str:
    """on_screen | voice_only | none from the premise (author brief first) and the character's own
    Character Bible fields (never its relationships, which describe other people)."""
    own = " ".join(str(value) for key, value in character.items()
                   if isinstance(value, str) and "relationship" not in key)
    for text in (_author_text(character.get("name", ""), brief), own):
        if _NO_BODY.search(text):
            return "none"
        if _VOICE_ONLY.search(text):
            return "voice_only"
    return "on_screen"


def presences(bible: dict, brief: dict | None = None, overrides: dict | None = None) -> dict:
    """character_id -> presence; ``overrides`` (kept in the visual bible) win over the deduction."""
    result = {item["character_id"]: deduce_presence(item, brief) for item in bible.get("characters", [])}
    result.update({cid: value for cid, value in (overrides or {}).items() if cid in result and value in PRESENCES})
    return result


def _scene_ids(outline: dict) -> list[str]:
    return [scene["scene_id"] for scene in outline.get("scenes", []) if scene.get("scene_id")]


def identity_problems(value: dict, narration: str = "", scenes: set | None = None,
                      presence: dict | None = None) -> list[str]:
    """Mood/state words in descriptors and body/dirt words in outfits (Phase 3.3.2).

    A scar is allowed in a descriptor only when the screenplay establishes one. Characters that are
    not on screen have no physical identity: only their proxy is checked (``absent_problems``).
    """
    problems = []
    permanent_scar = bool(re.search(r"\bscar(?:s|red)?\b", str(narration or "").lower()))
    for character in value.get("characters") or []:
        if not isinstance(character, dict):
            continue
        if _presence_of(character, presence) != "on_screen":
            problems += absent_problems(character, _presence_of(character, presence))
            continue
        cid = character.get("character_id")
        states = [word for word in dict.fromkeys(_DESCRIPTOR_STATES.findall(
            str(character.get("descriptor", "")).lower())) if not (permanent_scar and word == "scarred")]
        if states:
            problems.append(f"character {cid}: the descriptor is physical identity only; remove {states} "
                            "(moods, states and grooming belong to single shots)")
        for item in character.get("wardrobe") or []:
            body = list(dict.fromkeys(_OUTFIT_BODY.findall(str((item or {}).get("outfit", "")).lower())))
            if body:
                problems.append(f"character {cid}: outfit for {item.get('scene_ids')} is clothing and "
                                f"accessories only; remove {body} (story details belong to the shot "
                                "whose narration has them)")
            for field in ("outfit", "grooming"):
                if _SCENE_ID.search(str((item or {}).get(field, ""))):
                    problems.append(f"character {cid}: {field} for {item.get('scene_ids')} mentions scene ids; "
                                    "put per-scene states in scene_states")
        if not re.search(r"\b(?:man|woman|boy|girl|person|gentleman|lady)\b",
                         str(character.get("descriptor", "")).lower()):
            problems.append(f"character {cid}: start the descriptor with the apparent age and a gender noun "
                            "(e.g. 'a 37-year-old man') so shots can name the reference")
        markers = character.get("signature_markers")
        if not isinstance(markers, list) or not 1 <= len(markers) <= 3 \
                or not all(isinstance(item, str) and item.strip() for item in markers):
            problems.append(f"character {cid}: signature_markers must list 1 to 3 always-visible identity markers")
        states = character.get("scene_states", {})
        if not isinstance(states, dict) or not all(isinstance(text, str) for text in states.values()):
            problems.append(f"character {cid}: scene_states must map scene ids to a short state")
        elif scenes is not None and [sid for sid in states if sid not in scenes]:
            problems.append(f"character {cid}: scene_states has unknown scene ids "
                            f"{[sid for sid in states if sid not in scenes]}")
    return problems


def _presence_of(character: dict, presence: dict | None) -> str:
    return (presence or {}).get(character.get("character_id")) or character.get("presence") or "on_screen"


def absent_problems(character: dict, kind: str) -> list[str]:
    """A voice_only / none character needs a visual_proxy; a none character's is never a person
    (descriptors of absent characters are cleared in code)."""
    cid = character.get("character_id")
    problems = []
    if not str(character.get("visual_proxy", "")).strip():
        problems.append(f"character {cid} ({kind}): visual_proxy is required (what the frame shows instead)")
    found = sorted({match.lower() for match in PERSON_TERMS.findall(str(character.get("visual_proxy") or ""))})
    if kind == "none" and found:
        problems.append(f"character {cid} (none): visual_proxy must not describe a person {found}")
    return problems


def validate_visual_bible(value: dict, bible: dict, outline: dict, narration: str = "",
                          presence: dict | None = None) -> dict:
    from agents import identity
    if not isinstance(value, dict):
        raise NarrativeValidationError("visual bible must be an object")
    descriptions = json.dumps([value.get("characters"), value.get("locations"),
                               value.get("thumbnail_shot")], ensure_ascii=False)
    banned = identity.forbidden_terms(descriptions)
    if banned:
        raise NarrativeValidationError(f"visual bible mentions excluded subjects: {banned}")
    known_characters = {character["character_id"] for character in bible.get("characters", [])}
    scenes = set(_scene_ids(outline))
    characters = value.get("characters")
    if not isinstance(characters, list):
        raise NarrativeValidationError("visual bible requires a characters array")
    described = set()
    for index, character in enumerate(characters):
        cid = character.get("character_id") if isinstance(character, dict) else None
        if cid not in known_characters:
            raise NarrativeValidationError(f"characters[{index}].character_id {cid!r} is not in the Character Bible")
        if _presence_of(character, presence) != "on_screen":
            described.add(cid)
            continue
        if not str(character.get("descriptor", "")).strip():
            raise NarrativeValidationError(f"character {cid}: descriptor is required")
        wardrobe = character.get("wardrobe")
        if not isinstance(wardrobe, list) or not wardrobe:
            raise NarrativeValidationError(f"character {cid}: wardrobe must be a nonempty array")
        for item in wardrobe:
            unknown = [sid for sid in item.get("scene_ids", []) if sid not in scenes] \
                if isinstance(item, dict) else ["(not an object)"]
            if unknown or not str((item or {}).get("outfit", "")).strip():
                raise NarrativeValidationError(
                    f"character {cid}: wardrobe entries need an outfit and known scene_ids; "
                    f"unknown: {unknown}")
        described.add(cid)
    problems = identity_problems(value, narration, scenes, presence)
    if problems:
        raise NarrativeValidationError("visual bible: " + "; ".join(problems))
    missing = sorted(known_characters - described)
    if missing:
        raise NarrativeValidationError(f"visual bible is missing characters: {', '.join(missing)}")
    locations = value.get("locations")
    if not isinstance(locations, list) or not locations:
        raise NarrativeValidationError("visual bible requires a nonempty locations array")
    covered = set()
    for index, location in enumerate(locations):
        if not isinstance(location, dict) or not str(location.get("location_id", "")).strip() \
                or not str(location.get("descriptor", "")).strip():
            raise NarrativeValidationError(f"locations[{index}] needs location_id and descriptor")
        unknown = [sid for sid in location.get("scene_ids", []) if sid not in scenes]
        if unknown:
            raise NarrativeValidationError(f"location {location['location_id']}: unknown scene_ids {unknown}")
        covered.update(location.get("scene_ids", []))
        location["interior"] = location_interior(location)
    uncovered = sorted(scenes - covered)
    if uncovered:
        raise NarrativeValidationError(f"no location covers scenes: {', '.join(uncovered)}")
    thumb = value.get("thumbnail_shot")
    if not isinstance(thumb, dict) or not str(thumb.get("description", "")).strip():
        raise NarrativeValidationError("visual bible requires thumbnail_shot.description")
    absent = sorted(cid for cid in thumb.get("characters") or []
                    if (presence or {}).get(cid, "on_screen") != "on_screen")
    if absent:
        raise NarrativeValidationError(f"thumbnail_shot.characters lists characters who are never on screen: {absent}")
    return value


def location_interior(location: dict) -> bool:
    """Indoors? Keywords in the name and descriptor decide when they can (Phase 3.9.3-F); else the flag
    Gemini proposed; else indoors, the style's original setting."""
    from agents.prose_lint import is_exterior
    text = " ".join(str(location.get(key, "")) for key in ("location_id", "name", "descriptor")).replace("_", " ")
    found = is_exterior(text)
    if found is not None:
        return not found
    return bool(location.get("interior", True))


def backfill_interior(vb: dict) -> dict:
    """Give every location its ``interior`` flag (the source hash does not depend on it)."""
    for location in vb.get("locations") or []:
        location["interior"] = location_interior(location)
    return vb


def known_location_ids(existing: dict, output_dir) -> list[str]:
    """Location ids this project already used: the current visual bible, approvals and candidates."""
    ids = [item.get("location_id") for item in existing.get("locations") or []]
    keys = list(existing.get("references") or {}) + \
        list(load_json(Path(output_dir) / "reference_candidates.json") or {})
    ids += [key.split(":", 1)[1] for key in keys if key.startswith("location:")]
    return sorted({item for item in ids if item})


def keep_location_ids(value: dict, known: list[str]) -> dict:
    """Give a renamed location its earlier id ("front_room" -> "terraced_house_front_room") when exactly
    one known id shares all its words, so approved references and candidates still apply."""
    used = {item["location_id"] for item in value.get("locations") or []}
    renames = {}
    for location in value.get("locations") or []:
        new = location["location_id"]
        if new in known:
            continue
        words = set(new.split("_"))
        matches = [old for old in known if old not in used and old not in renames.values()
                   and (words <= set(old.split("_")) or set(old.split("_")) <= words)]
        if len(matches) == 1:
            renames[new] = matches[0]
    for location in value.get("locations") or []:
        location["location_id"] = renames.get(location["location_id"], location["location_id"])
    thumb = value.get("thumbnail_shot") or {}
    if thumb.get("location_id") in renames:
        thumb["location_id"] = renames[thumb["location_id"]]
    return renames


def source_hash_for(script: dict, bible: dict, outline: dict) -> str:
    """What the visual bible depends on (also used to report a stale one)."""
    from agents import identity
    return stable_hash({
        "version": VISUAL_BIBLE_VERSION, "style": identity.visual_style(),
        "bible": bible.get("characters"),
        "outline": [(scene.get("scene_id"), scene.get("setting")) for scene in outline.get("scenes", [])],
        "narration": [scene.get("narration") for scene in script.get("sections", [])],
    })


def build_visual_bible(script: dict, brief: dict, bible: dict, outline: dict, output_dir,
                       force: bool = False) -> dict:
    output = Path(output_dir)
    path = output / FILE_NAME
    from agents import identity
    source_hash = source_hash_for(script, bible, outline)
    narration = " ".join(str(scene.get("narration", "")) for scene in script.get("sections", []))
    existing = load_json(path) or {}
    if not force and existing.get("source_hash") == source_hash:
        return existing
    overrides = existing.get("presence_overrides") or {}
    fixed = presences(bible, brief, overrides)
    known = known_location_ids(existing, output)
    contract = f"""You are the production designer of Private Gravity, an anthology of dark
psychological fiction. From the CHARACTER BIBLE, OUTLINE and SCREENPLAY supplied after this
contract, write the episode's VISUAL BIBLE used to prompt the image model. The drawing style itself
is fixed elsewhere; describe only people, clothes, places and the thumbnail moment.

Return ONLY JSON:
{{"characters": [{{"character_id": "...", "descriptor": "...", "visual_proxy": "", "signature_markers": ["..."],
                   "wardrobe": [{{"scene_ids": ["s01"], "outfit": "...", "grooming": "..."}}],
                   "scene_states": {{"s05": "..."}}}}],
  "locations": [{{"location_id": "...", "name": "...", "descriptor": "...", "interior": true, "scene_ids": ["s01"]}}],
  "thumbnail_shot": {{"description": "...", "characters": ["character_id"], "location_id": "..."}}}}

Rules:
- {identity.image_prompt_rules()}
- Every Character Bible character gets ONE immutable descriptor = physical identity only,
  starting with the apparent age and a gender noun ("a 37-year-old man", "a 35-year-old woman"):
  apparent age consistent with the bible's age, ethnicity as stated in the bible or chosen once,
  build, face shape, eye colour, hair colour, length and default style, distinguishing features.
  Never contradict the bible. NO emotions, moods or states (weary, tired, sad, tearful, anxious),
  no grooming disorder (dishevelled, messy, escaping wisps), no injuries, dirt or story details;
  a scar only when the screenplay establishes a permanent one. Those belong to single shots.
- wardrobe[].outfit: clothing and accessories only, exactly consistent with the screenplay for
  each scene (if the screenplay names a garment, use it; never invent garments the text
  contradicts, e.g. no cardigan when the screenplay says she wears an ivory silk shirt). Cover
  every scene in which the character appears. NO skin, hands, dirt, stains, marks or body states
  in an outfit: a story detail such as soil on her fingers while repotting belongs only to the
  shot whose narration has it. grooming: hair, make-up and bearing in those scenes, following the
  character's state in the narration (composed people are poised and well groomed); never marks,
  dirt, injuries or tears the screenplay does not state.
- signature_markers: 1 to 3 short, always-visible identity markers that must appear in every shot
  of the character (e.g. "horn-rimmed glasses", "gold signet ring"): objects or fixed physical
  traits, never clothing that changes between scenes, never moods.
- scene_states: an object mapping a scene_id to the character's visible state in that scene only
  (e.g. {{"s05": "wet tear streaks on her cheeks"}}), only where the narration states it. outfit and
  grooming never mention scene ids ("in scene s05"): per-scene states go here.
- locations: every recurring set gets one descriptor reused in every shot set there (materials,
  light, scale, objects), keeping the social status the story gives it (an expensive minimal loft
  is immaculate: polished surfaces, precise objects, pristine walls; never raw, grimy, stained,
  rusted or ruined unless the screenplay says so). Every outline scene_id must belong to a location.
- PRESENCE is fixed by the premise and is not yours to change: {json.dumps(fixed)}.
  on_screen characters follow the rules above. A "none" character has no body (e.g. an AI app):
  descriptor "", no person, age, gender, face, robot or hologram anywhere; wardrobe [] and
  signature_markers []; visual_proxy = the object or light that stands for it in a frame (e.g.
  "the warm gold glow of Walter's phone screen"). A "voice_only" character is only heard (a voice on
  the phone): descriptor "", wardrobe [], signature_markers []; visual_proxy = what the frame shows
  while they speak (the listener holding the phone). visual_proxy is "" for on_screen characters.
- EXISTING location_ids (approved reference images depend on them): {json.dumps(known)}. Reuse the
  same location_id for the same place; invent a new id only for a new place.
- thumbnail_shot: the strongest emotional image of the episode, a human focal point (a partial
  face is fine), with negative space on the left for a short line of text; its characters are
  on_screen characters only."""
    context = f"""CHARACTER BIBLE: {compact_json(bible)}
OUTLINE: {compact_json([{key: scene.get(key) for key in ("scene_id", "title", "setting",
                         "characters_present", "summary")} for scene in outline.get("scenes", [])])}
SCREENPLAY: {compact_json([{"scene_id": scene.get("outline_scene_id"), "narration": scene.get("narration")}
                           for scene in script.get("sections", [])])}"""
    value = structured.generate_json(
        contract, "visual bible", lambda data: validate_visual_bible(data, bible, outline, narration, fixed),
        generator=generate, context=context, stage="visual_bible",
        temperature_key="character_bible", run_dir=str(output),
    )
    value["global_style"] = {"style": identity.visual_style(), "variant": identity.style_variant(),
                             "style_hash": identity.style_hash(), "style_suffix": identity.style_suffix(),
                             "exclusions": list(identity.negative_terms())}
    for character in value["characters"]:
        character["seed"] = seed_for("character", character["character_id"])
        character["presence"] = fixed.get(character["character_id"], "on_screen")
        if character["presence"] != "on_screen":
            character.update(descriptor="", wardrobe=[], signature_markers=[], scene_states={})
        else:
            character.pop("visual_proxy", None)
    if overrides:
        value["presence_overrides"] = overrides
    keep_location_ids(value, known)
    for location in value["locations"]:
        location["seed"] = seed_for("location", location["location_id"])
    value["thumbnail_shot"]["seed"] = seed_for("thumbnail", "episode")
    previous = existing.get("references") or {}
    # A bodiless character never keeps a portrait; a voice-only one keeps its approval (unused in shots).
    ids = {("character", c["character_id"]) for c in value["characters"] if c["presence"] != "none"} | \
          {("location", l["location_id"]) for l in value["locations"]}
    value["references"] = {key: ref for key, ref in previous.items()
                           if tuple(key.split(":", 1)) in ids}
    value.update({"version": VISUAL_BIBLE_VERSION, "source_hash": source_hash})
    atomic_write_json(path, value)
    return value


# ── Lookup helpers ─────────────────────────────────────────────────────────

def character(vb: dict, character_id: str) -> dict | None:
    return next((item for item in vb.get("characters", []) if item["character_id"] == character_id), None)


def presence(vb: dict, character_id: str) -> str:
    return (character(vb, character_id) or {}).get("presence") or "on_screen"


def on_screen(vb: dict) -> list[dict]:
    """Characters that can be drawn (references, shots, head counts, thumbnails)."""
    return [item for item in vb.get("characters", []) if (item.get("presence") or "on_screen") == "on_screen"]


def absent(vb: dict) -> list[dict]:
    """voice_only and none characters: never in a frame."""
    return [item for item in vb.get("characters", []) if (item.get("presence") or "on_screen") != "on_screen"]


def set_presence(output_dir, character_id: str, value: str) -> dict:
    """Override the deduced presence (kept across visual-bible rebuilds); the visual bible is rebuilt next."""
    if value not in PRESENCES:
        raise ValueError(f"presence must be one of {PRESENCES}")
    path = Path(output_dir) / FILE_NAME
    vb = load_json(path) or {}
    vb.setdefault("presence_overrides", {})[character_id] = value
    vb["source_hash"] = None                      # rebuild with the new presence
    atomic_write_json(path, vb)
    return vb


def wardrobe(vb: dict, character_id: str, scene_id: str) -> str:
    entry = character(vb, character_id) or {}
    for item in entry.get("wardrobe", []):
        if scene_id in item.get("scene_ids", []):
            return item["outfit"]
    return entry.get("wardrobe", [{}])[0].get("outfit", "") if entry.get("wardrobe") else ""


def grooming(vb: dict, character_id: str, scene_id: str) -> str:
    """Optional per-scene grooming (visual bibles before Phase 3.3 have none)."""
    entry = character(vb, character_id) or {}
    for item in entry.get("wardrobe", []):
        if scene_id in item.get("scene_ids", []):
            return str(item.get("grooming") or "")
    return ""


def signature_markers(vb: dict, character_id: str) -> list[str]:
    """Visible identity markers that must appear in every shot of the character (Phase 3.4)."""
    markers = (character(vb, character_id) or {}).get("signature_markers") or []
    return [str(item).strip() for item in markers if str(item).strip()][:3] if isinstance(markers, list) else []


def scene_state(vb: dict, character_id: str, scene_id: str) -> str:
    """The character's state in this scene only (e.g. "wet tear streaks"), from scene_states."""
    states = (character(vb, character_id) or {}).get("scene_states") or {}
    return str(states.get(scene_id) or "").strip() if isinstance(states, dict) else ""


def location(vb: dict, location_id: str) -> dict | None:
    return next((item for item in vb.get("locations", []) if item["location_id"] == location_id), None)


def location_for_scene(vb: dict, scene_id: str) -> dict | None:
    return next((item for item in vb.get("locations", []) if scene_id in item.get("scene_ids", [])), None)


def recurring_locations(vb: dict) -> list[dict]:
    return [item for item in vb.get("locations", []) if len(item.get("scene_ids", [])) >= 2]


def reference_key(kind: str, identifier: str) -> str:
    return f"{kind}:{identifier}"


def approved_reference(vb: dict, kind: str, identifier: str, output_dir) -> str | None:
    """Path of the approved reference image for the CURRENT style, if it exists on disk.

    References are style-specific: an approval made under another style (or a
    legacy photographic one without a style hash) is not returned.
    """
    from agents import identity
    entry = (vb.get("references") or {}).get(reference_key(kind, identifier))
    if not entry:
        return None
    if kind in ("character", "body") and presence(vb, identifier) == "none":
        return None          # a character with no body never has a portrait
    if entry.get("style_hash", identity.style_hash("cinematic_photo")) != identity.style_hash():
        return None
    if kind == "character" and entry.get("sheet_version", 1) != CHARACTER_SHEET_VERSION:
        return None          # an older character sheet: re-approve a neutral one
    path = Path(output_dir) / entry["path"]
    return str(path) if path.is_file() else None


def record_approval(vb: dict, kind: str, identifier: str, relative_path: str, *, seed: int,
                    prompt: str, output_dir) -> dict:
    from agents import identity
    vb.setdefault("references", {})[reference_key(kind, identifier)] = {
        "path": relative_path, "seed": seed, "prompt": prompt,
        "style": identity.visual_style(), "style_hash": identity.style_hash(),
        **({"sheet_version": CHARACTER_SHEET_VERSION} if kind == "character" else {}),
    }
    atomic_write_json(Path(output_dir) / FILE_NAME, vb)
    return vb
