"""Phase 3.9.7: the cold open, from the brief to the description. No live calls (the outline is a recorded mock)."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from agents import cold_open, narrative, premise_brief, prose_lint
from agents.narrative import atomic_write_json
from audio import assembly
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

FIXTURE = json.loads(Path("tests/fixtures/cold_open_filmed_childhood.json").read_text(encoding="utf-8"))
TYPE_A, TYPE_C = "realistic_psychological_fiction", "dark_human_parable"
ON = {"A": "flash_forward", "B": "flash_forward", "C": "omen"}
OFF = {"A": "off", "B": "off", "C": "off"}


def outline():
    return deepcopy(FIXTURE["outline"])


def sections():
    return deepcopy(FIXTURE["sections"])


def kinds(found):
    return [(item["severity"], item["issue_type"]) for item in found]


class BriefTests(unittest.TestCase):
    def test_the_fixture_brief_keeps_its_cold_open_beat(self):
        brief = premise_brief.parse(Path("briefs/filmed-childhood.md").read_text(encoding="utf-8"))
        self.assertIn("cold_open", brief["beats"])
        self.assertTrue(brief["beats"]["cold_open"].startswith("night, the editing desk"))
        self.assertEqual(list(brief["beats"])[0], "cold_open")
        self.assertIn("cold_open", premise_brief.coverage(brief, {}))

    def test_every_alias_maps_to_the_beat_and_its_movement(self):
        for key in ("Cold open", "cold_open", "Teaser", "Omen"):
            beats = premise_brief.parse_markdown(f"# Logline\nX\n\n# Beats\n- {key}: a moment.\n- Hook: a hook.")["beats"]
            self.assertEqual(beats.get("cold_open"), "a moment.", key)
        self.assertEqual(premise_brief.BEAT_MOVEMENTS["cold_open"], ("cold_open",))
        self.assertIn("cold_open", narrative.ARC_MOVEMENTS)

    def test_the_template_carries_a_cold_open(self):
        self.assertIn("- Cold open:", premise_brief.TEMPLATE)
        self.assertIn("cold_open", premise_brief.parse_markdown(premise_brief.TEMPLATE)["beats"])


class ModeTests(unittest.TestCase):
    def test_defaults_per_type_and_the_brief_beat_wins_over_off(self):
        with patch.object(config, "COLD_OPEN", ON):
            self.assertEqual(cold_open.mode_for(TYPE_A), "flash_forward")
            self.assertEqual(cold_open.mode_for(TYPE_C), "omen")
        with patch.object(config, "COLD_OPEN", OFF):
            self.assertEqual(cold_open.mode_for(TYPE_A), "off")
            self.assertEqual(cold_open.mode_for(TYPE_A, beat="a moment"), "flash_forward")

    def test_off_reproduces_the_old_behaviour(self):
        with patch.object(config, "COLD_OPEN", OFF):
            self.assertEqual(cold_open.outline_rule(cold_open.mode_for(TYPE_A)), "")
            plain = cold_open.ensure(outline(), "off")
            self.assertNotIn("cold_open", [scene["section_type"] for scene in plain["scenes"]])
            self.assertNotIn("cold_open", plain)
            self.assertEqual(cold_open.findings(sections()[1:], plain, narrative_type=TYPE_A), [])
            self.assertEqual(cold_open.screenplay_rule(plain), "")


class OutlineTests(unittest.TestCase):
    def test_from_the_brief_first_with_its_own_budget_and_movement(self):
        shuffled = outline()
        shuffled["scenes"].append(shuffled["scenes"].pop(0))
        shuffled["scenes"][-1]["target_words"] = 400
        result = cold_open.ensure(shuffled, "flash_forward", beat="night, the editing desk")
        teaser = result["scenes"][0]
        self.assertEqual((teaser["section_type"], teaser["status"], teaser["pace"]), ("cold_open", "from_brief", "brief"))
        self.assertEqual(teaser["target_words"], 100)
        self.assertEqual(teaser["arc_movements"][0], "cold_open")
        self.assertEqual([scene["order"] for scene in result["scenes"]], list(range(1, len(result["scenes"]) + 1)))
        self.assertEqual(result["cold_open"]["source_scene_id"], "s07")
        with patch.object(narrative, "outline_scene_issues", return_value={}):    # the mock is abridged
            narrative.validate_outline({**result, "narrative_contract_version": narrative.NARRATIVE_CONTRACT_VERSION}, {"characters": []})

    def test_proposed_without_a_beat_and_pointed_at_a_reversal(self):
        plan = outline()
        plan["scenes"][0]["source_scene_id"] = "s03"                     # not a reversal or climax
        result = cold_open.ensure(plan, "flash_forward")
        self.assertEqual(result["scenes"][0]["status"], "proposed")
        self.assertEqual(result["scenes"][0]["source_scene_id"], "s07")

    def test_a_cold_open_that_is_not_first_is_refused(self):
        plan = outline()
        plan["scenes"].insert(3, plan["scenes"].pop(0))
        for order, scene in enumerate(plan["scenes"], 1):
            scene["order"] = order
        with self.assertRaises(narrative.NarrativeValidationError), patch.object(narrative, "outline_scene_issues", return_value={}):
            narrative.validate_outline({**plan, "narrative_contract_version": narrative.NARRATIVE_CONTRACT_VERSION}, {"characters": []})

    def test_omen_has_no_source_and_a_short_budget(self):
        result = cold_open.ensure(outline(), "omen")
        self.assertEqual(result["scenes"][0]["source_scene_id"], "")
        self.assertLessEqual(result["scenes"][0]["target_words"], cold_open.WORDS["omen"][1])
        self.assertIn("no jump in time", cold_open.outline_rule("omen"))

    def test_the_teaser_does_not_take_words_from_the_story(self):
        result = cold_open.ensure(outline(), "flash_forward", beat="x")
        story = sum(scene["target_words"] for scene in result["scenes"][1:])
        balanced = narrative.rebalance_scene_word_targets(result, story + 70)
        self.assertEqual(sum(scene["target_words"] for scene in balanced["scenes"][1:]), story)
        self.assertEqual(balanced["scenes"][0]["target_words"], 70)


class CheckTests(unittest.TestCase):
    def check(self, found_sections, plan=None):
        with patch.object(config, "COLD_OPEN", ON):
            return cold_open.findings(found_sections, plan or cold_open.ensure(outline(), "flash_forward", beat="x"), narrative_type=TYPE_A, beat="x")

    def test_the_recorded_teaser_passes(self):
        self.assertEqual(self.check(sections()), [])

    def test_length(self):
        long = sections()
        long[0]["narration"] = " ".join(["Hannah waits by the screen."] * 25)
        self.assertIn(("major", "cold_open_length"), kinds(self.check(long)))
        short = sections()
        short[0]["narration"] = "Hannah stops. Her hand rises to her mouth."
        self.assertIn(("minor", "cold_open_length"), kinds(self.check(short)))

    def test_more_than_one_shared_sentence_with_the_source(self):
        copied = sections()
        copied[0]["narration"] += " Leo, six years old, is telling a long and serious dream about a whale that lived in the bathtub."
        self.assertIn(("major", "cold_open_overlap"), kinds(self.check(copied)))

    def test_a_reveal_of_the_withheld_content(self):
        spoiled = sections()
        spoiled[0]["narration"] += " On the screen, Leo tells her his dream, and Hannah never looks up."
        self.assertIn(("major", "cold_open_reveal"), kinds(self.check(spoiled)))

    def test_the_return_line(self):
        stock = sections()
        stock[1]["narration"] = "Let's go back to the beginning. " + stock[1]["narration"].split(". ", 1)[1]
        self.assertIn(("major", "cold_open_return"), kinds(self.check(stock)))
        vague = sections()
        vague[1]["narration"] = "The ring light hummed above the kitchen table. Leo looked at the candles, not the lens."
        self.assertIn(("major", "cold_open_return"), kinds(self.check(vague)))

    def test_present_when_the_mode_requires_one(self):
        plan = cold_open.ensure(outline(), "flash_forward", beat="x")
        self.assertIn(("major", "cold_open_missing"), kinds(self.check(sections()[1:], plan)))
        legacy = {"scenes": outline()["scenes"][1:]}                     # an outline from before 3.9.7
        self.assertEqual(self.check(sections()[1:], legacy), [])

    def test_omen_mode_produces_no_scene_jump(self):
        plan = cold_open.ensure(outline(), "omen")
        omen = sections()
        omen[0]["narration"] = "This is the story of how one birthday was filmed nine thousand times, and missed once."
        omen[1]["narration"] = "The ring light hummed above the kitchen table. Leo looked at the candles, not the lens."
        with patch.object(config, "COLD_OPEN", ON):
            found = cold_open.findings(omen, plan, narrative_type=TYPE_C)
        self.assertNotIn("cold_open_return", [item["issue_type"] for item in found])
        self.assertNotIn("cold_open_overlap", [item["issue_type"] for item in found])
        self.assertEqual(found, [])

    def test_the_deterministic_review_reports_them_and_counts_the_teaser_apart(self):
        script = {"title": "Hannah Finds the One Clip", "narrative_type": TYPE_A, "planned_words_per_minute": 145, "sections": sections()}
        script["sections"][0]["narration"] += " On the screen, Leo tells her his dream, and Hannah never looks up."
        with patch.object(config, "COLD_OPEN", ON):
            review = narrative.deterministic_review(script, 1.0, outline=cold_open.ensure(outline(), "flash_forward", beat="x"))
        self.assertIn("cold_open_reveal", [item["issue_type"] for item in review["major_issues"]])
        story_words = sum(len(item["narration"].split()) for item in script["sections"][1:])
        self.assertEqual(review["word_count"], story_words)


class ChronologyTests(unittest.TestCase):
    def test_the_cold_open_and_its_echo_are_exempt(self):
        plan = cold_open.ensure(outline(), "flash_forward", beat="x")
        plan["scenes"][7]["summary"] = "She watches the accidental clip from the kitchen counter."
        teaser = [{"id": 0, "outline_scene_id": "s00", "section_type": "cold_open",
                   "narration": "Then she watches the accidental clip from the kitchen counter and covers her mouth."}]
        self.assertEqual(prose_lint.chronology_findings(teaser, plan), [])
        early = [{"id": 2, "outline_scene_id": "s02", "section_type": "setup",
                  "narration": "Then she watches the accidental clip from the kitchen counter."}]
        planned = prose_lint.chronology_findings(early, plan)
        echo = [{"id": 7, "outline_scene_id": "s07", "section_type": "reversal",
                 "narration": "Then she watches the accidental clip from the kitchen counter."}]
        self.assertEqual(prose_lint.chronology_findings(echo, plan), [])
        self.assertTrue(all(item["scene_ids"] == [2] for item in planned))


class PackagingTests(unittest.TestCase):
    def test_chapter_zero_is_neutral(self):
        from video import description
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "script.json"), {"sections": sections()})
            atomic_write_json(Path(temp, "scene_timeline.json"), {"total_duration": 200, "scenes": [
                {"start": 0, "end": 30, "duration": 30}, {"start": 30, "end": 110, "duration": 80}, {"start": 110, "end": 200, "duration": 90}]})
            text = description.build(temp, {"sections": sections(), "title": "T"}, {"logline": "A mother films everything."},
                                     {"hook": "A mother who filmed every day of her son's life.", "chapter_titles": ["The Clip Of Her Face", "Birthday", "Kitchen"],
                                      "comment_question": "Why?", "pinned_comment": "Why?"})
        self.assertIn("0:00 Prologue", text)
        self.assertNotIn("The Clip Of Her Face", text)

    def test_the_description_hook_never_repeats_the_teaser(self):
        from video import description
        teaser_line = "In eleven years of video, there was only one where she could see her own face."
        with tempfile.TemporaryDirectory() as temp:
            text = description.build(temp, {"sections": sections(), "title": "T"}, {"logline": "A mother films everything for millions."},
                                     {"hook": teaser_line, "chapter_titles": ["Prologue", "Birthday", "Kitchen"],
                                      "comment_question": "Why?", "pinned_comment": "Why?"})
        self.assertNotIn(teaser_line, text)
        self.assertIn("A mother films everything for millions.", text)

    def test_shorts_may_open_on_the_teaser_but_never_pass_the_twist(self):
        from video import shorts
        script = {"sections": [{"narration": "a", "section_type": "cold_open"}, {"narration": "b", "section_type": "opening"},
                               {"narration": "c", "section_type": "reversal"}]}
        timeline = {"scenes": [{"start": 0, "duration": 25}, {"start": 25, "duration": 60}, {"start": 85, "duration": 40}]}
        sentence_list = [{"index": n, "start": n * 5.0, "end": n * 5.0 + 4.8, "text": f"s{n}"} for n in range(24)]
        self.assertEqual(shorts.teaser_end(script, sentence_list, timeline), 4)
        self.assertEqual(shorts.twist_start(script, timeline, 125), 85.0)          # the teaser is not the twist
        self.assertIn("COLD OPEN", shorts.contract(3, 17, 4))
        self.assertNotIn("COLD OPEN", shorts.contract(3, 17, None))
        low, high = shorts.limits()
        last = next(n for n in range(24) if sentence_list[n]["end"] >= low)
        ok = shorts.validate([{"start_sentence": 0, "end_sentence": last, "title": "She stops on one clip", "hook_text": "One clip",
                               "end_text": "Full story", "description": "d", "why": "w"}], sentence_list, twist=85.0, count=(1, 5))
        self.assertEqual(ok[0]["start_sentence"], 0)
        with self.assertRaises(Exception):
            shorts.validate([{"start_sentence": 0, "end_sentence": 18, "title": "x", "hook_text": "x", "end_text": "x",
                              "description": "d", "why": "w"}], sentence_list, twist=85.0, count=(1, 5))

    def test_the_teaser_is_followed_by_its_own_pause_and_a_music_sting(self):
        from video import music
        tone = np.full(int(.5 * assembly.RATE), .2, dtype=np.float32)
        scenes = [{"id": 0, "section_type": "cold_open", "text": "One.", "samples": tone, "words": [{"text": "One.", "start": .05, "end": .45, "sentence_end": True}]},
                  {"id": 1, "section_type": "opening", "text": "Two.", "samples": tone, "words": [{"text": "Two.", "start": .05, "end": .45, "sentence_end": True}]}]
        built = assembly.assemble(scenes)
        self.assertEqual([(pause["kind"], pause["seconds"]) for pause in built["pauses"]], [("cold_open", 1.5)])
        graph, _ = music.music_filter(60, 120, .1, swells=[1.2])
        self.assertIn("volume=eval=frame", graph)
        self.assertIn("[lifted]", graph)
        plain, _ = music.music_filter(60, 120, .1)
        self.assertNotIn("[lifted]", plain)
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "narration_words.json"), {"pauses": built["pauses"]})
            self.assertEqual(music.stings(temp), [round(built["pauses"][0]["at"] + .75, 3)])

    def test_the_teaser_looks_slower_and_darker(self):
        from video import creator
        treated = creator.cold_open_treatment([{"zoom": (1.0, 1.2), "motion": {"kind": "parallax", "amount": .04}}])[0]
        self.assertAlmostEqual(treated["zoom"][1] - treated["zoom"][0], 0.1)
        self.assertAlmostEqual(treated["motion"]["amount"], .02)
        self.assertLess(treated["darken"], 1)

    def test_the_teaser_shots_never_show_the_withheld(self):
        from agents import shot_list
        plan = cold_open.ensure(outline(), "flash_forward", beat="x")
        note = shot_list._cold_open_note(plan["scenes"][0])
        self.assertIn("NEVER SHOW: what the kitchen clip shows", note)
        self.assertEqual(shot_list._cold_open_note(plan["scenes"][1]), "")


class VariationTests(unittest.TestCase):
    def test_three_episodes_with_the_same_kind_of_moment_warn(self):
        from studio import variation
        current = variation.profile({"narrative_type": TYPE_A}, outline=cold_open.ensure(outline(), "flash_forward", beat="x"))
        self.assertEqual(current["cold_open"], {"mode": "flash_forward", "moment": "discovery"})
        past = [{"title": "One", "cold_open": {"mode": "flash_forward", "moment": "discovery"}},
                {"title": "Two", "cold_open": {"mode": "flash_forward", "moment": "discovery"}}]
        self.assertIn("cold_open", [item["kind"] for item in variation.checks(current, past)])
        past[0]["cold_open"]["moment"] = "line"
        self.assertNotIn("cold_open", [item["kind"] for item in variation.checks(current, past)])


class SettingsTests(unittest.TestCase):
    def test_settings_show_and_save_the_mode_per_type(self):
        import gui
        with patch.object(config, "COLD_OPEN", dict(ON)), patch.object(gui, "_write_env", create=True), \
                patch.object(gui, "_update_env_file", create=True):
            client = gui.app.test_client()
            self.assertEqual(client.get("/api/settings").get_json()["cold_open"], ON)
            with patch("gui.Path.write_text"), patch("gui.Path.replace"):
                client.post("/api/save-settings", json={"cold_open": {"A": "off", "C": "flash_forward", "B": "nonsense"}})
            self.assertEqual(config.COLD_OPEN, {"A": "off", "B": "flash_forward", "C": "flash_forward"})


if __name__ == "__main__":
    unittest.main()
