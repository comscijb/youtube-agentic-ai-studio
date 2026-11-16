"""Phase 4.3-A/B: Short formats, the script contract, the deterministic checks and the swipe gate (Gemini mocked)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import short_formats as sf

SECTIONS = [
    {"id": 1, "section_type": "cold_open", "narration": "The whole square went silent. Every face turned to Tomas. "
                                                         "They had all heard it. What had he thought?"},
    {"id": 2, "section_type": "opening", "narration": "Three weeks earlier, the baker Tomas woke to a town that could hear minds. "
                                                      "Every thought rang out like a bell."},
    {"id": 3, "section_type": "development", "narration": "He learned to think in prayers. He counted loaves to stay quiet. "
                                                          "The mayor smiled too long at him."},
    {"id": 4, "section_type": "reversal", "narration": "Then the mayor's own thought rang out: he had poisoned the well himself."},
    {"id": 5, "section_type": "resolution", "narration": "Tomas left the town at dawn and never thought of bread again."},
]
SCRIPT = {"title": "Everyone Heard What He Thought", "narrative_type": "dark_human_parable", "sections": SECTIONS}
BRIEF = {"psychological_theme": "shame", "logline": "A baker in a town where every thought is heard.",
         "topic_words": ["mind reading", "thoughts"]}
OUTLINE = {"scenes": [{"scene_id": 1, "section_type": "cold_open", "withheld": "the mayor poisoned the well"}]}
WPM = 150.0


def script_for(fmt, **extra):
    base = {
        "hook": {"hook_line": "The whole square heard his thought.", "on_screen_hook": "They heard everything",
                 "script": "The whole square heard his thought. Every face turned to Tomas. Nobody breathed. The baker "
                           "stood with flour on his hands. He had counted loaves for weeks to stay quiet. He had thought "
                           "in prayers. And now one thought had slipped out. They had all heard it. What did he think?",
                 "cta_text": "The full story is on the channel.", "title": "The Baker Whose Thoughts the Whole Town Heard"},
        "dilemma": {"hook_line": "Everyone in town can hear your mind.", "on_screen_hook": "Silence or truth?",
                    "script": "Everyone in town can hear your mind. One stray thought, and they all know. Tomas the baker "
                              "chose to think only in prayers. He counted loaves to stay quiet. The mayor smiled too long "
                              "at him. Would you hide your mind, or let them hear it?",
                    "cta_text": "Tell me in the comments.", "question": "What would you do?",
                    "title": "A Village Where Every Thought Rings Out"},
        "line": {"hook_line": "Every thought rang out like a bell.", "on_screen_hook": "Like a bell",
                 "script": "Every thought rang out like a bell. So the baker learned to think in prayers. He counted "
                           "loaves to stay quiet. He smiled at the mayor and kept his hands busy with the dough. "
                           "He never let one thought slip. And still, in that town, every thought rang out like a bell.",
                 "cta_text": "Full story on the channel", "title": "The Baker Who Learned to Think in Prayers"},
        "countdown": {"hook_line": "Three weeks before the square went silent.", "on_screen_hook": "Three weeks left",
                      "script": "Three weeks before the square went silent. Tomas woke to a town that could hear minds. "
                                "Every thought rang out like a bell. He learned to think in prayers. He counted loaves. "
                                "The mayor smiled too long at him. Every day the smile lasted longer. Every day the "
                                "town listened harder. And on the last morning, the mayor walked into the bakery and "
                                "closed the door behind him.",
                      "cta_text": "The full story is on the channel.", "title": "The Mayor Who Smiled Too Long at the Baker"},
        "pov": {"hook_line": "Your town can hear every thought you have.", "on_screen_hook": "POV: they hear you",
                "script": "Your town can hear every thought you have. You wake and the street is listening. You knead "
                          "the dough and think in prayers. You count loaves to stay quiet. The mayor smiles at you too "
                          "long. You feel a thought rising. You cannot stop it.",
                "cta_text": "The full story is on the channel.", "title": "A Baker in a Town of Mind Reading"},
    }[fmt]
    return {"format": fmt, "description_line": "A parable about a baker whose thoughts are heard.",
            "source_scene_ids": ["1", "2", "3"], "evidence_quotes": ["Every thought rang out like a bell."],
            "question": "", **base, **extra}


def verdicts_ok(items):
    return {item["id"]: {"blocks": [{"swipe": False, "reason": "pulled in"} for _ in sf.blocks(item, WPM)],
                         "loop_natural": True if item["format"] == "line" else None} for item in items}


class FormatTests(unittest.TestCase):
    def test_five_formats_and_ranges(self):
        self.assertEqual(sf.ORDER, ("hook", "dilemma", "line", "countdown", "pov"))
        self.assertEqual(sf.FORMATS["line"]["seconds"], (12, 25))
        self.assertEqual(sf.FORMATS["countdown"]["seconds"], (30, 45))
        self.assertFalse(sf.FORMATS["line"]["spoken_cta"])

    def test_allocate_follows_weights_and_order(self):
        self.assertEqual(sf.allocate(4, sf.DEFAULT_WEIGHTS), ["hook", "dilemma", "line", "countdown"])
        self.assertEqual(sf.allocate(6, {"hook": 2, "line": 1}), ["hook", "hook", "hook", "hook", "line", "line"])
        self.assertEqual(sf.allocate(3, {"hook": 1, "pov": 3, "line": 0}), ["hook", "pov", "pov"])
        self.assertEqual(len(sf.allocate(10, sf.DEFAULT_WEIGHTS)), sf.MAX_PER_EPISODE)

    def test_per_episode_default_and_cap(self):
        with patch.object(config, "SHORTS_PER_EPISODE", 9):
            self.assertEqual(sf.per_episode(), 6)
        self.assertEqual(sf.per_episode(), 4)

    def test_contract_names_every_rule(self):
        text = sf.contract(["hook", "dilemma"], "realistic_psychological_fiction")
        for phrase in ("evidence_quotes", "NEVER reveal", "hook_line", "Who was right?", "Part 2", "#Shorts"):
            self.assertIn(phrase, text)


class ValidateTests(unittest.TestCase):
    def test_evidence_quotes_must_exist(self):
        good = sf.validate({"shorts": [script_for("hook")]}, ["hook"], SECTIONS)
        self.assertEqual(good[0]["format"], "hook")
        with self.assertRaises(NarrativeValidationError):
            sf.validate({"shorts": [script_for("hook", evidence_quotes=["The baker flew to the moon."])]}, ["hook"], SECTIONS)

    def test_scene_ids_and_format_order(self):
        with self.assertRaises(NarrativeValidationError):
            sf.validate({"shorts": [script_for("hook", source_scene_ids=["99"])]}, ["hook"], SECTIONS)
        with self.assertRaises(NarrativeValidationError):
            sf.validate({"shorts": [script_for("line")]}, ["hook"], SECTIONS)

    def test_dilemma_needs_a_question(self):
        with self.assertRaises(NarrativeValidationError):
            sf.validate({"shorts": [script_for("dilemma", question="Think about it.")]}, ["dilemma"], SECTIONS)


class CheckTests(unittest.TestCase):
    guard = sf.story_guard(SCRIPT, OUTLINE)

    def run_checks(self, item):
        return {check["code"]: check for check in sf.checks(item, guard=self.guard, brief=BRIEF,
                                                            narrative_type="dark_human_parable", wpm=WPM)}

    def test_every_good_script_passes(self):
        for fmt in sf.ORDER:
            found = self.run_checks(script_for(fmt))
            self.assertEqual([code for code, check in found.items() if not check["ok"]], [], fmt)

    def test_hook_first_rejects_scene_setting(self):
        item = script_for("hook", script="In a small town, a baker lived quietly. " + script_for("hook")["script"],
                          hook_line="In a small town, a baker lived quietly.")
        self.assertFalse(self.run_checks(item)["hook_first"]["ok"])
        item = script_for("hook", script="Once there was a baker. " + script_for("hook")["script"])
        self.assertFalse(self.run_checks(item)["hook_first"]["ok"])

    def test_long_sentences_fail(self):
        long = " ".join(["word"] * 20) + ". " + script_for("hook")["script"]
        self.assertFalse(self.run_checks(script_for("hook", script=long))["sentences"]["ok"])

    def test_duration_range_per_format(self):
        short = script_for("countdown", script=script_for("line")["script"].replace("Every thought", "Three weeks before the square went silent. Every thought", 1))
        self.assertFalse(self.run_checks(short)["duration"]["ok"])

    def test_cta_present_and_no_part_two(self):
        self.assertFalse(self.run_checks(script_for("hook", cta_text=""))["cta"]["ok"])
        self.assertFalse(self.run_checks(script_for("hook", cta_text="Part 2 tomorrow"))["cta"]["ok"])

    def test_twist_guard(self):
        spoiler = script_for("countdown", script=script_for("countdown")["script"] + " The mayor poisoned the well himself.")
        self.assertFalse(self.run_checks(spoiler)["no_twist"]["ok"])
        reversal = script_for("countdown", source_scene_ids=["3", "4"])
        self.assertFalse(self.run_checks(reversal)["no_twist"]["ok"])
        self.assertTrue(self.run_checks(script_for("hook", source_scene_ids=["1"]))["no_twist"]["ok"])   # cold open allowed

    def test_cta_inside_the_script_is_spoken_once(self):
        item = script_for("hook", script=script_for("hook")["script"] + " The full story is on the channel.")
        clean = sf.validate({"shorts": [item]}, ["hook"], SECTIONS)[0]
        self.assertTrue(clean["script"].endswith("What did he think?"))
        self.assertEqual(sf.spoken_text(clean).lower().count("full story"), 1)

    def test_title_names_its_own_type_only(self):
        found = self.run_checks(script_for("hook", title="A Thought Experiment About a Baker"))
        self.assertFalse(found["title_type"]["ok"])
        self.assertTrue(self.run_checks(script_for("hook", title="A Parable About a Baker Who Thought Too Loud"))["title_type"]["ok"])

    def test_episode_concepts(self):
        brief = sf.episode_brief(tempfile.gettempdir(), SCRIPT, {"psychological_theme": "shame"})
        self.assertEqual(brief["narrative_type"], "dark_human_parable")
        self.assertIn("parable", brief["topic_words"])
        self.assertIn("baker", brief["topic_words"])               # found in the narration
        self.assertIn("parable", sf.contract(["hook"], "dark_human_parable", brief["topic_words"]))

    def test_title_rules(self):
        self.assertFalse(self.run_checks(script_for("hook", title="Shame: A Story About Guilt"))["title"]["ok"])
        self.assertFalse(self.run_checks(script_for("hook", title="They Heard Everything, Said The Town"))["title_vs_hook"]["ok"])
        self.assertTrue(sf.short_title("The Baker Who Thought Too Loud").endswith(" #Shorts"))

    def test_format_endings(self):
        self.assertFalse(self.run_checks(script_for("hook", script=script_for("hook")["script"].replace("What did he think?", "He was ruined.")))["ending"]["ok"])
        self.assertFalse(self.run_checks(script_for("pov", on_screen_hook="They hear you"))["pov"]["ok"])
        self.assertFalse(self.run_checks(script_for("dilemma", cta_text="Full story on the channel.", question="Silence?"))["question"]["ok"])

    def test_estimate_uses_shorts_rate_and_cta(self):
        line, hook = script_for("line"), script_for("hook")
        self.assertLess(sf.estimate_seconds(line, WPM), sf.estimate_seconds(hook, WPM))
        self.assertIn("full story", sf.spoken_text(hook).lower())
        self.assertNotIn("full story", sf.spoken_text(line).lower())
        with patch("agents.attention.wpm", return_value=150.0), patch.object(config, "SHORTS_TTS_SPEED", 0.95), \
                patch.object(config, "TTS_LOCAL_SPEED", 0.88):
            self.assertAlmostEqual(sf.shorts_wpm(), 150 * 0.95 / 0.88, places=3)


class SwipeGateTests(unittest.TestCase):
    item = {"id": "short_01", "format": "hook"}

    def test_first_block_swipe_fails(self):
        verdict = {"blocks": [{"swipe": True, "reason": "slow start"}, {"swipe": False, "reason": ""}]}
        self.assertTrue(any("first 3 s" in reason for reason in sf.gate(self.item, verdict)))

    def test_at_most_one_swipe_before_the_end(self):
        one = {"blocks": [{"swipe": False, "reason": ""}, {"swipe": True, "reason": "a"}, {"swipe": False, "reason": ""},
                          {"swipe": True, "reason": "end"}]}
        self.assertEqual(sf.gate(self.item, one), [])
        two = {"blocks": [{"swipe": False, "reason": ""}, {"swipe": True, "reason": "a"}, {"swipe": True, "reason": "b"},
                          {"swipe": False, "reason": ""}]}
        self.assertTrue(sf.gate(self.item, two))

    def test_line_needs_a_natural_loop(self):
        verdict = {"blocks": [{"swipe": False, "reason": ""}], "loop_natural": False}
        self.assertIn("the loop does not feel natural", sf.gate({"id": "x", "format": "line"}, verdict))

    def test_blocks_are_three_seconds(self):
        item = script_for("hook")
        per_block = round(WPM / 60 * 3)
        self.assertEqual(len(sf.blocks(item, WPM)[0].split()), per_block)

    def test_validate_sim_counts_blocks(self):
        item = {**script_for("line"), "id": "short_03"}
        block_map = {"short_03": sf.blocks(item, WPM)}
        with self.assertRaises(NarrativeValidationError):
            sf.validate_sim({"verdicts": [{"id": "short_03", "blocks": [{"swipe": False}]}]}, [item], block_map)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        atomic_write_json(self.folder / "script.json", SCRIPT)
        atomic_write_json(self.folder / "story_brief.json", BRIEF)
        atomic_write_json(self.folder / "story_outline.json", OUTLINE)
        self.wpm = patch.object(sf, "shorts_wpm", return_value=WPM)
        self.wpm.start()

    def tearDown(self):
        self.wpm.stop()
        self.temp.cleanup()

    def test_one_call_then_ready(self):
        calls = []

        def generator(prompt, *args, **kwargs):
            calls.append(kwargs.get("stage"))
            return json.dumps({"shorts": [script_for(fmt) for fmt in ["hook", "dilemma", "line", "countdown"]]})
        plan = sf.build(self.folder, generator=generator, simulator=verdicts_ok, formats=["hook", "dilemma", "line", "countdown"])
        self.assertEqual(calls, ["shorts_scripts"])
        self.assertEqual(plan["mode"], "formats")
        self.assertEqual([item["status"] for item in plan["segments"]], ["ready"] * 4)
        self.assertEqual(plan["segments"][0]["id"], "short_01")
        self.assertTrue(plan["segments"][0]["title"].endswith("#Shorts"))
        self.assertEqual(load_json(self.folder / "shorts" / "shorts_plan.json")["segments"][2]["format"], "line")
        again = sf.build(self.folder, generator=generator, simulator=verdicts_ok, formats=["hook", "dilemma", "line", "countdown"])
        self.assertEqual(len(calls), 1)                    # cached on the narration
        self.assertEqual(again["source_hash"], plan["source_hash"])

    def test_one_rewrite_then_review(self):
        stages = []
        bad_hook = script_for("hook", script="In a small town, there was a baker. " + script_for("hook")["script"])

        def generator(prompt, *args, **kwargs):
            stages.append(kwargs.get("stage"))
            if kwargs.get("stage") == "shorts_scripts":
                return json.dumps({"shorts": [bad_hook, script_for("pov")]})
            return json.dumps({"shorts": [bad_hook]})       # the rewrite still opens on the stage
        plan = sf.build(self.folder, generator=generator, simulator=verdicts_ok, formats=["hook", "pov"])
        self.assertEqual(stages, ["shorts_scripts", "shorts_rewrite"])
        hook, pov = plan["segments"]
        self.assertEqual(hook["status"], "review")
        self.assertTrue(hook["rewritten"])
        self.assertTrue(any("hook" in reason for reason in hook["reasons"]))
        self.assertEqual(len(hook["versions"]), 1)
        self.assertEqual(pov["status"], "ready")

    def test_rewrite_fixes_a_swipe(self):
        def generator(prompt, *args, **kwargs):
            return json.dumps({"shorts": [script_for("hook")]})
        seen = []

        def simulator(items):
            seen.append([item.get("rewritten", False) for item in items])
            result = verdicts_ok(items)
            if not items[0].get("rewritten"):
                result[items[0]["id"]]["blocks"][0] = {"swipe": True, "reason": "nothing happens"}
            return result
        plan = sf.build(self.folder, generator=generator, simulator=simulator, formats=["hook"])
        self.assertEqual(seen, [[False], [True]])
        self.assertEqual(plan["segments"][0]["status"], "ready")

    def test_edit_rechecks_and_marks_stale(self):
        def generator(prompt, *args, **kwargs):
            return json.dumps({"shorts": [script_for("hook"), script_for("line")]})
        sf.build(self.folder, generator=generator, simulator=verdicts_ok, formats=["hook", "line"])
        plan = sf.save(self.folder, [{"id": "short_01", "script": "Once upon a time. " + script_for("hook")["script"]},
                                     {"id": "short_02", "include": False}])
        first, second = plan["segments"]
        self.assertEqual(first["status"], "review")
        self.assertTrue(first["swipe"]["stale"])
        self.assertFalse(second["include"])
        self.assertEqual(second["status"], "ready")
        plan = sf.resimulate(self.folder, ["short_01"], simulator=verdicts_ok)
        self.assertNotIn("the text changed: simulate again", plan["segments"][0]["reasons"])

    def test_regenerate_keeps_the_old_version(self):
        def generator(prompt, *args, **kwargs):
            if kwargs.get("stage") == "shorts_version":
                return json.dumps({"shorts": [script_for("hook", hook_line="Nobody in the square breathed.",
                                                         script="Nobody in the square breathed. " + script_for("hook")["script"].split(". ", 1)[1])]})
            return json.dumps({"shorts": [script_for("hook")]})
        sf.build(self.folder, generator=generator, simulator=verdicts_ok, formats=["hook"])
        fresh = sf.regenerate(self.folder, "short_01", generator=generator, simulator=verdicts_ok)
        self.assertEqual(fresh["hook_line"], "Nobody in the square breathed.")
        self.assertEqual(len(fresh["versions"]), 1)
        with self.assertRaises(LookupError):
            sf.regenerate(self.folder, "short_09", generator=generator, simulator=verdicts_ok)


if __name__ == "__main__":
    unittest.main()
