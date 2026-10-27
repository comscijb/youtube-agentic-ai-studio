"""Phase 3.1-A/B: visual bible and narration-aligned shot list (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import shot_list as shots_module
from agents import identity
from agents import visual_bible as vbible
from agents.narrative import NarrativeValidationError, atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

BIBLE = {"characters": [
    {"character_id": "julian_vance", "name": "Julian Vance", "age": "37", "role": "protagonist"},
    {"character_id": "elena_vance", "name": "Elena Vance", "age": "35", "role": "deuteragonist"},
    {"character_id": "marcus_croft", "name": "Marcus Croft", "age": "40", "role": "supporting"},
]}
OUTLINE = {"scenes": [
    {"scene_id": "s01", "title": "One", "setting": "Loft", "characters_present": ["julian_vance", "elena_vance"]},
    {"scene_id": "s02", "title": "Two", "setting": "Loft", "characters_present": ["julian_vance", "marcus_croft"]},
]}
SCRIPT = {"sections": [
    {"id": 1, "outline_scene_id": "s01", "narration": "Elena wore her ivory silk shirt."},
    {"id": 2, "outline_scene_id": "s02", "narration": "Marcus raised his glass."},
]}
VISUAL_BIBLE = {
    "global_style": {"style_suffix": "cinematic still, low-key light, 35 mm grain, no text, no watermark",
                     "negative_prompt": "text, watermark"},
    "characters": [
        {"character_id": "julian_vance", "descriptor": "a 37-year-old white man, lean, sharp jaw",
         "signature_markers": ["horn-rimmed glasses"],
         "wardrobe": [{"scene_ids": ["s01", "s02"], "outfit": "a charcoal tailored suit"}]},
        {"character_id": "elena_vance", "descriptor": "a 35-year-old woman of Spanish descent, dark bob",
         "signature_markers": ["dark bob"],
         "wardrobe": [{"scene_ids": ["s01"], "outfit": "an ivory silk shirt"}]},
        {"character_id": "marcus_croft", "descriptor": "a 40-year-old broad-shouldered man, grey stubble",
         "signature_markers": ["gold signet ring"],
         "wardrobe": [{"scene_ids": ["s02"], "outfit": "a navy blazer"}]},
    ],
    "locations": [{"location_id": "loft", "name": "Julian's loft",
                   "descriptor": "vaulted concrete loft, blackened oak table, smoked decanters",
                   "scene_ids": ["s01", "s02"]}],
    "thumbnail_shot": {"description": "Julian alone at the table", "characters": ["julian_vance"],
                       "location_id": "loft"},
}


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class VisualBibleTests(unittest.TestCase):
    def test_build_validates_adds_seeds_and_caches(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(vbible, "generate", return_value=json.dumps(VISUAL_BIBLE)) as call:
            first = quiet(vbible.build_visual_bible, SCRIPT, {}, BIBLE, OUTLINE, temp)
            again = quiet(vbible.build_visual_bible, SCRIPT, {}, BIBLE, OUTLINE, temp)
        self.assertEqual(call.call_count, 1)
        prompt = call.call_args.args[0]
        self.assertIn("immutable descriptor", prompt)
        self.assertIn("ivory silk shirt", prompt)              # screenplay is in the context
        self.assertIn("NO creatures", prompt)                  # identity image rules
        self.assertNotIn("35 mm", prompt)
        self.assertEqual(first["global_style"]["style_hash"], identity.style_hash())
        self.assertEqual(first, again)
        julian = vbible.character(first, "julian_vance")
        self.assertEqual(julian["seed"], vbible.seed_for("character", "julian_vance"))
        self.assertEqual(vbible.wardrobe(first, "elena_vance", "s01"), "an ivory silk shirt")
        self.assertEqual(vbible.location_for_scene(first, "s02")["location_id"], "loft")

    def test_validation_rejects_missing_character_unknown_scene_and_text(self):
        cases = []
        missing = json.loads(json.dumps(VISUAL_BIBLE)); missing["characters"].pop()
        cases.append((missing, "missing characters: marcus_croft"))
        bad_scene = json.loads(json.dumps(VISUAL_BIBLE))
        bad_scene["characters"][0]["wardrobe"][0]["scene_ids"] = ["s09"]
        cases.append((bad_scene, "unknown: ['s09']"))
        creature = json.loads(json.dumps(VISUAL_BIBLE))
        creature["locations"][0]["descriptor"] += ", tentacles in the fog"
        cases.append((creature, "excluded subjects: ['tentacles']"))
        uncovered = json.loads(json.dumps(VISUAL_BIBLE))
        uncovered["locations"][0]["scene_ids"] = ["s01"]
        cases.append((uncovered, "no location covers scenes: s02"))
        for value, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(NarrativeValidationError) as caught:
                    vbible.validate_visual_bible(value, BIBLE, OUTLINE)
                self.assertIn(message, str(caught.exception))

    def test_approved_references_survive_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            refs = Path(temp, "character_refs"); refs.mkdir()
            Path(refs, "julian_vance.png").write_bytes(b"png")
            with patch.object(vbible, "generate", return_value=json.dumps(VISUAL_BIBLE)):
                vb = quiet(vbible.build_visual_bible, SCRIPT, {}, BIBLE, OUTLINE, temp)
                vbible.record_approval(vb, "character", "julian_vance", "character_refs/julian_vance.png",
                                       seed=7, prompt="portrait", output_dir=temp)
                rebuilt = quiet(vbible.build_visual_bible, SCRIPT, {}, BIBLE, OUTLINE, temp, force=True)
            self.assertEqual(rebuilt["references"]["character:julian_vance"]["seed"], 7)
            self.assertTrue(vbible.approved_reference(rebuilt, "character", "julian_vance", temp))


def cues_for(sentences, start=0.0, gap=0.2):
    """SRT cues (ms) for (text, seconds) sentences."""
    cues, cursor = [], start
    for text, seconds in sentences:
        cues.append((cursor * 1000, (cursor + seconds) * 1000, text))
        cursor += seconds + gap
    return cues


class SegmentationTests(unittest.TestCase):
    def test_cuts_fall_on_sentence_boundaries_and_tile_the_scene(self):
        sentences = shots_module.sentences_from_cues(cues_for(
            [("He waited.", 3), ("The table", 1), ("was set for eight.", 2), ("Nobody came.", 4),
             ("He poured a glass.", 5), ("Then another.", 3)]))
        self.assertEqual(sentences[1]["text"], "The table was set for eight.")
        scene = {"start": 0.0, "end": 19.5}
        segments = shots_module.segment_scene(scene, sentences, 6, 12)
        sentence_ends = {round(s["end"], 3) for s in sentences}
        self.assertEqual(segments[0]["start"], 0.0)
        self.assertEqual(segments[-1]["end"], 19.5)
        for a, b in zip(segments, segments[1:]):
            self.assertEqual(a["end"], b["start"])
            self.assertIn(a["end"], sentence_ends)
        self.assertTrue(all(6 <= s["end"] - s["start"] <= 12.5 for s in segments[:-1]))

    def test_budget_merges_shortest_pairs_within_scenes(self):
        segments = [{"scene_id": "s01", "start": i * 6.0, "end": (i + 1) * 6.0, "text": f"t{i}"}
                    for i in range(4)] + \
                   [{"scene_id": "s02", "start": 24 + i * 6.0, "end": 30 + i * 6.0, "text": f"u{i}"}
                    for i in range(2)]
        merged = shots_module.enforce_budget(segments, 3)
        self.assertEqual(len(merged), 3)
        self.assertTrue(all(item["scene_id"] in {"s01", "s02"} for item in merged))
        self.assertEqual(merged[-1]["end"], 36.0)
        self.assertEqual(sum(item["end"] - item["start"] for item in merged), 36.0)

    def test_real_benchmark_timing_respects_budget(self):
        output = Path("output")
        if not (output / "scene_timeline.json").exists():
            self.skipTest("no rendered output in this checkout")
        from video.timeline import parse_srt
        script = json.loads((output / "script.json").read_text(encoding="utf-8"))
        timeline = json.loads((output / "scene_timeline.json").read_text(encoding="utf-8"))
        segments = shots_module.plan_segments(script, timeline, parse_srt(output / "narration.srt"))
        self.assertLessEqual(len(segments), config.IMAGEGEN_MAX_UNIQUE_SHOTS)
        self.assertAlmostEqual(sum(s["end"] - s["start"] for s in segments),
                               timeline["total_duration"], places=2)


def scene_responder(plan, edit=None):
    """Fake Gemini for the per-scene shot list: answers each call with that scene's shots.

    ``edit(scene_id, call_number, value)`` may change the answer (e.g. to break a shot).
    """
    import re
    calls = {}

    def respond(prompt, **kwargs):
        scene = re.search(r"SCENE_ID: (\S+)", prompt).group(1)
        calls[scene] = calls.get(scene, 0) + 1
        shots = [dict(shot) for shot in plan["shots"] if shot["shot_id"].startswith(scene + "_")]
        value = {"settings": plan.get("settings"), "shots": shots}
        if edit:
            value = edit(scene, calls[scene], value, prompt)
        return json.dumps(value)
    return respond


def planned_shots(segments, full=1):
    shots = []
    for index, segment in enumerate(segments):
        shots.append({"shot_id": segment["shot_id"], "shot_type": "medium" if index % 2 else "wide",
                      "characters": ["julian_vance"], "location_id": "loft",
                      "action": "He straightens a linen napkin", "face_visibility":
                          "full" if index < full else "partial", "reuse_of": "",
                      "appearance": {"julian_vance": "neatly groomed, composed expression"},
                      "others_present": False})
    return {"settings": {"loft": "immaculate and expensive: polished surfaces, pristine walls"},
            "shots": shots}


class ShotListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        atomic_write_json(self.root / "scene_timeline.json", {"total_duration": 40.0, "scenes": [
            {"scene_id": 1, "outline_scene_id": "s01", "start": 0.0, "end": 20.0, "duration": 20.0},
            {"scene_id": 2, "outline_scene_id": "s02", "start": 20.0, "end": 40.0, "duration": 20.0}]})
        lines = []
        for index, (start, end, text) in enumerate(
                cues_for([("Julian set the table.", 6), ("Elena watched.", 6), ("Nobody spoke.", 7),
                          ("Marcus arrived.", 6), ("He raised a glass.", 6), ("It was over.", 7)])):
            lines.append(f"{index + 1}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
        (self.root / "narration.srt").write_text("\n".join(lines), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def build(self, response, **settings):
        with patch.object(shots_module, "generate", side_effect=response) as call:
            with patch.multiple(config, **settings) if settings else _null():
                result = quiet(shots_module.build_shot_list, SCRIPT, OUTLINE, VISUAL_BIBLE, self.root)
        return result, call

    def segments(self):
        from video.timeline import parse_srt
        return shots_module.plan_segments(SCRIPT, json.loads(
            (self.root / "scene_timeline.json").read_text(encoding="utf-8")),
            parse_srt(self.root / "narration.srt"))

    def test_shot_list_fields_prompts_and_cache(self):
        respond = scene_responder(planned_shots(self.segments()))
        result, call = self.build(respond)
        self.assertEqual(call.call_count, 2)                     # one call per scene, no repairs
        self.assertEqual(result["flagged"], [])
        shot = result["shots"][0]
        for field in ("start", "end", "narration_excerpt", "shot_type", "characters", "location",
                      "prompt", "face_visibility", "reuse_of"):
            self.assertIn(field, shot)
        self.assertTrue(shot["prompt"].startswith("Create an illustration of the man from the first image"))
        self.assertIn("charcoal tailored suit", shot["prompt"])
        self.assertIn("without lettering", shot["prompt"])
        self.assertEqual(shot["reference_slots"][0], {"slot": 0, "kind": "character", "id": "julian_vance"})
        self.assertEqual(result["shots"][-1]["end"], 40.0)
        self.assertEqual(result["unique_shots"], len(result["shots"]))
        _, again = self.build(respond)
        again.assert_not_called()

    def test_validation_enforces_face_mix_reuse_and_ids(self):
        segments = self.segments()
        all_full = planned_shots(segments, full=len(segments))
        shots = shots_module.validate_shots(all_full, segments, VISUAL_BIBLE, OUTLINE, "reference")
        self.assertIn("below the 40% target", " ".join(shots_module.shot_warnings(shots, "reference")))
        bad = planned_shots(segments)
        bad["shots"][0]["reuse_of"] = bad["shots"][1]["shot_id"]
        bad["shots"][1]["location_id"] = "beach"
        with self.assertRaises(NarrativeValidationError) as caught:
            shots_module.validate_shots(bad, segments, VISUAL_BIBLE, OUTLINE, "reference")
        self.assertIn("reuse_of must name an earlier shot", str(caught.exception))
        self.assertIn("location_id", str(caught.exception))
        limited = planned_shots(segments, full=3)
        with patch.object(config, "IMAGEGEN_MAX_FULL_FACE_SHOTS", 2):
            shots = shots_module.validate_shots(limited, segments, VISUAL_BIBLE, OUTLINE, "limited_faces")
            self.assertIn("above the limited_faces target of 2",
                          " ".join(shots_module.shot_warnings(shots, "limited_faces")))

    def test_budget_is_applied_before_planning(self):
        with patch.object(config, "IMAGEGEN_MAX_UNIQUE_SHOTS", 2):
            segments = self.segments()
        self.assertEqual(len(segments), 2)
        self.assertEqual([s["shot_id"] for s in segments], ["s01_01", "s02_01"])


class ComposeTests(unittest.TestCase):
    def shot(self, **kwargs):
        value = {"shot_id": "s02_01", "scene_id": "s02", "shot_type": "medium",
                 "characters": ["julian_vance", "marcus_croft"], "location": "loft",
                 "action": "They face each other across the table", "face_visibility": "partial",
                 "seed": 100, "seed_offset": 0}
        value.update(kwargs)
        return value

    def test_reference_slots_characters_then_location(self):
        with tempfile.TemporaryDirectory() as temp:
            vb = json.loads(json.dumps(VISUAL_BIBLE))
            for kind, ident, rel in (("character", "julian_vance", "character_refs/julian_vance.png"),
                                     ("character", "marcus_croft", "character_refs/marcus_croft.png"),
                                     ("location", "loft", "location_refs/loft.png")):
                Path(temp, rel).parent.mkdir(exist_ok=True)
                Path(temp, rel).write_bytes(b"png")
                vb.setdefault("references", {})[f"{kind}:{ident}"] = {
                    "path": rel, "seed": 1, "prompt": "", "style_hash": identity.style_hash(),
                    "sheet_version": vbible.CHARACTER_SHEET_VERSION}
            composed = shots_module.compose_shot(self.shot(), vb, temp)
        self.assertEqual(composed["kind"], "ref")
        self.assertEqual([Path(p).name for p in composed["references"]],
                         ["julian_vance.png", "marcus_croft.png", "loft.png"])
        prompt = composed["prompt"]
        self.assertIn("the man from the first image, with horn-rimmed glasses, wearing a charcoal tailored suit", prompt)
        self.assertIn("the man from the second image, with gold signet ring, wearing a navy blazer", prompt)
        self.assertIn("in the room from the third image", prompt)

    def test_four_characters_keep_the_three_most_important_as_references(self):
        vb = json.loads(json.dumps(VISUAL_BIBLE))
        vb["characters"].append({"character_id": "guest", "descriptor": "an older woman",
                                 "wardrobe": [{"scene_ids": ["s02"], "outfit": "a black dress"}]})
        shot = self.shot(characters=["guest", "julian_vance", "marcus_croft", "elena_vance"])
        composed = shots_module.compose_shot(shot, vb, "unused", assume_references=True)
        self.assertEqual([slot["id"] for slot in composed["slots"]],
                         ["julian_vance", "marcus_croft", "elena_vance"])
        self.assertNotIn("an older woman", composed["prompt"])
        self.assertIn("Exactly three people in the foreground: the man from the first image, the man from the "
                      "second image and the woman from the third image, plus one more distant, indistinct "
                      "figure in shadow.", composed["prompt"])

    def test_environment_shot_uses_t2i_and_new_seed_offset(self):
        composed = shots_module.compose_shot(
            self.shot(characters=[], shot_type="insert", action="A smoked decanter on oak",
                      face_visibility="none", seed_offset=1), VISUAL_BIBLE, "unused")
        self.assertEqual(composed["kind"], "t2i")
        self.assertEqual(composed["references"], [])
        self.assertIn("vaulted concrete loft", composed["prompt"])
        self.assertEqual(composed["seed"], 100 + 7919)

    def test_resolve_image_follows_reuse(self):
        shots = [{"shot_id": "a", "image_path": "a.jpg", "reuse_of": ""},
                 {"shot_id": "b", "reuse_of": "a"}]
        self.assertEqual(shots_module.resolve_image(shots, shots[1]), "a.jpg")


def _ts(ms):
    ms = int(ms)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
