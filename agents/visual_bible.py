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

VISUAL_BIBLE_VERSION = 3   # 2: identity-only descriptors, clothing-only outfits (3.3.2); 3: signature markers, scene states (3.4)
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


def _scene_ids(outline: dict) -> list[str]:
    return [scene["scene_id"] for scene in outline.get("scenes", []) if scene.get("scene_id")]


def identity_problems(value: dict, narration: str = "", scenes: set | None = None) -> list[str]:
    """Mood/state words in descriptors and body/dirt words in outfits (Phase 3.3.2).

    A scar is allowed in a descriptor only when the screenplay establishes one.
    """
    problems = []
    permanent_scar = bool(re.search(r"\bscar(?:s|red)?\b", str(narration or "").lower()))
    for character in value.get("characters") or []:
        if not isinstance(character, dict):
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


def validate_visual_bible(value: dict, bible: dict, outline: dict, narration: str = "") -> dict:
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
    problems = identity_problems(value, narration, scenes)
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
    uncovered = sorted(scenes - covered)
    if uncovered:
        raise NarrativeValidationError(f"no location covers scenes: {', '.join(uncovered)}")
    thumb = value.get("thumbnail_shot")
    if not isinstance(thumb, dict) or not str(thumb.get("description", "")).strip():
        raise NarrativeValidationError("visual bible requires thumbnail_shot.description")
    return value


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
    contract = f"""You are the production designer of Private Gravity, an anthology of dark
psychological fiction. From the CHARACTER BIBLE, OUTLINE and SCREENPLAY supplied after this
contract, write the episode's VISUAL BIBLE used to prompt the image model. The drawing style itself
is fixed elsewhere; describe only people, clothes, places and the thumbnail moment.

Return ONLY JSON:
{{"characters": [{{"character_id": "...", "descriptor": "...", "signature_markers": ["..."],
                   "wardrobe": [{{"scene_ids": ["s01"], "outfit": "...", "grooming": "..."}}],
                   "scene_states": {{"s05": "..."}}}}],
  "locations": [{{"location_id": "...", "name": "...", "descriptor": "...", "scene_ids": ["s01"]}}],
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
- thumbnail_shot: the strongest emotional image of the episode, a human focal point (a partial
  face is fine), with negative space on the left for a short line of text."""
    context = f"""CHARACTER BIBLE: {compact_json(bible)}
OUTLINE: {compact_json([{key: scene.get(key) for key in ("scene_id", "title", "setting",
                         "characters_present", "summary")} for scene in outline.get("scenes", [])])}
SCREENPLAY: {compact_json([{"scene_id": scene.get("outline_scene_id"), "narration": scene.get("narration")}
                           for scene in script.get("sections", [])])}"""
    value = structured.generate_json(
        contract, "visual bible", lambda data: validate_visual_bible(data, bible, outline, narration),
        generator=generate, context=context, stage="visual_bible",
        temperature_key="character_bible", run_dir=str(output),
    )
    value["global_style"] = {"style": identity.visual_style(), "variant": identity.style_variant(),
                             "style_hash": identity.style_hash(), "style_suffix": identity.style_suffix(),
                             "exclusions": list(identity.negative_terms())}
    for character in value["characters"]:
        character["seed"] = seed_for("character", character["character_id"])
    for location in value["locations"]:
        location["seed"] = seed_for("location", location["location_id"])
    value["thumbnail_shot"]["seed"] = seed_for("thumbnail", "episode")
    previous = existing.get("references") or {}
    ids = {("character", c["character_id"]) for c in value["characters"]} | \
          {("location", l["location_id"]) for l in value["locations"]}
    value["references"] = {key: ref for key, ref in previous.items()
                           if tuple(key.split(":", 1)) in ids}
    value.update({"version": VISUAL_BIBLE_VERSION, "source_hash": source_hash})
    atomic_write_json(path, value)
    return value


# ── Lookup helpers ─────────────────────────────────────────────────────────

def character(vb: dict, character_id: str) -> dict | None:
    return next((item for item in vb.get("characters", []) if item["character_id"] == character_id), None)


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
