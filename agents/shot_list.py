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

def shot_issues(value: dict, segments: list[dict], vb: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Blocking problems: ({shot_id: [problems]}, [whole-document problems]).

    These trigger the repair and, if it fails, fail the stage: ids, structure,
    excluded content, page layouts, injuries/decay/tears and unnamed people the
    narration does not state. Composition targets are warnings (``shot_warnings``).
    """
    from agents import identity
    shots = value.get("shots") if isinstance(value, dict) else None
    ids = [segment["shot_id"] for segment in segments]
    if not isinstance(shots, list) or [shot.get("shot_id") for shot in shots if isinstance(shot, dict)] != ids:
        return {}, [f"shot list must return exactly these shot_ids in order: {ids}"]
    characters = {item["character_id"] for item in vb.get("characters", [])}
    locations = {item["location_id"] for item in vb.get("locations", [])}
    excerpts = {segment["shot_id"]: segment.get("text", "") for segment in segments}
    by_shot: dict[str, list[str]] = {}
    general: list[str] = []
    settings = value.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        general.append("settings must map every location_id used to its condition")
    used = {shot.get("location_id") for shot in shots}
    for lid in sorted(lid for lid in used if lid in locations):
        condition = str(settings.get(lid) or "").strip()
        if not condition:
            general.append(f"settings.{lid}: describe the condition the story gives this place")
        unsupported = identity.unsupported_terms(condition, " ".join(excerpts.values()))
        if unsupported:
            general.append(f"settings.{lid}: the narration never mentions {unsupported}")
    seen = set()
    for shot in shots:
        sid = shot["shot_id"]
        problems = by_shot.setdefault(sid, [])
        cast = shot.get("characters") if isinstance(shot.get("characters", []), list) else []
        appearance = shot.get("appearance") if isinstance(shot.get("appearance"), dict) else {}
        missing = [cid for cid in cast if not str(appearance.get(cid, "")).strip()]
        if missing:
            problems.append(f"appearance needs grooming and expression for {missing}")
        if not isinstance(shot.get("others_present"), bool):
            problems.append("others_present must be true or false")
        elif not shot["others_present"]:
            strangers = unnamed_people(shot.get("action", ""), len(cast))
            if strangers:
                problems.append(f"the action shows people beyond the {len(cast)} listed character(s) "
                                f"({', '.join(strangers)}) but others_present is false")
        visible = " ".join([str(shot.get("action", ""))] + [str(text) for text in appearance.values()])
        unsupported = identity.unsupported_terms(visible, excerpts.get(sid, ""))
        if unsupported:
            problems.append(f"the narration at this moment never mentions {unsupported}; "
                            "no injuries, decay or tears it does not state")
        banned = identity.forbidden_terms(shot.get("action", ""))
        if banned:
            problems.append(f"the action mentions excluded subjects {banned}")
        layout = identity.layout_terms(shot.get("action", ""))
        if layout:
            problems.append(f"the action asks for a page layout {layout}; describe one single image")
        if shot.get("shot_type") not in SHOT_TYPES:
            problems.append(f"shot_type must be one of {SHOT_TYPES}")
        if shot.get("face_visibility") not in FACE_VISIBILITY:
            problems.append(f"face_visibility must be one of {FACE_VISIBILITY}")
        unknown = [cid for cid in cast if cid not in characters]
        if unknown or not isinstance(shot.get("characters", []), list):
            problems.append(f"unknown characters {unknown}")
        if shot.get("location_id") not in locations:
            problems.append(f"location_id must be one of {sorted(locations)}")
        if not str(shot.get("action", "")).strip():
            problems.append("action is required")
        reuse = shot.get("reuse_of") or ""
        if reuse and reuse not in seen:
            problems.append("reuse_of must name an earlier shot")
        seen.add(sid)
    return {sid: problems for sid, problems in by_shot.items() if problems}, general


def shot_warnings(shots: list[dict], policy: str) -> list[str]:
    """Composition targets: reported in shot_list.json, never a failure."""
    warnings = []
    if not shots:
        return warnings
    wide = sum(1 for shot in shots if shot.get("shot_type") in {"wide", "establishing"})
    target = float(getattr(config, "IMAGEGEN_MIN_WIDE_SHARE", 0.3))
    if wide / len(shots) < target:
        warnings.append(f"wide/establishing shots: {wide}/{len(shots)}, below the {target:.0%} target")
    full = sum(1 for shot in shots if shot.get("face_visibility") == "full")
    if policy == "limited_faces":
        limit = int(getattr(config, "IMAGEGEN_MAX_FULL_FACE_SHOTS", 6))
        if full > limit:
            warnings.append(f"full-face shots: {full}, above the limited_faces target of {limit}")
    elif (len(shots) - full) / len(shots) < 0.4:
        warnings.append(f"shots without a full face: {len(shots) - full}/{len(shots)}, below the 40% target")
    return warnings


def validate_shots(value: dict, segments: list[dict], vb: dict, outline: dict, policy: str) -> list[dict]:
    by_shot, general = shot_issues(value, segments, vb)
    if by_shot or general:
        problems = general + [f"{sid}: {problem}" for sid, items in by_shot.items() for problem in items]
        raise NarrativeValidationError("shot list problems: " + "; ".join(problems[:20]))
    settings = value.get("settings") or {}
    shots = value["shots"]
    for shot in shots:
        shot["setting_condition"] = str(settings.get(shot.get("location_id")) or "").strip()
    return shots


def _segments_and_hash(script: dict, vb: dict, output: Path) -> tuple[list[dict], str, str]:
    """(segments, source hash, identity policy): what the shot list depends on."""
    timeline = load_json(output / "scene_timeline.json")
    if not timeline:
        raise FileNotFoundError("scene_timeline.json is missing: run the narration step first")
    from agents import identity
    from video.timeline import parse_srt
    policy = str(getattr(config, "IMAGEGEN_IDENTITY_POLICY", "reference"))
    segments = plan_segments(script, timeline, parse_srt(output / "narration.srt"))
    source_hash = stable_hash({"version": SHOT_LIST_VERSION, "segments": segments,
                               "visual_bible": vb.get("source_hash"), "policy": policy,
                               "style": identity.visual_style()})
    return segments, source_hash, policy


def expected_source_hash(script: dict, vb: dict, output_dir) -> str | None:
    """Source hash a fresh shot list would have now (None when narration is missing)."""
    try:
        return _segments_and_hash(script, vb, Path(output_dir))[1]
    except (FileNotFoundError, OSError, KeyError, ValueError):
        return None


def build_shot_list(script: dict, outline: dict, vb: dict, output_dir, force: bool = False) -> dict:
    output = Path(output_dir)
    path = output / FILE_NAME
    segments, source_hash, policy = _segments_and_hash(script, vb, output)
    from agents import identity
    existing = load_json(path) or {}
    if not force and existing.get("source_hash") == source_hash:
        return existing
    scene_cast = {scene["scene_id"]: scene.get("characters_present", []) for scene in outline.get("scenes", [])}
    scene_info = {scene["scene_id"]: {"setting": scene.get("setting", ""), "summary": scene.get("summary", "")}
                  for scene in outline.get("scenes", [])}
    policy_rule = (
        "Faces may be visible; aim for 40% to 60% of shots WITHOUT a full face "
        "(hands, silhouettes, over-the-shoulder, objects, wides) — a target, not a quota."
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
avoid two consecutive shots with the same framing. Aim for about {getattr(config, "IMAGEGEN_MIN_WIDE_SHARE", 0.3):.0%}
of shots wide or establishing, with figures small inside vast, towering spaces, where the narration
allows it (a target, not a quota); close-ups keep a dark background with a single light source.
When a shot shows only the listed characters, describing them as "two figures" is fine; any person
beyond them (guests, staff, a crowd) requires others_present true. {policy_rule}
{identity.image_prompt_rules()}"""
    context = f"""VISUAL BIBLE: {compact_json({key: vb.get(key) for key in ("characters", "locations")})}
SEGMENTS: {compact_json([{"shot_id": s["shot_id"], "scene_id": s["scene_id"],
                         "seconds": round(s["end"] - s["start"], 1), "narration": s["text"],
                         "scene_cast": scene_cast.get(s["scene_id"], []),
                         "scene_setting": scene_info.get(s["scene_id"], {}).get("setting", ""),
                         "scene_summary": scene_info.get(s["scene_id"], {}).get("summary", ""),
                         "default_location": (vbible.location_for_scene(vb, s["scene_id"]) or {}).get("location_id")}
                        for s in segments])}"""
    def failing_shots(data: dict) -> dict:
        # Repair only the shots that break a blocking rule; a whole-document
        # problem (ids, settings) returns {} and gets the full repair instead.
        by_shot, general = shot_issues(data, segments, vb)
        return {} if general else by_shot

    shots = structured.generate_json(
        contract, "shot list", lambda data: validate_shots(data, segments, vb, outline, policy),
        generator=generate, context=context, stage="shot_list",
        temperature_key="outline", run_dir=str(output),
        scene_scope=structured.SceneScope(issues=failing_shots, list_key="shots", id_key="shot_id",
                                          prefer=True, item="shot"),
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
    warnings = shot_warnings(planned, policy)
    for warning in warnings:
        print(f"   ⚠ shot list: {warning} (a target, not a failure)")
    result = {
        "version": SHOT_LIST_VERSION, "source_hash": source_hash, "policy": policy,
        "budget": int(getattr(config, "IMAGEGEN_MAX_UNIQUE_SHOTS", 50)),
        "unique_shots": sum(1 for shot in planned if not shot["reuse_of"]),
        "warnings": warnings, "shots": planned,
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
# Words that always mean people beyond the named cast.
_UNNAMED_TERMS = re.compile(
    r"\b(?:guests?|crowds?|strangers?|waiters?|waitress(?:es)?|staff|onlookers?|passers-?by|passerby|"
    r"bystanders?|partygoers?|servants?|others|people in the background)\b")
# Generic words that may just be the listed characters ("Two figures stand…").
_GENERIC_PEOPLE = r"(?:figures|people|persons|men|women|silhouettes|shapes)"
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "a few": 3, "several": 3, "many": 5, "dozens of": 24}
_COUNTED = re.compile(r"\b(\d+|" + "|".join(sorted(_COUNTS, key=len, reverse=True)) + r")\s+(?:[a-z-]+\s+){0,2}?"
                      + _GENERIC_PEOPLE + r"\b")
_OF_THEM = re.compile(r"\b(?:the\s+)?(\w+)\s+of\s+them\b")


def unnamed_people(action: str, cast_size: int) -> list[str]:
    """Phrases in ``action`` that put more people in the frame than the ``cast_size`` named characters.

    "Two figures stand…" with two listed characters is the cast itself; "two
    guests", "a crowd" or "three figures" with two characters are not.
    """
    text = str(action or "").lower()
    found = [match.group(0) for match in _UNNAMED_TERMS.finditer(text)]
    counted = set()
    for match in _COUNTED.finditer(text):
        word = match.group(1)
        count = int(word) if word.isdigit() else _COUNTS[word]
        counted.add(match.start())
        if count > cast_size:
            found.append(match.group(0))
    for match in _OF_THEM.finditer(text):
        word = match.group(1)
        count = int(word) if word.isdigit() else _COUNTS.get(word, 0)
        if count > cast_size:
            found.append(match.group(0))
    if cast_size < 2:          # an uncounted plural is already more people than one character
        for match in re.finditer(r"\b" + _GENERIC_PEOPLE + r"\b", text):
            if not any(start <= match.start() < start + 40 for start in counted):
                found.append(match.group(0))
    return list(dict.fromkeys(found))


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
    if others is True or (others is None and unnamed_people(shot.get("action", ""), count)):
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
