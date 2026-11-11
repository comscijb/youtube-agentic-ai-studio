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
from agents.narrative import NarrativeValidationError, atomic_write_json, extract_json, load_json, stable_hash
from agents.structured import compact_json
from studio.filenames import safe_filename

SHOT_LIST_VERSION = 2   # the shot-prompt version: bumping it re-plans and regenerates shots
FILE_NAME = "shot_list.json"
SHOT_TYPES = ("wide", "establishing", "medium", "close_up", "insert", "over_the_shoulder",
              "silhouette", "hands")
FACE_VISIBILITY = ("none", "partial", "full")
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
ORDINALS = ("first", "second", "third", "fourth")
MAX_CHARACTER_REFS = 3     # of the 4 reference slots; the 4th is the location or a style anchor
FRAMING = {
    "wide": "wide shot, the figures small within towering architecture",
    "establishing": "establishing wide shot of the vast space, any figure tiny",
    "medium": "medium shot, waist up",
    "close_up": "close-up on the face and shoulders against a dark background lit by one light source",
    "insert": "insert close-up of the object",
    "over_the_shoulder": "over-the-shoulder shot",
    "silhouette": "backlit silhouette: the figure's shape dark against the bright background, face and clothes in shadow",
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


BEAT_SENTENCE = 2.6          # a sentence this short is a beat: a natural place for a quick cut
BEAT_PAUSE = 0.45            # a pause this long after a sentence is a beat too


def pacing_window() -> tuple[float, float]:
    """(min, max) seconds per shot: PACING_MIN/MAX_SECONDS with pacing variety (Phase 3.9-B.4), else the
    fixed IMAGEGEN_SHOT_MIN/MAX_SECONDS window."""
    if getattr(config, "PACING_VARIETY", True):
        return (float(getattr(config, "PACING_MIN_SECONDS", 3.5)), float(getattr(config, "PACING_MAX_SECONDS", 16.0)))
    return (float(getattr(config, "IMAGEGEN_SHOT_MIN_SECONDS", 6.0)), float(getattr(config, "IMAGEGEN_SHOT_MAX_SECONDS", 9.0)))


def varied_segments(scene: dict, sentences: list[dict], min_s: float, max_s: float) -> list[dict]:
    """Cut on the narration's rhythm (Phase 3.9-B.4): a short sentence or a pause is a beat and closes a
    quick shot; long passages are held. Deterministic, sentence boundaries only, segments tile the scene.

    Each shot gets a target length from a fixed long/short pattern (seeded by the scene), so durations
    vary instead of piling up just under the maximum; a beat after the minimum closes the shot early."""
    import random
    inside = [s for s in sentences if scene["start"] - 0.05 <= s["start"] < scene["end"]]
    if not inside:
        return [{"start": scene["start"], "end": scene["end"], "text": ""}]
    rng = random.Random(int(round(scene["start"] * 100)) + 17)

    def target() -> float:      # mostly holds, some quick beats (tuned on episode 2: CV 0.23 → 0.37)
        return min_s + (max_s - min_s) * (rng.random() ** 0.35 if rng.random() < 0.65 else rng.random() * 0.1)
    groups, current, goal = [], [], target()
    for position, sentence in enumerate(inside):
        if current and sentence["end"] - current[0]["start"] > max_s and \
                current[-1]["end"] - current[0]["start"] >= min_s:
            groups.append(current)
            current, goal = [], target()
        current.append(sentence)
        length = current[-1]["end"] - current[0]["start"]
        following = inside[position + 1] if position + 1 < len(inside) else None
        pause = (following["start"] - sentence["end"]) if following else 0.0
        beat = sentence["end"] - sentence["start"] <= BEAT_SENTENCE or pause >= BEAT_PAUSE
        if length >= max_s or length >= goal or (beat and length >= min_s and length >= goal * 0.5):
            groups.append(current)
            current, goal = [], target()
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


def pacing_cv(segments: list[dict]) -> float:
    """Coefficient of variation of the shot durations (target ≥ 0.35: not metronomic)."""
    import statistics
    durations = [float(item["end"]) - float(item["start"]) for item in segments]
    if len(durations) < 2 or not statistics.mean(durations):
        return 0.0
    return round(statistics.pstdev(durations) / statistics.mean(durations), 3)


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
    min_s, max_s = pacing_window()
    cut = varied_segments if getattr(config, "PACING_VARIETY", True) else segment_scene
    sentences = sentences_from_cues(cues)
    segments = []
    for scene in timeline.get("scenes", []):
        for segment in cut(scene, sentences, min_s, max_s):
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

# Fields the code owns: Gemini never returns them (they come from the narration timeline).
SKELETON_FIELDS = ("shot_id", "scene_id", "section_id", "start", "end", "seconds", "n",
                   "narration", "narration_excerpt")


def _reuse_id(reuse, index: int, segments: list[dict]):
    """Map Gemini's reuse_of (the 1-based number of an earlier shot) to that shot's id."""
    if reuse in (None, "", 0, "0", False):
        return ""
    earlier = [segment["shot_id"] for segment in segments[:index]]
    if isinstance(reuse, str) and reuse in earlier:
        return reuse
    try:
        number = int(str(reuse).lstrip("#"))
    except ValueError:
        return str(reuse)
    return earlier[number - 1] if 1 <= number <= len(earlier) else f"#{number}"


def attach_skeleton(value: dict, segments: list[dict]) -> dict:
    """Zip Gemini's ordered shot contents onto the code-built skeleton (ids, timings).

    Raises NarrativeValidationError when the number of shots does not match.
    """
    shots = value.get("shots") if isinstance(value, dict) else None
    if not isinstance(shots, list) or len(shots) != len(segments) \
            or not all(isinstance(shot, dict) for shot in shots):
        got = len(shots) if isinstance(shots, list) else "no list"
        raise NarrativeValidationError(f"return exactly {len(segments)} shot objects, one per segment "
                                       f"in order (got {got})")
    attached = []
    for index, (segment, shot) in enumerate(zip(segments, shots)):
        content = {key: item for key, item in shot.items() if key not in SKELETON_FIELDS}
        content["shot_id"] = segment["shot_id"]
        content["reuse_of"] = _reuse_id(shot.get("reuse_of"), index, segments)
        attached.append(content)
    return {**value, "shots": attached}


def shot_issues(value: dict, segments: list[dict], vb: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Blocking problems: ({shot_id: [problems]}, [whole-scene problems]).

    Only what makes a shot unusable blocks (repair, then flag): excluded
    subjects, page-layout phrases, empty or invalid fields and a wrong shot
    count. Content doubts (unstated injuries/decay/tears, unnamed people) are
    advisory (``shot_advice``) and composition targets are ``shot_warnings``.
    """
    from agents import identity
    try:
        value = attach_skeleton(value, segments)
    except NarrativeValidationError as exc:
        return {}, [str(exc)]
    characters = {item["character_id"] for item in vbible.on_screen(vb)}
    absent = {item["character_id"]: item for item in vbible.absent(vb)}
    locations = {item["location_id"] for item in vb.get("locations", [])}
    by_shot: dict[str, list[str]] = {}
    seen = set()
    for shot in value["shots"]:
        sid = shot["shot_id"]
        problems = by_shot.setdefault(sid, [])
        cast = shot.get("characters") if isinstance(shot.get("characters", []), list) else []
        appearance = shot.get("appearance") if isinstance(shot.get("appearance"), dict) else {}
        missing = [cid for cid in cast if not str(appearance.get(cid, "")).strip()]
        if missing:
            problems.append(f"appearance needs grooming and expression for {missing}")
        if not isinstance(shot.get("others_present"), bool):
            problems.append("others_present must be true or false")
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
        for cid in [cid for cid in cast if cid in absent]:
            problems.append(f"{cid} is {absent[cid]['presence']} and never in a frame; remove it from "
                            f"characters and show {absent[cid].get('visual_proxy') or 'the listener'} instead")
        named = absent_named(shot.get("action", ""), absent)
        if named:
            problems.append(f"the action describes {named}, who never appear on screen; show their "
                            "visual_proxy (or who is listening) without naming or describing them")
        unknown = [cid for cid in cast if cid not in characters and cid not in absent]
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
    return {sid: problems for sid, problems in by_shot.items() if problems}, []


def absent_named(action: str, absent: dict) -> list[str]:
    """Absent characters (voice_only / none) the shot text names: they would be drawn as people."""
    text = str(action or "")
    named = []
    for cid, item in absent.items():
        words = {cid.split("_")[0]} | {part for part in str(item.get("name", "")).split()[:1]}
        if any(re.search(rf"\b{re.escape(word)}\b", text, re.I) for word in words if len(word) > 1):
            named.append(cid)
    return named


def advice_for(shot: dict, scene_narration: str) -> list[str]:
    """Advisory notes for one shot (never blocking): unstated injuries/decay/tears
    compared with the whole scene's narration, and people beyond the listed cast."""
    from agents import identity
    notes = []
    cast = [cid for cid in (shot.get("characters") or []) if isinstance(cid, str)]
    appearance = shot.get("appearance") if isinstance(shot.get("appearance"), dict) else {}
    visible = " ".join([str(shot.get("action", ""))] + [str(text) for text in appearance.values()])
    unsupported = identity.unsupported_terms(visible, scene_narration)
    if unsupported:
        notes.append(f"the scene's narration never mentions {', '.join(unsupported)}")
    if shot.get("others_present") is False:
        strangers = unnamed_people(shot.get("action", ""), len(cast))
        if strangers:
            notes.append(f"people beyond the {len(cast)} listed character(s) ({', '.join(strangers)}) "
                         "while others_present is false; the prompt keeps them alone")
    return notes


def shot_advice(value: dict, segments: list[dict]) -> tuple[dict[str, list[str]], list[str]]:
    """Advisory notes: ({shot_id: [notes]}, [setting notes]) — shown, never blocking."""
    from agents import identity
    shots = attach_skeleton(value, segments)["shots"]
    scene_of = {segment["shot_id"]: segment.get("scene_id") or segment["shot_id"].rsplit("_", 1)[0]
                for segment in segments}
    narration = {}
    for segment in segments:
        scene = scene_of[segment["shot_id"]]
        narration[scene] = f"{narration.get(scene, '')} {segment.get('text', '')}"
    notes = {shot["shot_id"]: advice_for(shot, narration[scene_of[shot["shot_id"]]]) for shot in shots}
    general = []
    settings = value.get("settings") if isinstance(value.get("settings"), dict) else {}
    everything = " ".join(narration.values())
    for lid in sorted({shot.get("location_id") for shot in shots if shot.get("location_id")}):
        condition = str(settings.get(lid) or "").strip()
        if not condition:
            general.append(f"settings.{lid}: no condition given for this place")
        elif identity.unsupported_terms(condition, everything):
            general.append(f"settings.{lid}: the narration never mentions "
                           f"{', '.join(identity.unsupported_terms(condition, everything))}")
    return {sid: items for sid, items in notes.items() if items}, general


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
    shots = attach_skeleton(value, segments)["shots"]
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


SCENE_REPAIRS = 2          # per scene; a scene that still fails keeps its best attempt, flagged
CHECKPOINT_DIR = Path(".checkpoints") / "shot_list"


def _scene_contract(policy: str) -> str:
    from agents import identity
    policy_rule = (
        "Faces may be visible; aim for 40% to 60% of shots WITHOUT a full face "
        "(hands, silhouettes, over-the-shoulder, objects, wides) — a target, not a quota."
        if policy != "limited_faces" else
        f"Identity retention is weak: use face_visibility none or partial, with at most "
        f"{getattr(config, 'IMAGEGEN_MAX_FULL_FACE_SHOTS', 6)} full-face hero shots in the episode."
    )
    return f"""You are the director of photography of Private Gravity, a dark cinematic anthology.
You plan ONE SCENE at a time. For every narration SEGMENT of the scene supplied after this
contract, design one still image that shows what is being said at that moment.
Return ONLY JSON {{"settings": {{...}}, "shots": [...]}}.
settings maps every location_id you use to one sentence on its condition as the story gives it
(an expensive minimal loft: "immaculate and expensive: polished surfaces, precise objects,
pristine walls"); never decay, grime, stains, rust or ruin unless the narration states them.
shots has exactly one object per segment, in segment order (the code assigns shot ids and
timings by position; never return ids or times), keys: shot_type
({", ".join(SHOT_TYPES)}), characters (character_ids visible in the frame, from the scene cast),
location_id (from the visual bible, chosen from THIS segment's own narration: when the narration moves — "in the
car on the drive home", "back in the kitchen" — the shot moves with it, whatever the scene's first location), action (what is visible: pose, gesture, objects, light and
composition in one concrete sentence; no camera jargon beyond the framing; never describe faces
or clothes, the visual bible supplies them), appearance (an object mapping every character in
the shot to their grooming and expression at this exact moment, following the narration: a
composed character is "poised, hair neatly pinned, composed expression"; tears, dishevelment,
dirt or marks only where the narration has them), others_present (true only when the narration
or the scene's chronology places unnamed people — guests, staff, a crowd — in the room at this
moment; before the guests arrive it is false and the named characters are alone),
face_visibility (none | partial | full) and reuse_of (0, or the segment number n of an EARLIER
shot of this scene when showing the same image again is dramatically right).
Mix wides, mediums, close-ups, inserts of objects, hands, silhouettes and over-the-shoulder shots;
avoid two consecutive shots with the same framing, also across the scene boundary (the previous
scene's last shot is given). Aim for about {getattr(config, "IMAGEGEN_MIN_WIDE_SHARE", 0.3):.0%}
of shots wide or establishing, with figures small inside vast, towering spaces, where the narration
allows it (a target, not a quota); close-ups keep a dark background with a single light source.
A shot that lists characters and has others_present false refers to them by name or pronoun, never with a
generic person word ("a lone figure", "a man", "someone"): that word draws a second person. Generic person
words are only for others_present true, and any person beyond the listed characters (guests, staff, a
crowd) requires it. An over_the_shoulder shot lists
first the character whose shoulder we see and then the character facing the camera; both are listed
characters, never a third person. Characters whose presence (VISUAL BIBLE) is "none" or
"voice_only" are never in characters, never named and never described as a person in action: a
"none" character (an AI with no body) appears only as its visual_proxy (e.g. the gold glow of the
phone screen); while a "voice_only" character speaks (a voice on the phone), the shot shows who is
listening, with the phone. {policy_rule}{_ambient_rule()}
{identity.image_prompt_rules()}"""


def _ambient_rule() -> str:
    """Phase 3.9-B.2: one optional animated element per shot, only where the narration has its source."""
    from video import ambient
    if not ambient.enabled():
        return ""
    return ("\nambient (optional): \"\" or one of " + ", ".join(ambient.KINDS) + ": a subtle animated "
            "element for this shot, only when the scene's narration names what produces it (a candle or "
            "lamp; dust in a beam of light; rain; fog or mist; steam from a kettle, tea or a mug). At most "
            "one per shot; most shots have none.")


def _cold_open_note(scene: dict) -> str:
    """Phase 3.9.7: the teaser's shots read as a jump in time and never show what it withholds."""
    if scene.get("section_type") != "cold_open":
        return ""
    if scene.get("cold_open_mode") == "omen":
        return ("\nCOLD OPEN (omen): one or two quiet establishing images of the place, no characters in close-up; a slower, "
                "darker look is added at render.")
    return ("\nCOLD OPEN (flash-forward to scene " + str(scene.get("source_scene_id", "")) + "): 2-3 shots of that later moment, "
            "seen from another angle or closer than the scene itself will show it (a slower, darker look is added at render). "
            "NEVER SHOW: " + str(scene.get("withheld") or "the payoff") + " (keep it out of frame, a soft glow, or seen only "
            "in a character's face).")


def _scene_context(scene_id: str, scene_segments: list[dict], vb: dict, outline: dict) -> str:
    scene = next((item for item in outline.get("scenes", []) if item.get("scene_id") == scene_id), {})
    return f"""VISUAL BIBLE: {compact_json({key: vb.get(key) for key in ("characters", "locations")})}
SCENE_ID: {scene_id}
SCENE: {compact_json({"setting": scene.get("setting", ""), "summary": scene.get("summary", ""),
                      "cast": scene.get("characters_present", []),
                      "default_location": (vbible.location_for_scene(vb, scene_id) or {}).get("location_id")})}
SCENE NARRATION: {" ".join(segment["text"] for segment in scene_segments)}{_cold_open_note(scene)}
SEGMENTS ({len(scene_segments)}; return exactly {len(scene_segments)} shots): {compact_json(
        [{"n": number, "seconds": round(segment["end"] - segment["start"], 1), "narration": segment["text"]}
         for number, segment in enumerate(scene_segments, 1)])}"""


def _continuity(previous: dict | None) -> str:
    if not previous:
        return "PREVIOUS SCENE'S LAST SHOT: none (this is the first scene)"
    return "PREVIOUS SCENE'S LAST SHOT (read-only continuity): " + compact_json(
        {key: previous.get(key) for key in ("shot_type", "characters", "location_id", "action")})


def _problems(value, scene_segments: list[dict], vb: dict) -> tuple[dict[int, list[str]], list[str]]:
    """Blocking problems of one scene: ({position: [problems]}, [whole-scene problems])."""
    by_shot, general = shot_issues(value, scene_segments, vb)
    index = {segment["shot_id"]: position for position, segment in enumerate(scene_segments)}
    return {index[sid]: problems for sid, problems in by_shot.items()}, general


def _shot_repair_prompt(contract: str, context: str, shots: list, issues: dict[int, list[str]]) -> str:
    targets = sorted(issues)
    neighbours = sorted({near for index in targets for near in (index - 1, index + 1)
                         if 0 <= near < len(shots) and near not in targets})
    diagnostics = "\n".join(f"- shot n={index + 1}: {'; '.join(issues[index])}" for index in targets)
    return structured.compose_prompt(contract, context) + f"""

REPAIR: some shots of this scene broke a rule. Return exactly {{"shots": [...]}} with one complete
corrected object per shot listed below, in the same order ({len(targets)} objects). Do not return
the neighbour shots; they are read-only context.
PROBLEMS:
{diagnostics}
SHOTS TO CORRECT: {compact_json([{"n": index + 1, **shots[index]} for index in targets])}
NEIGHBOUR SHOTS (read-only): {compact_json([{"n": index + 1, **shots[index]} for index in neighbours])}"""


def _scene_repair_prompt(contract: str, context: str, raw_value, general: list[str]) -> str:
    return structured.compose_prompt(contract, context) + f"""

REPAIR: the previous answer for this scene was not usable: {'; '.join(general)}.
Return the COMPLETE corrected JSON for the scene, {{"settings": {{...}}, "shots": [...]}}.
PREVIOUS ANSWER: {compact_json(raw_value) if raw_value is not None else "(unparseable)"}"""


def _fit_count(value, scene_segments: list[dict]) -> dict:
    """Best effort when the count never matched: trim extras, pad with empty (flagged) shots."""
    shots = [shot for shot in (value or {}).get("shots", []) if isinstance(shot, dict)][:len(scene_segments)]
    shots += [{"action": "", "characters": [], "others_present": False}
              for _ in range(len(scene_segments) - len(shots))]
    settings = (value or {}).get("settings")
    return {"settings": settings if isinstance(settings, dict) else {}, "shots": shots}


def plan_scene(scene_id: str, scene_segments: list[dict], vb: dict, outline: dict, policy: str,
               previous: dict | None, *, run_dir, generator=None) -> dict:
    """Plan one scene: one call, up to SCENE_REPAIRS repairs, never raises on content.

    Returns {"settings", "shots" (attached to the skeleton), "flags" {shot_id: [problems]},
    "calls"}; flags are blocking problems left after the repairs.
    """
    generator = generator or generate
    contract = _scene_contract(policy)
    context = _scene_context(scene_id, scene_segments, vb, outline) + "\n" + _continuity(previous)
    job = structured._Job(generator, f"shot list {scene_id}", f"shot_list-{scene_id}", "outline", run_dir)
    best, best_score, value, calls, pending_targets = None, None, None, 0, []
    prompt = structured.compose_prompt(contract, context)
    for attempt in range(SCENE_REPAIRS + 1):
        calls += 1
        stage = job.stage if attempt == 0 else f"{job.stage}-repair"
        raw = job.call(prompt, stage, "outline" if attempt == 0 else "repair")
        try:
            returned = extract_json(raw, f"shot list {scene_id}")
        except NarrativeValidationError as exc:
            job.dump("parse-failure", exc, raw, stage=stage)
            prompt = _scene_repair_prompt(contract, context, None, [str(exc)])
            continue
        shots_back = returned.get("shots") if isinstance(returned, dict) else None
        if value is not None and pending_targets and isinstance(shots_back, list) \
                and len(shots_back) == len(pending_targets) != len(scene_segments):
            merged = list(value["shots"])            # a shot-scoped repair: merge by position
            for index, shot in zip(pending_targets, shots_back):
                if isinstance(shot, dict):
                    merged[index] = {key: item for key, item in shot.items() if key != "n"}
            settings_back = returned.get("settings")
            returned = {**value, "shots": merged,
                        **({"settings": settings_back} if isinstance(settings_back, dict) else {})}
        value = returned
        issues, general = _problems(value, scene_segments, vb)
        score = (1 if general else 0, sum(len(items) for items in issues.values()))
        if best_score is None or score < best_score:
            best, best_score = value, score
        if not issues and not general:
            break
        job.dump("validation-failure", "; ".join(general + [f"n={i + 1}: {'; '.join(p)}"
                                                            for i, p in sorted(issues.items())]), raw, stage=stage)
        pending_targets = sorted(issues) if not general else []
        prompt = (_shot_repair_prompt(contract, context, value["shots"], issues) if pending_targets
                  else _scene_repair_prompt(contract, context, value, general))
    best = best if best is not None else {"settings": {}, "shots": []}
    issues, general = _problems(best, scene_segments, vb)
    if general:
        best = _fit_count(best, scene_segments)
        issues, general = _problems(best, scene_segments, vb)
    attached = attach_skeleton(best, scene_segments)
    flags = {scene_segments[index]["shot_id"]: problems for index, problems in issues.items()}
    settings = best.get("settings") if isinstance(best.get("settings"), dict) else {}
    narration = " ".join(segment["text"] for segment in scene_segments)
    advice = {shot["shot_id"]: notes for shot in attached["shots"] if (notes := advice_for(shot, narration))}
    return {"settings": settings, "shots": attached["shots"], "flags": flags, "advice": advice,
            "calls": calls, "general": general}


class PlanningStopped(InterruptedError):
    """Stop was requested while scenes were still being planned (completed checkpoints are kept)."""


def planner_concurrency(value=None) -> int:
    """SHOT_PLANNER_CONCURRENCY: 1 (default, strict continuity) or 2; never more."""
    try:
        wanted = int(value if value is not None else getattr(config, "SHOT_PLANNER_CONCURRENCY", 1))
    except (TypeError, ValueError):
        wanted = 1
    return max(1, min(2, wanted))


def _failed_scene(scene_segments: list[dict], exc: BaseException) -> dict:
    """A scene whose planning raised (e.g. Gemini unavailable after its retries): every shot flagged."""
    message = f"planning failed: {exc}"[:300]
    shots = attach_skeleton(_fit_count({"settings": {}, "shots": []}, scene_segments), scene_segments)["shots"]
    return {"settings": {}, "shots": shots, "advice": {}, "calls": 0, "general": [message],
            "flags": {segment["shot_id"]: [message] for segment in scene_segments}}


def build_shot_list(script: dict, outline: dict, vb: dict, output_dir, force: bool = False, *,
                    on_scene=None, concurrency: int | None = None, stop=None, lock=None) -> dict:
    """Plan the shot list scene by scene (one Gemini call each, checkpointed).

    The skeleton (ids, timings, excerpts) comes from the narration timeline; each
    scene is validated and repaired on its own; a scene that still breaks a
    blocking rule keeps its best attempt with those shots flagged, never
    aborting the others. A rerun reuses every completed scene's checkpoint
    (``.checkpoints/shot_list/<scene>.json``) and redoes missing or flagged ones.

    Phase 3.8: ``on_scene(index, scene_id, entries)`` receives each scene's composed shots as soon as
    it is planned (the renderer starts on them while later scenes are still with Gemini). Scenes are
    planned in order; with ``concurrency`` 2 the next scene starts before the current one ends and
    gets the latest finished scene's last shot as continuity. A scene whose planning raises is flagged
    (``failed_scenes``) and the others go on. ``stop`` (an Event) ends planning after the current scene
    (PlanningStopped). ``lock`` guards the composed shots, which the renderer updates.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import nullcontext
    output = Path(output_dir)
    path = output / FILE_NAME
    segments, source_hash, policy = _segments_and_hash(script, vb, output)
    existing = load_json(path) or {}
    if not force and existing.get("source_hash") == source_hash:
        return existing
    scene_ids = list(dict.fromkeys(segment["scene_id"] for segment in segments))
    contract = _scene_contract(policy)
    guard = lock or nullcontext()
    state = threading.Lock()
    results, by_scene, settings, flags, advice = {}, {}, {}, {}, {}
    totals = {"calls": 0, "reused": [], "failed": {}}

    def plan(index: int) -> None:
        if stop is not None and stop.is_set():
            return
        scene_id = scene_ids[index]
        scene_segments = [segment for segment in segments if segment["scene_id"] == scene_id]
        key = stable_hash({"version": SHOT_LIST_VERSION, "contract": contract,
                           "context": _scene_context(scene_id, scene_segments, vb, outline)})
        checkpoint = output / CHECKPOINT_DIR / f"{safe_filename(scene_id)}.json"
        saved = load_json(checkpoint) or {}
        if saved.get("key") == key and not saved.get("flags"):
            result = saved
            with state:
                totals["reused"].append(scene_id)
        else:
            with state:
                earlier = [i for i in results if i < index and results[i]["shots"]]
                previous = results[max(earlier)]["shots"][-1] if earlier else None
            print(f"   -> Shot list {scene_id}: {len(scene_segments)} shots")
            try:
                result = plan_scene(scene_id, scene_segments, vb, outline, policy, previous, run_dir=str(output))
            except Exception as exc:                     # noqa: BLE001 — flag this scene, plan the others
                result = _failed_scene(scene_segments, exc)
                with state:
                    totals["failed"][scene_id] = str(exc)[:300]
                print(f"   ⛔ {scene_id}: planning failed ({exc}); its shots are flagged, the other scenes go on")
            else:
                atomic_write_json(checkpoint, {"key": key, **result})
                if result["flags"]:
                    print(f"   ⚠ {scene_id}: {len(result['flags'])} shot(s) still break a rule after "
                          f"{SCENE_REPAIRS} repairs; they are flagged and skipped until edited")
            with state:
                totals["calls"] += result["calls"]
        from video import ambient
        scene_text = " ".join(segment["text"] for segment in scene_segments)
        for shot in result["shots"]:                     # whitelist + the scene's own words (3.9-B.2)
            shot["ambient"] = ambient.validate(shot.get("ambient"), scene_text) if ambient.enabled() else ""
        with state, guard:
            results[index] = result
            for lid, condition in (result.get("settings") or {}).items():
                settings.setdefault(lid, str(condition or "").strip())
            flags.update(result.get("flags") or {})
            advice.update(result.get("advice") or {})
            entries = [_entry(segment, shot, settings, vb, output, flags.get(segment["shot_id"]),
                              advice.get(segment["shot_id"]))
                       for segment, shot in zip(scene_segments, result["shots"])]
            relocate_entries(entries, vb, output)
            by_scene[index] = entries
        if on_scene is not None:
            on_scene(index, scene_id, entries)

    workers = planner_concurrency(concurrency)
    if workers == 1:
        for index in range(len(scene_ids)):
            plan(index)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="shot-planner") as pool:
            list(pool.map(plan, range(len(scene_ids))))
    if len(by_scene) < len(scene_ids):
        raise PlanningStopped(f"planning stopped after {len(by_scene)}/{len(scene_ids)} scenes; "
                              "the completed scenes are checkpointed")
    planned = [entry for index in sorted(by_scene) for entry in by_scene[index]]
    if totals["reused"]:
        print(f"   -> Reused checkpoints for {', '.join(sorted(totals['reused']))}")
    warnings = shot_warnings(planned, policy) + _setting_notes(settings, planned, segments)
    for warning in warnings:
        print(f"   ⚠ shot list: {warning} (a target, not a failure)")
    if advice:
        print(f"   ⚠ {len(advice)} shot(s) have advisory notes (shown on the shot cards; they are "
              "generated anyway)")
    result = {
        "version": SHOT_LIST_VERSION, "source_hash": source_hash, "policy": policy,
        "budget": int(getattr(config, "IMAGEGEN_MAX_UNIQUE_SHOTS", 50)),
        "unique_shots": sum(1 for shot in planned if not shot["reuse_of"]),
        "warnings": warnings, "flagged": sorted(flags), "gemini_calls": totals["calls"], "shots": planned,
        **({"failed_scenes": totals["failed"]} if totals["failed"] else {}),
    }
    with guard:
        atomic_write_json(path, result)
    write_report(output, result)
    return result


def relocate_entries(entries: list[dict], vb: dict, output) -> list[dict]:
    """Phase 3.9.9-A: every shot's location from its own narration excerpt; moved shots are composed again and a new
    beat-level location (a car interior) is saved to the visual bible."""
    from agents import adherence
    before = len(vb.get("locations", []))
    changes = adherence.relocate(entries, vb)
    moved = {change["shot_id"] for change in changes}
    for entry in entries:
        if entry["shot_id"] in moved:
            composed = compose_shot(entry, vb, output, assume_references=True)
            entry["prompt"], entry["reference_slots"] = composed["prompt"], composed["slots"]
    if len(vb.get("locations", [])) != before or changes:
        atomic_write_json(Path(output) / vbible.FILE_NAME, vb)
    return changes


REPORT_FILE = "shot_list_report.json"


def write_report(output, shot_list: dict) -> dict:
    """shot_list_report.json: list-level warnings, flagged (blocking) and advisory notes per shot."""
    shots = shot_list.get("shots", [])
    report = {
        "version": shot_list.get("version"), "warnings": shot_list.get("warnings", []),
        "flagged": {shot["shot_id"]: shot["flags"] for shot in shots if shot.get("flags")},
        "advisory": {shot["shot_id"]: shot["warnings"] for shot in shots if shot.get("warnings")},
    }
    atomic_write_json(Path(output) / REPORT_FILE, report)
    return report


def _setting_notes(settings: dict, planned: list[dict], segments: list[dict]) -> list[str]:
    from agents import identity
    narration = " ".join(segment["text"] for segment in segments)
    notes = []
    for lid in sorted({shot["location"] for shot in planned if shot.get("location")}):
        condition = settings.get(lid, "")
        unsupported = identity.unsupported_terms(condition, narration) if condition else []
        if not condition:
            notes.append(f"settings.{lid}: no condition given for this place")
        elif unsupported:
            notes.append(f"settings.{lid}: the narration never mentions {', '.join(unsupported)}")
    return notes


_LONE_WORDS = {"lone", "solitary"}
_PERSON_WORD = re.compile(
    r"\b(?P<article>[Aa]n?)\s+(?P<adjectives>(?:(?:[a-z]+-[a-z]+|lone|solitary|seated|standing|small|tall|single|distant|"
    r"hunched|motionless|stark|dark|young|old|thin|slight)[,]?\s+){0,3})"
    r"(?P<noun>figure|man|woman|person|silhouette)(?P<possessive>'s)?\b(?!\s+of\b)")
_SOMEONE = re.compile(r"\b[Ss]omeone\b")


def _reference_to(cid: str, vb: dict, cast: list[str]) -> str:
    """"the man", or the first name when two of the cast share that noun."""
    entry = vbible.character(vb, cid) or {}
    noun = _noun(entry.get("descriptor", "")) or "person"
    shared = sum(1 for other in cast if _noun((vbible.character(vb, other) or {}).get("descriptor", "")) == noun) > 1
    return str(entry.get("name") or cid).replace("_", " ").split()[0].title() if shared else f"the {noun}"


def rewrite_action(action: str, characters: list[str], vb: dict, others_present: bool = False) -> str:
    """Phase 3.9.4-A: a listed character written as "a lone figure" is drawn twice, once from the reference
    and once as the figure. The generic word becomes the character's short reference ("the man, alone, ...");
    a "man" or "woman" nobody listed is left alone. Generic words stay when others_present is true."""
    if not characters or others_present:
        return action
    nouns = {cid: _noun((vbible.character(vb, cid) or {}).get("descriptor", "")) for cid in characters}

    def pick(word: str, used: list[str]) -> str | None:
        wanted = [cid for cid in characters if nouns[cid] == word] if word in ("man", "woman") else list(characters)
        fresh = [cid for cid in wanted if cid not in used]
        chosen = (fresh or wanted or [None])[0]
        if chosen:
            used.append(chosen)
        return chosen

    used: list[str] = []

    def replace(match: re.Match) -> str:
        cid = pick(match.group("noun"), used)
        if cid is None:
            return match.group(0)
        words = [word.strip(",") for word in match.group("adjectives").split()]
        lone = [word for word in words if word in _LONE_WORDS]
        kept = [word for word in words if word not in _LONE_WORDS and word != "single"]
        reference = _reference_to(cid, vb, characters)
        if kept and reference.startswith("the "):
            reference = "the " + " ".join(kept) + reference[3:]
        text = reference + (match.group("possessive") or "")
        if lone and not match.group("possessive"):
            text += ", alone,"
        return text[0].upper() + text[1:] if match.group("article")[0] == "A" else text
    rewritten = _PERSON_WORD.sub(replace, action)

    def someone(match: re.Match) -> str:
        cid = pick("person", used)
        text = _reference_to(cid, vb, characters)
        return text[0].upper() + text[1:] if match.group(0)[0] == "S" else text
    return _SOMEONE.sub(someone, rewritten)


def is_faceless(shot: dict) -> bool:
    """Faceless, distant or silhouette: the portrait reference would pull the character into a close-up."""
    return shot.get("face_visibility") == "none" or shot.get("shot_type") in ("wide", "silhouette", "over_the_shoulder")


def reference_kinds(shot: dict) -> list[str]:
    """Phase 3.9.8-A: which sheets a character sends, primary first. A face sheet for close-ups and mediums with a face;
    for faceless and distant shots ``config.FACELESS_PORTRAIT_REFERENCE`` by shot type ("body" the full-body sheet,
    "keep"/"face" the face sheet, "both", "none"); a medium with a partial face sends both when slots allow."""
    if is_faceless(shot):
        choices = getattr(config, "FACELESS_PORTRAIT_REFERENCE", {}) or {}
        choice = choices.get(shot.get("shot_type"), choices.get("faceless", "keep"))
        return {"none": [], "body": ["body"], "both": ["body", "character"]}.get(choice, ["character"])
    if shot.get("face_visibility") == "partial" and shot.get("shot_type") == "medium":
        return ["character", "body"]
    return ["character"]


def portrait_reference_wanted(shot: dict) -> bool:
    """Whether a shot sends any character sheet (``reference_kinds``)."""
    return bool(reference_kinds(shot))


_FACELESS_VIEW = {"silhouette": "backlit, a dark shape against the bright background, face and clothes in shadow",
                  "over_the_shoulder": "seen from behind, over the shoulder",
                  "wide": "seen small and from behind, far from the viewer"}
NEUTRAL_DISTANCE = "seen at full length, at a distance"
# Phase 3.9.8-D: actions that show the face (or need it toward the camera). "Seen from behind" against them made the
# model ignore the framing and fall back to its defaults.
FACE_ACTIONS = re.compile(r"\b(?:turn(?:s|ing)? (?:her|his|their) head|look(?:s|ing)? (?:at|toward|towards|up|back|across)|"
                          r"gaz(?:e|es|ing)|glanc(?:e|es|ing)|smil(?:e|es|ing)|frown(?:s|ing)?|laugh(?:s|ing)?|cr(?:y|ies|ying)|"
                          r"plac(?:e|es|ing)|faces? (?:the|us|forward)|watch(?:es|ing)?|stares?|staring|meets? (?:her|his) eyes)\b", re.I)


def framing_conflict(shot: dict) -> str | None:
    """The phrase of the action that contradicts a faceless framing, or None."""
    found = FACE_ACTIONS.search(str(shot.get("action", "")))
    return found.group(0) if found and shot.get("shot_type") != "silhouette" else None


def faceless_headcount(names: list[str], shot: dict) -> str:
    """"The only person in the image is the man from the first image, seen small and from behind, far from the viewer."
    The from-behind wording only when the face is really hidden (face_visibility none) and the action agrees; otherwise
    a neutral distance phrase."""
    view = _FACELESS_VIEW.get(shot.get("shot_type"), "turned away from the viewer, the face not visible")
    if shot.get("shot_type") not in ("silhouette", "over_the_shoulder") and (shot.get("face_visibility") != "none" or framing_conflict(shot)):
        view = NEUTRAL_DISTANCE
    if len(names) == 1:
        return f"The only person in the image is {names[0]}, {view}."
    return f"The only people in the image are {', '.join(names[:-1])} and {names[-1]}, {view}."


def _entry(segment: dict, shot: dict, settings: dict, vb: dict, output: Path, flags: list | None,
           notes: list | None = None) -> dict:
    drawn = {item["character_id"] for item in vbible.on_screen(vb)}
    cast = [cid for cid in (shot.get("characters") or []) if isinstance(cid, str) and cid in drawn]
    entry = {
        "shot_id": segment["shot_id"], "scene_id": segment["scene_id"],
        "section_id": segment["section_id"], "start": segment["start"], "end": segment["end"],
        "narration_excerpt": segment["text"], "shot_type": shot.get("shot_type") or "medium",
        "characters": cast, "location": shot.get("location_id") or "",
        "action": str(shot.get("action") or ""), "face_visibility": shot.get("face_visibility") or "partial",
        "appearance": {cid: str(text) for cid, text in (shot.get("appearance") or {}).items() if cid in cast},
        "others_present": shot.get("others_present") if isinstance(shot.get("others_present"), bool) else False,
        "setting_condition": settings.get(shot.get("location_id"), ""),
        "reuse_of": shot.get("reuse_of") or "", "seed": vbible.seed_for("shot", segment["shot_id"]),
        "ambient": str(shot.get("ambient") or ""),
        "seed_offset": 0,
    }
    conflict = framing_conflict(entry) if entry["face_visibility"] == "none" else None
    if conflict:                                          # the action wins: its face is not hidden after all
        print(f"   -> {entry['shot_id']}: face_visibility none contradicts \"{conflict}\"; framed at a distance instead")
        entry["face_visibility"] = "partial"
        entry["framing_resolved"] = conflict
    rewritten = rewrite_action(entry["action"], cast, vb, entry["others_present"])
    if rewritten != entry["action"]:
        print(f"   -> {entry['shot_id']}: wording rewritten ({entry['action'][:50]} -> {rewritten[:50]})")
        entry["wording_rewritten_from"] = entry["action"]
        entry["action"] = rewritten
    if flags:
        entry["flags"] = list(flags)
    if notes:
        entry["warnings"] = list(notes)
    composed = compose_shot(entry, vb, output, assume_references=True)
    entry["prompt"] = composed["prompt"]
    entry["reference_slots"] = composed["slots"]
    return entry


# ── Prompt composition ──────────────────────────────────────────────────────

def _noun(descriptor: str) -> str:
    """"man"/"woman" from the descriptor's nouns, then its pronouns ("on his left ring finger")."""
    words = set(re.findall(r"[a-z]+", str(descriptor or "").lower()))
    if words & {"woman", "female", "girl", "lady"}:
        return "woman"
    if words & {"man", "male", "boy", "gentleman"}:
        return "man"
    if words & {"she", "her", "hers"}:
        return "woman"
    if words & {"he", "his", "him"}:
        return "man"
    return "person"


def compose_shot(shot: dict, vb: dict, output_dir, *, assume_references: bool = False) -> dict:
    """Template kind, final prompt, reference image paths, slot plan and seed for one shot.

    Reference slots in ReferenceMultiLatent (max 4), in priority order: every
    listed character with an approved sheet, whatever the face visibility
    (silhouettes and backs still need build, hair, glasses and wardrobe; at
    most 3, the most important — visual-bible order — first), then the
    location reference (not for inserts/hands), then one style anchor. The
    anchor is dropped first, then the location, never a listed character; a
    4th+ character becomes a distant, indistinct figure. Each reference is
    named by its ordinal in the prompt.
    """
    from agents import identity, style_refs

    style = identity.style_suffix()
    noun = identity.image_noun()
    framing = FRAMING.get(shot.get("shot_type"), "")
    drawn = [item["character_id"] for item in vbible.on_screen(vb)]
    listed = [cid for cid in shot.get("characters") or [] if cid in drawn or not vb.get("characters")]
    order = [item["character_id"] for item in vb.get("characters", [])]
    by_importance = sorted(listed, key=lambda cid: order.index(cid) if cid in order else len(order))
    characters = [cid for cid in listed if cid in by_importance[:MAX_CHARACTER_REFS]]
    background = [cid for cid in listed if cid not in characters]
    references, slots, ref_names, ref_kinds, secondary = [], [], {}, {}, []
    kinds = reference_kinds(shot)

    def sheet(kind: str, cid: str) -> str | None:
        path = vbible.approved_reference(vb, kind, cid, output_dir)
        if path or (assume_references and kind == "character"):
            return path or f"character_refs/{safe_filename(cid)}.png"
        return None

    for cid in characters:
        for position, kind in enumerate(kinds):
            path = sheet(kind, cid)
            if not path:
                continue                                  # no full-body sheet yet: the next kind, if any
            if cid in ref_names:
                secondary.append((kind, cid, path))       # placed after the location, before the style anchor
                continue
            references.append(path)
            slots.append({"slot": len(references) - 1, "kind": kind, "id": cid})
            entry = vbible.character(vb, cid) or {}
            ref_names[cid] = f"the {_noun(entry.get('descriptor', ''))} from the " \
                             f"{ORDINALS[len(references) - 1]} image"
            ref_kinds[cid] = kind
        if cid not in ref_names and kinds == ["body"]:   # no full-body sheet yet: the face sheet beats none (3.9.8 probe)
            path = sheet("character", cid)
            if path:
                references.append(path)
                slots.append({"slot": len(references) - 1, "kind": "character", "id": cid})
                entry = vbible.character(vb, cid) or {}
                ref_names[cid] = f"the {_noun(entry.get('descriptor', ''))} from the {ORDINALS[len(references) - 1]} image"
                ref_kinds[cid] = "character"
    location = vbible.location(vb, shot.get("location")) or {}
    location_text = location.get("descriptor", "")
    exterior = bool(location) and not vbible.location_interior(location)    # Phase 3.9.3-F: no rooms on a moor
    if exterior and shot.get("shot_type") == "wide":
        framing = "wide shot, the figures small within the vast land"
    location_ref = vbible.approved_reference(vb, "location", location.get("location_id", ""), output_dir)
    if location_ref and len(references) < 4 and shot.get("shot_type") not in {"insert", "hands"}:
        references.append(location_ref)
        slots.append({"slot": len(references) - 1, "kind": "location", "id": location.get("location_id")})
        location_text = f"in the {'room' if not exterior else 'place'} from the {ORDINALS[len(references) - 1]} image"
    location_text = _setting_text(location_text, shot.get("setting_condition", ""))
    figure_text = ""
    for kind, cid, path in secondary:
        if len(references) >= 4:
            break
        references.append(path)
        slots.append({"slot": len(references) - 1, "kind": kind, "id": cid})
        pronoun = {"woman": "Her", "man": "His"}.get(_noun((vbible.character(vb, cid) or {}).get("descriptor", "")), "Their")
        figure_text += f" {pronoun} full figure, hair and build as in the {ORDINALS[len(references) - 1]} image." \
            if kind == "body" else f" {pronoun} face as in the {ORDINALS[len(references) - 1]} image."
    others = shot.get("others_present")
    if others is None:      # shot lists before version 2 have no others_present
        others = bool(unnamed_people(shot.get("action", ""), len(characters)))
    # Small figures only in shots with people; objects, inserts and empty rooms get the figure-free
    # suffix and only anchors with nobody in them.
    figures = bool(characters or others) and shot.get("shot_type") != "insert"
    style = identity.style_suffix(figures=figures, exterior=exterior)
    anchors = style_refs.approved_paths() if figures else style_refs.empty_paths()
    style_text = ""
    # Hotfix (3.9.3): never a style board as the only reference (it gets copied); it only joins
    # character or location references.
    if anchors and references and len(references) < 4:
        references.append(anchors[0])
        slots.append({"slot": len(references) - 1, "kind": "style", "id": Path(anchors[0]).stem})
        style_text = f"Rendered in the drawing style of the {ORDINALS[len(references) - 1]} image."

    appearance = shot.get("appearance") or {}
    subjects = [character_sentence(cid, vb, shot.get("scene_id", ""), str(appearance.get(cid) or ""),
                                   ref_names.get(cid, ""), now=ref_kinds.get(cid) == "body") for cid in characters]
    action = _cap(str(shot.get("action", "")).rstrip("."), ACTION_TEXT_MAX)
    article = "an" if noun[0] in "aeiou" else "a"
    # Explicit headcount; a 4th+ listed character (no slot left) stays far and indistinct.
    crowd = headcount([short_name(cid, vb, ref_names) for cid in characters], bool(others), len(background))
    if characters and not others and not background and is_faceless(shot) and \
            not (shot.get("shot_type") == "over_the_shoulder" and len(characters) > 1):    # one of them faces the viewer
        crowd = f"{crowd} {faceless_headcount([short_name(cid, vb, ref_names) for cid in characters], shot)}"
    if shot.get("shot_type") == "over_the_shoulder" and characters:
        framing = over_the_shoulder(characters, vb, ref_names)
    if references:
        lead = f"Create {article} {noun} of " + "; and ".join(subjects) if subjects else f"Create {article} {noun}"
        prompt = f"{lead}.{figure_text} {action}. {crowd} Setting: {location_text}. {framing}. {style_text} {style}"
        kind = "ref"
    else:
        people = f" Featuring {'; and '.join(subjects)}." if subjects else ""
        prompt = f"{article.capitalize()} {noun}: {action}.{people} {crowd} Setting: {location_text}. {framing}. {style}"
        kind = "t2i"
    prompt = re.sub(r"\s+", " ", re.sub(r"\.\s*\.", ".", prompt)).strip()
    return {"kind": kind, "prompt": prompt, "references": references, "slots": slots,
            "seed": int(shot.get("seed", 0)) + 7919 * int(shot.get("seed_offset", 0))}


CROWD_HINT = ("Any other people are few, distant and indistinct in shadow, each clearly different in "
              "age, build, posture and clothing.")
# Explicit people terms: always people beyond the named cast.
_UNNAMED_TERMS = re.compile(
    r"\b(?:guests?|crowds?|strangers?|waiters?|waitress(?:es)?|staff|onlookers?|passers-?by|passerby|"
    r"people in the background)\b")
# "Silhouettes"/"shapes" of these are scenery, not people ("faint urban silhouettes").
_SCENERY = re.compile(r"\b(?:urban|city|cities|skyline|skyscrapers?|buildings?|towers?|rooftops?|trees?|"
                      r"forest|furniture|chairs?|tables?|architecture|architectural|columns?|pillars?|"
                      r"spires?|cranes?|hills?|mountains?)\b")
# Generic words that may just be the listed characters ("Two figures stand…").
_GENERIC_PEOPLE = r"(?:figures|people|persons|men|women|silhouettes|shapes)"
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "a few": 3, "several": 3, "many": 5, "dozens of": 24}
_COUNTED = re.compile(r"\b(\d+|" + "|".join(sorted(_COUNTS, key=len, reverse=True)) + r")\s+(?:[a-z-]+\s+){0,2}?"
                      + _GENERIC_PEOPLE + r"\b")
_OF_THEM = re.compile(r"\b(?:the\s+)?(\w+)\s+of\s+them\b")


def unnamed_people(action: str, cast_size: int) -> list[str]:
    """Phrases in ``action`` that put more people in the frame than the ``cast_size`` named characters.

    Only explicit people terms ("two guests", "a crowd") or a stated number of
    people/figures larger than the cast count; "Two figures stand…" with two
    listed characters is the cast itself, and silhouettes of a skyline,
    buildings, trees or furniture are scenery.
    """
    text = str(action or "").lower()
    found = [match.group(0) for match in _UNNAMED_TERMS.finditer(text)]
    for match in _COUNTED.finditer(text):
        word = match.group(1)
        count = int(word) if word.isdigit() else _COUNTS[word]
        phrase = match.group(0)
        if phrase.endswith(("silhouettes", "shapes")) and _SCENERY.search(text[match.start():match.end() + 40]):
            continue
        if count > cast_size:
            found.append(phrase)
    for match in _OF_THEM.finditer(text):
        word = match.group(1)
        count = int(word) if word.isdigit() else _COUNTS.get(word, 0)
        if count > cast_size:
            found.append(match.group(0))
    return list(dict.fromkeys(found))


# ── Clean composition (Phase 3.4-B) ─────────────────────────────────────────
_SCENE_REF = re.compile(r"\bs\d{2}\b", re.I)
_IN_SCENE = re.compile(r"\s*\b(?:in|during|for|from)\s+scenes?\s+s\d{2}(?:\s*(?:,|and|&)\s*s\d{2})*", re.I)
CHARACTER_TEXT_MAX = 320     # characters per character sentence
ACTION_TEXT_MAX = 420
SETTING_TEXT_MAX = 320


def scene_scoped(text: str, scene_id: str) -> str:
    """Only what belongs to this scene: drop phrases naming another scene, strip "in scene sNN"."""
    kept = []
    for phrase in re.split(r"[;.]\s*", str(text or "")):
        for part in phrase.split(","):
            mentioned = {match.lower() for match in _SCENE_REF.findall(part)}
            if mentioned - {str(scene_id).lower()}:
                continue
            part = _IN_SCENE.sub("", part).strip()
            if part:
                kept.append(part)
    return ", ".join(kept)


def _norm(clause: str) -> str:
    clause = re.sub(r"^(?:wearing|with|in)\s+", "", clause.strip().lower())
    return re.sub(r"[^a-z0-9 ]+", "", clause).strip()


def _cap(text: str, limit: int) -> str:
    """Shorten at a clause boundary (never mid-word)."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ("; ", ", ", " "):
        if mark in cut:
            cut = cut[:cut.rindex(mark)]
            break
    return cut.rstrip(",;. ")


_HAIR_OR_FACE = re.compile(r"\b(?:hair|bob|braid|ponytail|bun|fringe|bangs|curls?|eyes?|face|jaw|skin|freckles?|"
                           r"wrinkles?|beard|moustache|mustache|makeup|make-up|lips?)\b", re.I)


def character_sentence(cid: str, vb: dict, scene_id: str, moment: str = "", ref_name: str = "", *, now: bool = False) -> str:
    """One clause list per character: reference name (or descriptor when there is no reference)
    + signature markers + this scene's outfit + this scene's state + this moment, without
    repeats or phrases from other scenes.

    Phase 3.9.8-B: with a reference the text never re-describes the face or the hair (the sheet carries them; the text
    only pulled the model back to its default face): only the scene's wardrobe, expression, pose and the signature
    markers that agree with the sheet. ``now``: the reference is the full-body sheet in its default outfit."""
    entry = vbible.character(vb, cid) or {}
    pieces = [ref_name] if ref_name else [part.strip() for part in
                                          str(entry.get("descriptor") or cid).rstrip(". ").split(",")]
    markers = vbible.signature_markers(vb, cid)
    if ref_name:
        conflicts = set(entry.get("marker_conflicts") or [])
        markers = [marker for marker in markers if marker not in conflicts and not _HAIR_OR_FACE.search(marker)]
    if markers:
        pieces.append("with " + " and ".join(markers))
    outfit = scene_scoped(vbible.wardrobe(vb, cid, scene_id), scene_id)
    if outfit:
        pieces.append(("now wearing " if now else "wearing ") + outfit[0].lower() + outfit[1:])
    grooming = vbible.grooming(vb, cid, scene_id)
    for extra in (grooming, vbible.scene_state(vb, cid, scene_id), moment):
        parts = [part for part in scene_scoped(extra, scene_id).split(", ") if part]
        if ref_name and extra is grooming:
            parts = [part for part in parts if not _HAIR_OR_FACE.search(part)]
        pieces += parts
    kept, seen = [], []
    for piece in pieces:
        norm = _norm(piece)
        if not norm or any(norm in earlier for earlier in seen):
            continue
        kept.append(piece)
        seen.append(norm)
    return _cap(", ".join(kept), CHARACTER_TEXT_MAX)


def _setting_text(descriptor: str, condition: str) -> str:
    """The location once: a condition that repeats the descriptor is dropped (and vice versa)."""
    descriptor, condition = str(descriptor or "").strip().rstrip("."), str(condition or "").strip().rstrip(".")
    if not condition or _norm(condition) in _norm(descriptor):
        return _cap(descriptor, SETTING_TEXT_MAX)
    if _norm(descriptor) in _norm(condition):
        return _cap(condition, SETTING_TEXT_MAX)
    return _cap(f"{descriptor}, {condition}", SETTING_TEXT_MAX)


def short_name(cid: str, vb: dict, ref_names: dict) -> str:
    """"the man from the first image", or without a reference "the man with horn-rimmed glasses"."""
    if ref_names.get(cid):
        return ref_names[cid]
    noun = _noun((vbible.character(vb, cid) or {}).get("descriptor", ""))
    markers = vbible.signature_markers(vb, cid)
    return f"the {noun} with {markers[0]}" if markers else f"the {noun}"


def over_the_shoulder(characters: list[str], vb: dict, ref_names: dict) -> str:
    """Name whose shoulder it is (the first listed character) and who faces the viewer,
    so the model never invents a third body seen from behind."""
    first = short_name(characters[0], vb, ref_names)
    if len(characters) == 1:
        return f"seen over the shoulder of {first}, the only person in the frame"
    return f"seen over the shoulder of {first}; {short_name(characters[1], vb, ref_names)} faces the viewer"


_NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six")


def headcount(names: list[str], others: bool = False, background: int = 0) -> str:
    """The exact number and identity of the people in the frame (Phase 3.4-C).

    "Exactly two people in the scene: the man from the first image and the woman
    from the second image." Unnamed guests are added only when the narration
    places them (others_present); a 4th+ character stays distant and indistinct.
    """
    if not names and not others and not background:
        return "The room is empty and still."
    listing = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""
    count = len(names)
    word = _NUMBER_WORDS[count] if count < len(_NUMBER_WORDS) else str(count)
    sentence = (f"Exactly {word} {'person' if count == 1 else 'people'} in the "
                f"{'foreground' if background else 'scene'}: {listing}") if names else "In the scene"
    extras = []
    if background:
        extras.append(f"{_NUMBER_WORDS[background] if background < len(_NUMBER_WORDS) else background} more "
                      f"distant, indistinct figure{'s' if background > 1 else ''} in shadow")
    if others:
        extras.append("a few distant, indistinct guests in shadow, each clearly different in age, build, "
                      "posture and clothing")
    if extras:
        sentence += (", plus " if names else ": ") + " and ".join(extras)
    elif count >= 2:
        sentence += "; nobody else is present"
    return sentence + "."


def alone_hint(count: int) -> str:
    """Positive wording for 'nobody else is here' (the negative prompt is zeroed)."""
    if count <= 0:
        return "The room is empty and still."
    if count == 1:
        return "Alone in the room."
    if count == 2:
        return "The two of them alone in the room."
    return f"Only these {count} people in the room."


def prompt_advice(prompt: str, scene_narration: str) -> list[str]:
    """Advisory check of a composed prompt (never blocks): injuries, decay or tears the
    scene's narration lacks, e.g. coming from a visual-bible wardrobe line."""
    from agents import identity
    unsupported = identity.unsupported_terms(prompt, scene_narration)
    return [f"the prompt mentions {', '.join(unsupported)}, which the scene's narration does not"] \
        if unsupported else []


def scene_narration(shots: list[dict]) -> dict[str, str]:
    """Whole narration per scene, rebuilt from the shots' excerpts."""
    text: dict[str, str] = {}
    for shot in shots:
        text[shot.get("scene_id", "")] = f"{text.get(shot.get('scene_id', ''), '')} {shot.get('narration_excerpt', '')}"
    return text


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
