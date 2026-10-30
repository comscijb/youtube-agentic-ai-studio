"""Edits on any project, characters with no body / voice only, and titles kept until the user switches."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import packaging, researcher
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from scripts import apply_edits
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_narrative import bible, screenplay_with_counts, story_brief
from tests.test_review_reliability import assessment, finding

# ── 1. scripts.apply_edits ──────────────────────────────────────────────────

LECTURE = "Being listened to was not the same as being known."


def make_project(root: Path) -> Path:
    folder = root / "2026-10-30-test"
    script = screenplay_with_counts([80, 80, 80], [80, 80, 80])
    script["target_duration_minutes"] = 240 / 125
    script["sections"][1]["narration"] += " " + LECTURE
    lecture = finding("Scene 2 lectures.", issue_type="lecture")
    lecture["scene_ids"] = [2]
    other = finding("Scene 3 repeats a motif.", issue_type="repetition_control", severity="minor")
    other["scene_ids"] = [3]
    from agents.narrative import deterministic_review, merge_editorial_review
    review = merge_editorial_review(deterministic_review(script, 240 / 125), assessment([lecture, other]))
    for name, value in (("project.json", {"slug": folder.name, "stages": {}}), ("story_brief.json", story_brief()),
                        ("character_bible.json", bible()), ("story_outline.json", {"scenes": []}),
                        ("script.json", script), ("narrative_review.json", review),
                        ("review_approval.json", {"approved_at": "x"})):
        atomic_write_json(folder / name, value)
    (folder / "narration.mp3").write_bytes(b"audio")
    return folder


class ApplyEditsTests(unittest.TestCase):
    def run_main(self, root, edits, *extra):
        path = Path(root, "edits.json")
        path.write_text(json.dumps(edits), encoding="utf-8")
        out = io.StringIO()
        with patch("studio.projects.root", return_value=Path(root)), redirect_stdout(out), redirect_stderr(out):
            code = apply_edits.main(["--project", "2026-10-30-test", "--edits", str(path), *extra])
        return code, out.getvalue()

    def test_edits_are_recorded_reviewed_and_invalidate_downstream(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = make_project(Path(temp))
            code, out = self.run_main(temp, [{"scene_id": 2, "find": LECTURE, "replace": "He said nothing."}])
            script = load_json(folder / "script.json")
            review = load_json(folder / "narrative_review.json")
            backups = list(folder.glob("script.pre_edit-*.json"))
            self.assertEqual(code, 0, out)
            self.assertIn("He said nothing.", script["sections"][1]["narration"])
            self.assertEqual(script["editorial_edits"][0]["author"], "human_editorial")
            self.assertEqual([item["claim"] for item in review["addressed_editorial_findings"]], ["Scene 2 lectures."])
            self.assertTrue(review["editorial_review_stale"])
            self.assertFalse((folder / "narration.mp3").exists())
            self.assertFalse((folder / "review_approval.json").exists())
            self.assertEqual(len(backups), 1)
            # A second run keeps earlier edits and their addressed findings.
            code, _ = self.run_main(temp, [{"scene_id": "s03", "find": "word1 ", "replace": "word1, "}])
            again = load_json(folder / "narrative_review.json")
        self.assertEqual(code, 0)
        self.assertEqual(len(again["editorial_edits"]), 2)
        self.assertEqual(sorted(item["claim"] for item in again["addressed_editorial_findings"]),
                         ["Scene 2 lectures.", "Scene 3 repeats a motif."])

    def test_one_bad_find_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = make_project(Path(temp))
            before = (folder / "script.json").read_bytes()
            code, out = self.run_main(temp, [{"scene_id": 2, "find": LECTURE, "replace": "x"},
                                             {"scene_id": 1, "find": "word", "replace": "y"}])
            self.assertEqual((folder / "script.json").read_bytes(), before)
            self.assertTrue((folder / "narration.mp3").exists())
        self.assertEqual(code, 1)
        self.assertIn("must be exactly once", out)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = make_project(Path(temp))
            before = (folder / "script.json").read_bytes()
            code, out = self.run_main(temp, [{"scene_id": 2, "find": LECTURE, "replace": "x"}], "--dry-run")
            self.assertEqual((folder / "script.json").read_bytes(), before)
        self.assertEqual(code, 0)
        self.assertIn("would apply 1 edit(s)", out)


# ── 2. presence: on_screen | voice_only | none ──────────────────────────────

BIBLE = {"characters": [
    {"character_id": "walter_hale", "name": "Walter Hale", "age": "68", "role": "protagonist",
     "background": "A retired piano tuner who calls his daughter June every Sunday."},
    {"character_id": "june_hale", "name": "June Hale", "age": "41", "role": "supporting",
     "background": "A night-shift nurse."},
    {"character_id": "ada", "name": "Ada", "age": "N/A", "role": "supporting",
     "background": "An AI companion app lacking any physical body or form."},
]}
BRIEF = {"author_brief": {"protagonist": "Walter Hale, 68, retired piano tuner.", "other_characters": [
    "June Hale, 41, his daughter, night-shift nurse. Only a voice on the phone.",
    "Ada, the AI companion app: no body, never drawn as a person."]}}
OUTLINE = {"scenes": [{"scene_id": "s01", "title": "One", "setting": "Front room",
                       "characters_present": ["walter_hale", "june_hale", "ada"]}]}
SCRIPT = {"sections": [{"id": 1, "outline_scene_id": "s01", "narration": "Walter talked to the glowing phone."}]}


def visual_bible(ada_descriptor="", ada_proxy="the warm gold glow of Walter's phone screen"):
    return {
        "characters": [
            {"character_id": "walter_hale", "descriptor": "a 68-year-old man, slight stoop, round glasses",
             "signature_markers": ["round wire-rimmed glasses"],
             "wardrobe": [{"scene_ids": ["s01"], "outfit": "a worn grey cardigan"}]},
            {"character_id": "june_hale", "descriptor": "a 41-year-old woman, dark bob",
             "visual_proxy": "Walter listening with the phone to his ear", "wardrobe": []},
            {"character_id": "ada", "descriptor": ada_descriptor, "visual_proxy": ada_proxy, "wardrobe": []},
        ],
        "locations": [{"location_id": "front_room", "name": "Front room", "descriptor": "a narrow front room",
                       "scene_ids": ["s01"]}],
        "thumbnail_shot": {"description": "Walter in the dark", "characters": ["walter_hale"],
                           "location_id": "front_room"},
    }


def build(value, temp):
    with patch.object(vbible, "generate", return_value=json.dumps(value)) as call, redirect_stdout(io.StringIO()):
        vb = vbible.build_visual_bible(SCRIPT, BRIEF, BIBLE, OUTLINE, temp, force=True)
    return vb, call


class PresenceTests(unittest.TestCase):
    def test_deduced_from_the_premise(self):
        self.assertEqual(vbible.presences(BIBLE, BRIEF),
                         {"walter_hale": "on_screen", "june_hale": "voice_only", "ada": "none"})
        self.assertEqual(vbible.presences(BIBLE, {}, {"june_hale": "on_screen"})["june_hale"], "on_screen")
        self.assertEqual(vbible.presences(BIBLE, {})["ada"], "none")              # the bible alone says it

    def test_build_keeps_no_person_for_absent_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            vb, call = build(visual_bible(ada_descriptor="a 30-year-old woman made of light"), temp)
        prompt = call.call_args.args[0]
        self.assertIn('"ada": "none"', prompt)
        self.assertIn('"june_hale": "voice_only"', prompt)
        ada, june = vbible.character(vb, "ada"), vbible.character(vb, "june_hale")
        self.assertEqual((ada["presence"], ada["descriptor"], ada["wardrobe"]), ("none", "", []))
        self.assertEqual((june["presence"], june["descriptor"]), ("voice_only", ""))
        self.assertEqual([item["character_id"] for item in vbible.on_screen(vb)], ["walter_hale"])
        self.assertNotIn("visual_proxy", vbible.character(vb, "walter_hale"))

    def test_validator_rejects_a_person_proxy_and_absent_thumbnail_people(self):
        fixed = vbible.presences(BIBLE, BRIEF)
        with self.assertRaises(NarrativeValidationError) as caught:
            vbible.validate_visual_bible(visual_bible(ada_proxy="a 30-year-old woman glowing"), BIBLE, OUTLINE,
                                         presence=fixed)
        self.assertIn("visual_proxy must not describe a person", str(caught.exception))
        value = visual_bible()
        value["thumbnail_shot"]["characters"].append("june_hale")
        with self.assertRaises(NarrativeValidationError):
            vbible.validate_visual_bible(value, BIBLE, OUTLINE, presence=fixed)
        missing = visual_bible(ada_proxy="")
        with self.assertRaises(NarrativeValidationError):
            vbible.validate_visual_bible(missing, BIBLE, OUTLINE, presence=fixed)

    def test_no_portrait_for_a_bodiless_character(self):
        from video import shot_generation
        with tempfile.TemporaryDirectory() as temp:
            vb, _ = build(visual_bible(), temp)
            refs = Path(temp, "character_refs")
            refs.mkdir()
            for name in ("ada", "june_hale"):
                (refs / f"{name}.png").write_bytes(b"png")
                vbible.record_approval(vb, "character", name, f"character_refs/{name}.png", seed=1, prompt="p",
                                       output_dir=temp)
            self.assertIsNone(vbible.approved_reference(vb, "character", "ada", temp))
            archived = shot_generation.archive_stale_references(Path(temp), vb)
            self.assertTrue((archived / "character_refs" / "ada.png").is_file())
            self.assertTrue((refs / "june_hale.png").is_file())                  # voice_only: left alone
            rebuilt, _ = build(visual_bible(), temp)
        self.assertNotIn("character:ada", rebuilt["references"])

    def test_presence_override_survives_a_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            build(visual_bible(), temp)
            vbible.set_presence(temp, "june_hale", "none")
            vb, call = build(visual_bible(), temp)
        self.assertEqual(vbible.presence(vb, "june_hale"), "none")
        self.assertEqual(vb["presence_overrides"], {"june_hale": "none"})

    def test_shots_never_list_or_name_absent_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            vb, _ = build(visual_bible(), temp)
        segments = [{"shot_id": f"s01_0{n}", "scene_id": "s01", "text": "Walter listened."} for n in (1, 2, 3)]
        base = {"shot_type": "medium", "location_id": "front_room", "face_visibility": "partial",
                "others_present": False, "appearance": {"walter_hale": "calm"}}
        value = {"shots": [
            dict(base, characters=["walter_hale"], action="Walter holds the phone; its gold glow lights his hand"),
            dict(base, characters=["walter_hale", "ada"], action="Walter looks at the phone",
                 appearance={"walter_hale": "calm", "ada": "warm"}),
            dict(base, characters=["walter_hale"], action="June talks while Walter listens at the window"),
        ]}
        issues, general = shots_module.shot_issues(value, segments, vb)
        self.assertEqual(general, [])
        self.assertNotIn("s01_01", issues)
        self.assertIn("ada is none and never in a frame", " ".join(issues["s01_02"]))
        self.assertIn("describes ['june_hale']", " ".join(issues["s01_03"]))
        composed = shots_module.compose_shot({"characters": ["walter_hale", "ada"], "action": "x",
                                              "shot_type": "medium", "location": "front_room"}, vb, ".")
        self.assertNotIn("from the second image", composed["prompt"])
        self.assertEqual([slot["id"] for slot in composed["slots"] if slot["kind"] == "character"], [])
        self.assertIn("never named", shots_module._scene_contract("reference"))


# ── 3. titles ───────────────────────────────────────────────────────────────

SCORED = [{"title": "He Cut Off His Daughter for a Warm Glow", "passes": True, "total": 100, "findings": []},
          {"title": "The Only One Who Never Got Tired of Him", "passes": True, "total": 80, "findings": []}]


class TitleTests(unittest.TestCase):
    def test_the_premise_title_is_kept_and_the_best_is_a_suggestion(self):
        brief = {"author_title": "The Only One Who Never Got Tired of Him",
                 "video_title": "The Only One Who Never Got Tired of Him", "title_review": {}}
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(packaging, "score_titles", return_value=SCORED):
            researcher._apply_title_review(brief, {"selected_title": "Something Else Entirely"})
            researcher._score_titles(brief)
            record = load_json(Path(temp, packaging.TITLES_FILE))
        self.assertEqual(brief["video_title"], "The Only One Who Never Got Tired of Him")
        self.assertEqual(brief["suggested_title"], "He Cut Off His Daughter for a Warm Glow")
        self.assertEqual(record["selected"], "The Only One Who Never Got Tired of Him")
        self.assertEqual(record["history"], [])

    def test_a_title_the_user_chose_is_kept(self):
        brief = {"video_title": "x", "title_review": {}}
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(packaging, "score_titles", return_value=SCORED):
            packaging.save_titles(temp, None, "A Title I Picked Myself", reason="user")
            researcher._score_titles(brief)
        self.assertEqual(brief["video_title"], "A Title I Picked Myself")

    def test_without_an_author_or_user_title_the_best_is_chosen(self):
        brief = {"video_title": "x", "title_review": {}}
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(packaging, "score_titles", return_value=SCORED):
            researcher._score_titles(brief)
        self.assertEqual(brief["video_title"], "He Cut Off His Daughter for a Warm Glow")

    def test_the_author_brief_sets_the_title(self):
        from agents import premise_brief
        brief = {"title_candidates": [], "psychological_theme": "", "psychological_angle": ""}
        researcher._apply_author_brief(brief, premise_brief.parse(premise_brief.TEMPLATE), [])
        self.assertEqual((brief["video_title"], brief["author_title"]),
                         ("The Only One Who Never Got Tired of Him",) * 2)
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        self.assertIn("é mantido até você clicar em Usar", page)


if __name__ == "__main__":
    unittest.main()
