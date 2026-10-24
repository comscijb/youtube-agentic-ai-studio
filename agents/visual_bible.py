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
from pathlib import Path

import config
from agents import structured
from agents.gemini_client import generate
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash
from agents.structured import compact_json

VISUAL_BIBLE_VERSION = 1
FILE_NAME = "visual_bible.json"


def seed_for(kind: str, identifier: str) -> int:
    """Stable 31-bit seed per character/location id (reproducible across runs)."""
    digest = hashlib.sha256(f"private-gravity|{kind}|{identifier}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) & 0x7FFFFFFF


def _scene_ids(outline: dict) -> list[str]:
    return [scene["scene_id"] for scene in outline.get("scenes", []) if scene.get("scene_id")]


def validate_visual_bible(value: dict, bible: dict, outline: dict) -> dict:
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


def build_visual_bible(script: dict, brief: dict, bible: dict, outline: dict, output_dir,
                       force: bool = False) -> dict:
    output = Path(output_dir)
    path = output / FILE_NAME
    from agents import identity
    source_hash = stable_hash({
        "version": VISUAL_BIBLE_VERSION, "style": identity.visual_style(),
        "bible": bible.get("characters"),
        "outline": [(scene.get("scene_id"), scene.get("setting")) for scene in outline.get("scenes", [])],
        "narration": [scene.get("narration") for scene in script.get("sections", [])],
    })
    existing = load_json(path) or {}
    if not force and existing.get("source_hash") == source_hash:
        return existing
    contract = f"""You are the production designer of Private Gravity, an anthology of dark
psychological fiction. From the CHARACTER BIBLE, OUTLINE and SCREENPLAY supplied after this
contract, write the episode's VISUAL BIBLE used to prompt the image model. The drawing style itself
is fixed elsewhere; describe only people, clothes, places and the thumbnail moment.

Return ONLY JSON:
{{"characters": [{{"character_id": "...", "descriptor": "...",
                   "wardrobe": [{{"scene_ids": ["s01"], "outfit": "...", "grooming": "..."}}]}}],
  "locations": [{{"location_id": "...", "name": "...", "descriptor": "...", "scene_ids": ["s01"]}}],
  "thumbnail_shot": {{"description": "...", "characters": ["character_id"], "location_id": "..."}}}}

Rules:
- {identity.image_prompt_rules()}
- Every Character Bible character gets ONE immutable descriptor: apparent age consistent with
  the bible's age, ethnicity as stated in the bible or chosen once, build, face shape, hair,
  distinguishing features. Never contradict the bible.
- wardrobe: what each character wears in each scene, exactly consistent with the screenplay
  (if the screenplay names a garment, use it; never invent garments the text contradicts, e.g.
  no cardigan when the screenplay says she wears an ivory silk shirt). Cover every scene in which
  the character appears. grooming: hair, make-up and bearing in those scenes, following the
  character's state in the narration (composed people are poised and well groomed); never marks,
  dirt, injuries or tears the screenplay does not state.
- The immutable descriptor is physical only (age, build, face, hair, features): no mood words
  such as weary or tearful, which belong to single moments.
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
        contract, "visual bible", lambda data: validate_visual_bible(data, bible, outline),
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
    path = Path(output_dir) / entry["path"]
    return str(path) if path.is_file() else None


def record_approval(vb: dict, kind: str, identifier: str, relative_path: str, *, seed: int,
                    prompt: str, output_dir) -> dict:
    from agents import identity
    vb.setdefault("references", {})[reference_key(kind, identifier)] = {
        "path": relative_path, "seed": seed, "prompt": prompt,
        "style": identity.visual_style(), "style_hash": identity.style_hash(),
    }
    atomic_write_json(Path(output_dir) / FILE_NAME, vb)
    return vb
