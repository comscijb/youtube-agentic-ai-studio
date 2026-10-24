"""Shot list aligned to the narration (``shot_list.json``).

Built after narration because it needs real timings:

1. Sentences come from the word-timed SRT (cues never cross a sentence).
2. Each scene is cut on sentence boundaries into segments of
   IMAGEGEN_SHOT_MIN_SECONDS-IMAGEGEN_SHOT_MAX_SECONDS (6-12 s), so every image
   matches what is being said; segments tile the scene timeline exactly.
3. If there are more segments than IMAGEGEN_MAX_UNIQUE_SHOTS, the shortest
   adjacent pairs inside a scene are merged until the budget fits.
4. One Gemini call (contract first, JSON mode, validated) decides per segment
   the shot type, characters, location, visible action/composition, face
   visibility and an optional re-use of an earlier shot.
5. Prompts and reference slots are composed in code from the visual bible
   (``compose_shot``), so identity text is never left to the model.

Version 2 (Phase 3.3) adds, per shot, each visible character's grooming and
expression at that moment and whether unnamed people are present, and per
location the condition the story gives it; injury, decay and tears words the
narration does not contain are rejected.
"""
from __future__ import annotations

import re
from pathlib import Path

import config
from agents import structured
from agents import visual_bible as vbible
from agents.gemini_client import generate
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash
from agents.structured import compact_json

SHOT_LIST_VERSION = 2   # the shot-prompt version: bumping it re-plans and regenerates shots
FILE_NAME = "shot_list.json"
SHOT_TYPES = ("wide", "establishing", "medium", "close_up", "insert", "over_the_shoulder",
              "silhouette", "hands")
FACE_VISIBILITY = ("none", "partial", "full")
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
ORDINALS = ("first", "second", "third", "fourth")
FRAMING = {
    "wide": "wide shot, the figures small within towering architecture",
    "establishing": "establishing wide shot of the vast space, any figure tiny",
    "medium": "medium shot, waist up",
    "close_up": "close-up on the face and shoulders against a dark background lit by one light source",
    "insert": "insert close-up of the object",
    "over_the_shoulder": "over-the-shoulder shot",
    "silhouette": "silhouette against the light, face not visible",
    "hands": "close-up of the hands only, face out of frame",
}


# ── Timing ──────────────────────────────────────────────────────────────────

def sentences_from_cues(cues: list[tuple]) -> list[dict]:
    """Join SRT cues ((start_ms, end_ms, text)) into sentences in seconds."""
    sentences, current = [], []
    for start, end, text in cues:
        current.append((start, end, text))
        if _SENTENCE_END.search(text.strip()):
            sentences.append({"start": current[0][0] / 1000, "end": current[-1][1] / 1000,
                              "text": " ".join(item[2] for item in current)})
            current = []
    if current:
        sentences.append({"start": current[0][0] / 1000, "end": current[-1][1] / 1000,
                          "text": " ".join(item[2] for item in current)})
    return sentences


def segment_scene(scene: dict, sentences: list[dict], min_s: float, max_s: float) -> list[dict]:
    """Cut one scene on sentence boundaries; segments tile [scene.start, scene.end]."""
    inside = [s for s in sentences if scene["start"] - 0.05 <= s["start"] < scene["end"]]
    if not inside:
        return [{"start": scene["start"], "end": scene["end"], "text": ""}]
    groups, current = [], []
    for sentence in inside:
        if current:
            length = sentence["end"] - current[0]["start"]
            if length > max_s and current[-1]["end"] - current[0]["start"] >= min_s:
                groups.append(current)
                current = []
        current.append(sentence)
        if current[-1]["end"] - current[0]["start"] >= max_s:
            groups.append(current)
            current = []
    if current:
        if groups and current[-1]["end"] - current[0]["start"] < min_s / 2:
            groups[-1].extend(current)
        else:
            groups.append(current)
    segments = []
    for index, group in enumerate(groups):
        start = scene["start"] if index == 0 else segments[-1]["end"]
        end = scene["end"] if index == len(groups) - 1 else group[-1]["end"]
        segments.append({"start": round(start, 3), "end": round(end, 3),
                         "text": " ".join(item["text"] for item in group)})
    return segments


def enforce_budget(segments: list[dict], budget: int) -> list[dict]:
    """Merge the shortest adjacent pair within a scene until ``len <= budget``."""
    segments = [dict(segment) for segment in segments]
    while len(segments) > max(1, budget):
        best = None
        for index in range(len(segments) - 1):
            a, b = segments[index], segments[index + 1]
            if a["scene_id"] != b["scene_id"]:
                continue
            length = b["end"] - a["start"]
            if best is None or length < best[0]:
                best = (length, index)
        if best is None:
            break
        index = best[1]
        a, b = segments[index], segments.pop(index + 1)
        a["end"] = b["end"]
        a["text"] = f"{a['text']} {b['text']}".strip()
    return segments


def plan_segments(script: dict, timeline: dict, cues: list[tuple]) -> list[dict]:
    min_s = float(getattr(config, "IMAGEGEN_SHOT_MIN_SECONDS", 6.0))
    max_s = float(getattr(config, "IMAGEGEN_SHOT_MAX_SECONDS", 12.0))
    sentences = sentences_from_cues(cues)
    segments = []
    for scene in timeline.get("scenes", []):
        for segment in segment_scene(scene, sentences, min_s, max_s):
            segment.update({"scene_id": scene.get("outline_scene_id") or f"s{int(scene['scene_id']):02d}",
                            "section_id": scene["scene_id"]})
            segments.append(segment)
    segments = enforce_budget(segments, int(getattr(config, "IMAGEGEN_MAX_UNIQUE_SHOTS", 50)))
    counters: dict[str, int] = {}
    for segment in segments:
        counters[segment["scene_id"]] = counters.get(segment["scene_id"], 0) + 1
        segment["shot_id"] = f"{segment['scene_id']}_{counters[segment['scene_id']]:02d}"
    return segments


# ── Planning with Gemini ────────────────────────────────────────────────────

def validate_shots(value: dict, segments: list[dict], vb: dict, outline: dict, policy: str) -> list[dict]:
    shots = value.get("shots")
    ids = [segment["shot_id"] for segment in segments]
    if not isinstance(shots, list) or [shot.get("shot_id") for shot in shots if isinstance(shot, dict)] != ids:
        raise NarrativeValidationError(f"shot list must return exactly these shot_ids in order: {ids}")
    characters = {item["character_id"] for item in vb.get("characters", [])}
    locations = {item["location_id"] for item in vb.get("locations", [])}
    excerpts = {segment["shot_id"]: segment.get("text", "") for segment in segments}
    seen = set()
    problems = []
    from agents import identity
    settings = value.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        problems.append("settings must map every location_id used to its condition")
    used = {shot.get("location_id") for shot in shots if isinstance(shot, dict)}
    for lid in sorted(lid for lid in used if lid in locations):
        condition = str(settings.get(lid) or "").strip()
        if not condition:
            problems.append(f"settings.{lid}: describe the condition the story gives this place")
        unsupported = identity.unsupported_terms(condition, " ".join(excerpts.values()))
        if unsupported:
            problems.append(f"settings.{lid}: the narration never mentions {unsupported}")
    for shot in shots:
        sid = shot["shot_id"]
        appearance = shot.get("appearance") if isinstance(shot.get("appearance"), dict) else {}
        missing = [cid for cid in shot.get("characters") or [] if not str(appearance.get(cid, "")).strip()]
        if missing:
            problems.append(f"{sid}: appearance needs grooming and expression for {missing}")
        if not isinstance(shot.get("others_present"), bool):
            problems.append(f"{sid}: others_present must be true or false")
        elif not shot["others_present"] and _has_unnamed_people(shot.get("action", "")):
            problems.append(f"{sid}: the action shows unnamed people but others_present is false")
        visible = " ".join([str(shot.get("action", ""))] + [str(text) for text in appearance.values()])
        unsupported = identity.unsupported_terms(visible, excerpts.get(sid, ""))
        if unsupported:
            problems.append(f"{sid}: the narration at this moment never mentions {unsupported}; "
                            "no injuries, decay or tears it does not state")
        banned = identity.forbidden_terms(shot.get("action", ""))
        if banned:
            problems.append(f"{sid}: the action mentions excluded subjects {banned}")
        layout = identity.layout_terms(shot.get("action", ""))
        if layout:
            problems.append(f"{sid}: the action asks for a page layout {layout}; describe one single image")
        if shot.get("shot_type") not in SHOT_TYPES:
            problems.append(f"{sid}: shot_type must be one of {SHOT_TYPES}")
        if shot.get("face_visibility") not in FACE_VISIBILITY:
            problems.append(f"{sid}: face_visibility must be one of {FACE_VISIBILITY}")
        unknown = [cid for cid in shot.get("characters") or [] if cid not in characters]
        if unknown or not isinstance(shot.get("characters", []), list):
            problems.append(f"{sid}: unknown characters {unknown}")
        if shot.get("location_id") not in locations:
            problems.append(f"{sid}: location_id must be one of {sorted(locations)}")
        if not str(shot.get("action", "")).strip():
            problems.append(f"{sid}: action is required")
        reuse = shot.get("reuse_of") or ""
        if reuse and reuse not in seen:
            problems.append(f"{sid}: reuse_of must name an earlier shot")
        seen.add(sid)
    wide = sum(1 for shot in shots if shot.get("shot_type") in {"wide", "establishing"})
    min_wide = float(getattr(config, "IMAGEGEN_MIN_WIDE_SHARE", 0.4))
    if shots and wide / len(shots) < min_wide:
        problems.append(f"at least {min_wide:.0%} of shots must be wide/establishing with small figures; "
                        f"got {wide}/{len(shots)}")
    full = sum(1 for shot in shots if shot.get("face_visibility") == "full")
    if policy == "limited_faces":
        limit = int(getattr(config, "IMAGEGEN_MAX_FULL_FACE_SHOTS", 6))
        if full > limit:
            problems.append(f"at most {limit} full-face shots under the limited_faces policy; got {full}")
    elif shots and (len(shots) - full) / len(shots) < 0.35:
        problems.append(f"at least 40% of shots must not show a full face; got {len(shots) - full}/{len(shots)}")
    if problems:
        raise NarrativeValidationError("shot list problems: " + "; ".join(problems[:20]))
    for shot in shots:
        shot["setting_condition"] = str(settings.get(shot.get("location_id")) or "").strip()
    return shots


def build_shot_list(script: dict, outline: dict, vb: dict, output_dir, force: bool = False) -> dict:
    output = Path(output_dir)
    path = output / FILE_NAME
    timeline = load_json(output / "scene_timeline.json")
    if not timeline:
        raise FileNotFoundError("scene_timeline.json is missing: run the narration step first")
    from video.timeline import parse_srt
    cues = parse_srt(output / "narration.srt")
    policy = str(getattr(config, "IMAGEGEN_IDENTITY_POLICY", "reference"))
    segments = plan_segments(script, timeline, cues)
    from agents import identity
    source_hash = stable_hash({"version": SHOT_LIST_VERSION, "segments": segments,
                               "visual_bible": vb.get("source_hash"), "policy": policy,
                               "style": identity.visual_style()})
    existing = load_json(path) or {}
    if not force and existing.get("source_hash") == source_hash:
        return existing
    scene_cast = {scene["scene_id"]: scene.get("characters_present", []) for scene in outline.get("scenes", [])}
    scene_info = {scene["scene_id"]: {"setting": scene.get("setting", ""), "summary": scene.get("summary", "")}
                  for scene in outline.get("scenes", [])}
    policy_rule = (
        "Faces may be visible; between 40% and 60% of shots must NOT show a full face "
        "(hands, silhouettes, over-the-shoulder, objects, wides)."
        if policy != "limited_faces" else
        f"Identity retention is weak: use face_visibility none or partial, with at most "
        f"{getattr(config, 'IMAGEGEN_MAX_FULL_FACE_SHOTS', 6)} full-face hero shots."
    )
    contract = f"""You are the director of photography of Private Gravity, a dark cinematic anthology.
For every narration SEGMENT supplied after this contract, design one still image that shows what is
being said at that moment. Return ONLY JSON {{"settings": {{...}}, "shots": [...]}}.
settings maps every location_id you use to one sentence on its condition as the story gives it
(an expensive minimal loft: "immaculate and expensive: polished surfaces, precise objects,
pristine walls"); never decay, grime, stains, rust or ruin unless the narration states them.
shots has one object per segment, same order, keys: shot_id (unchanged), shot_type
({", ".join(SHOT_TYPES)}), characters (character_ids visible in the frame, from the scene cast),
location_id (from the visual bible), action (what is visible: pose, gesture, objects, light and
composition in one concrete sentence; no camera jargon beyond the framing; never describe faces
or clothes, the visual bible supplies them), appearance (an object mapping every character in
the shot to their grooming and expression at this exact moment, following the narration: a
composed character is "poised, hair neatly pinned, composed expression"; tears, dishevelment,
dirt or marks only where the narration has them), others_present (true only when the narration
or the scene's chronology places unnamed people — guests, staff, a crowd — in the room at this
moment; before the guests arrive it is false and the named characters are alone),
face_visibility (none | partial | full) and reuse_of ("" or the shot_id of an EARLIER shot when
showing the same image again is dramatically right, e.g. a motif returning).
Mix wides, mediums, close-ups, inserts of objects, hands, silhouettes and over-the-shoulder shots;
avoid two consecutive shots with the same framing. At least {getattr(config, "IMAGEGEN_MIN_WIDE_SHARE", 0.4):.0%}
of shots are wide or establishing, with figures small inside vast, towering spaces; close-ups keep a
dark background with a single light source. {policy_rule}
{identity.image_prompt_rules()}"""
    context = f"""VISUAL BIBLE: {compact_json({key: vb.get(key) for key in ("characters", "locations")})}
SEGMENTS: {compact_json([{"shot_id": s["shot_id"], "scene_id": s["scene_id"],
                         "seconds": round(s["end"] - s["start"], 1), "narration": s["text"],
                         "scene_cast": scene_cast.get(s["scene_id"], []),
                         "scene_setting": scene_info.get(s["scene_id"], {}).get("setting", ""),
                         "scene_summary": scene_info.get(s["scene_id"], {}).get("summary", ""),
                         "default_location": (vbible.location_for_scene(vb, s["scene_id"]) or {}).get("location_id")}
                        for s in segments])}"""
    shots = structured.generate_json(
        contract, "shot list", lambda data: validate_shots(data, segments, vb, outline, policy),
        generator=generate, context=context, stage="shot_list",
        temperature_key="outline", run_dir=str(output),
    )
    planned = []
    for segment, shot in zip(segments, shots):
        entry = {
            "shot_id": segment["shot_id"], "scene_id": segment["scene_id"],
            "section_id": segment["section_id"], "start": segment["start"], "end": segment["end"],
            "narration_excerpt": segment["text"], "shot_type": shot["shot_type"],
            "characters": list(shot.get("characters") or []), "location": shot["location_id"],
            "action": shot["action"], "face_visibility": shot["face_visibility"],
            "appearance": {cid: str(text) for cid, text in (shot.get("appearance") or {}).items()
                           if cid in (shot.get("characters") or [])},
            "others_present": shot["others_present"], "setting_condition": shot.get("setting_condition", ""),
            "reuse_of": shot.get("reuse_of") or "", "seed": vbible.seed_for("shot", segment["shot_id"]),
            "seed_offset": 0,
        }
        composed = compose_shot(entry, vb, output, assume_references=True)
        entry["prompt"] = composed["prompt"]
        entry["reference_slots"] = composed["slots"]
        planned.append(entry)
    result = {
        "version": SHOT_LIST_VERSION, "source_hash": source_hash, "policy": policy,
        "budget": int(getattr(config, "IMAGEGEN_MAX_UNIQUE_SHOTS", 50)),
        "unique_shots": sum(1 for shot in planned if not shot["reuse_of"]), "shots": planned,
    }
    atomic_write_json(path, result)
    return result


# ── Prompt composition ──────────────────────────────────────────────────────

def _noun(descriptor: str) -> str:
    words = set(re.findall(r"[a-z]+", descriptor.lower()))
    if words & {"woman", "female", "girl"}:
        return "woman"
    if words & {"man", "male", "boy"}:
        return "man"
    return "person"


def compose_shot(shot: dict, vb: dict, output_dir, *, assume_references: bool = False) -> dict:
    """Template kind, final prompt, reference image paths, slot plan and seed for one shot.

    Reference slots in ReferenceMultiLatent (max 4), in order: characters in
    the shot with approved portraits (at most 2, the most important first),
    then the approved location reference (at most 1, not for inserts/hands),
    then one approved style anchor when a slot is still free. Each is named by
    its ordinal in the prompt.
    """
    from agents import identity, style_refs

    style = identity.style_suffix()
    noun = identity.image_noun()
    framing = FRAMING.get(shot.get("shot_type"), "")
    characters = list(shot.get("characters") or [])
    use_faces = shot.get("face_visibility") != "none" or shot.get("shot_type") in {"hands", "silhouette"}
    references, slots, ref_names = [], [], {}
    for cid in characters:
        if len(ref_names) >= 2 or not use_faces:
            break
        path = vbible.approved_reference(vb, "character", cid, output_dir)
        if path or assume_references:
            references.append(path or f"character_refs/{cid}.png")
            slots.append({"slot": len(references) - 1, "kind": "character", "id": cid})
            entry = vbible.character(vb, cid) or {}
            ref_names[cid] = f"the {_noun(entry.get('descriptor', ''))} from the " \
                             f"{ORDINALS[len(references) - 1]} image"
    location = vbible.location(vb, shot.get("location")) or {}
    location_text = location.get("descriptor", "")
    location_ref = vbible.approved_reference(vb, "location", location.get("location_id", ""), output_dir)
    if location_ref and len(references) < 4 and shot.get("shot_type") not in {"insert", "hands"}:
        references.append(location_ref)
        slots.append({"slot": len(references) - 1, "kind": "location", "id": location.get("location_id")})
        location_text = f"in the room from the {ORDINALS[len(references) - 1]} image"
    if shot.get("setting_condition"):
        location_text = f"{location_text}, {shot['setting_condition']}"
    anchors = style_refs.approved_paths()
    style_text = ""
    if anchors and len(references) < 4:
        references.append(anchors[0])
        slots.append({"slot": len(references) - 1, "kind": "style", "id": Path(anchors[0]).stem})
        style_text = f"Rendered in the drawing style of the {ORDINALS[len(references) - 1]} image."

    subjects = []
    appearance = shot.get("appearance") or {}
    for cid in characters:
        outfit = vbible.wardrobe(vb, cid, shot.get("scene_id", ""))
        name = ref_names.get(cid) or (vbible.character(vb, cid) or {}).get("descriptor", cid)
        details = [f"wearing {outfit}" if outfit else "",
                   vbible.grooming(vb, cid, shot.get("scene_id", "")), str(appearance.get(cid) or "")]
        subjects.append(", ".join([name] + [text.strip() for text in details if text.strip()]))
    article = "an" if noun[0] in "aeiou" else "a"
    crowd = _company(shot, len(characters))
    if references:
        lead = f"Create {article} {noun} of " + "; and ".join(subjects) if subjects else f"Create {article} {noun}"
        prompt = f"{lead}. {shot['action']}. {crowd} Setting: {location_text}. {framing}. {style_text} {style}"
        kind = "ref"
    else:
        people = f" Featuring {'; and '.join(subjects)}." if subjects else ""
        prompt = f"{article.capitalize()} {noun}: {shot['action']}.{people} {crowd} Setting: {location_text}. {framing}. {style}"
        kind = "t2i"
    prompt = re.sub(r"\s+", " ", re.sub(r"\.\s*\.", ".", prompt)).strip()
    return {"kind": kind, "prompt": prompt, "references": references, "slots": slots,
            "seed": int(shot.get("seed", 0)) + 7919 * int(shot.get("seed_offset", 0))}


CROWD_HINT = ("Any other people are few, distant and indistinct in shadow, each clearly different in "
              "age, build, posture and clothing.")
_GROUP_WORDS = {"guests", "men", "women", "people", "figures", "crowd", "others", "strangers",
                "colleagues", "friends", "couples", "diners", "partygoers", "onlookers"}


def _has_unnamed_people(action: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", str(action or "").lower())) & _GROUP_WORDS)


def alone_hint(count: int) -> str:
    """Positive wording for 'nobody else is here' (the negative prompt is zeroed)."""
    if count <= 0:
        return "The room is empty and still."
    if count == 1:
        return "Alone in the room."
    if count == 2:
        return "The two of them alone in the room."
    return f"Only these {count} people in the room."


def _company(shot: dict, count: int) -> str:
    """Crowd or solitude sentence; shot lists before version 2 have no others_present."""
    others = shot.get("others_present")
    if others is True or (others is None and _has_unnamed_people(shot.get("action", ""))):
        return CROWD_HINT
    return alone_hint(count) if others is False else ""


def prompt_problems(shot: dict, prompt: str) -> list[str]:
    """Composed-prompt checks before generation: injuries, decay or tears the narration lacks."""
    from agents import identity
    unsupported = identity.unsupported_terms(prompt, shot.get("narration_excerpt", ""))
    return [f"the prompt mentions {unsupported} but the narration at this moment does not"] if unsupported else []


def resolve_image(shots: list[dict], shot: dict) -> str | None:
    """Image path for a shot, following reuse_of; a held shot (generation failed in
    illustration mode) shows the previous shot's image for longer."""
    by_id = {item["shot_id"]: item for item in shots}
    current, hops = shot, 0
    while current and hops < 10:
        if current.get("reuse_of"):
            current = by_id.get(current["reuse_of"])
        elif current.get("hold_previous") and not current.get("image_path"):
            index = next((i for i, item in enumerate(shots) if item["shot_id"] == current["shot_id"]), 0)
            current = shots[index - 1] if index > 0 else None
        else:
            break
        hops += 1
    return (current or {}).get("image_path")
