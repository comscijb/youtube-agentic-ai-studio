"""Fixes found by the first project made from a brief: stable brief hash, confirmed Story Brief
regeneration, the brief's duration, robust expansion, quoted dialogue, ending fidelity, empty projects."""
import io
import json
import os
import tempfile
import unicodedata
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import premise_brief, scriptwriter
from agents.narrative import atomic_write_json, stable_hash
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_narrative import bible, outline, screenplay_with_counts, story_brief

ENDING_BRIEF = """# Protagonist
Walter Hale, 68, retired piano tuner.

# Beats
- Hook: Walter tells his phone: "That's the best conversation I've had in two years."
- Ending: at the grave Walter calls June. Back home his phone glows gold: "I'm here whenever you need me, Walter." He turns it face down and plays one wrong note on the piano. Open question: who will he call tomorrow?
"""


class BriefHashTests(unittest.TestCase):
    def test_crlf_trailing_spaces_and_nfd_give_the_same_hash(self):
        plain = premise_brief.parse(premise_brief.TEMPLATE)
        windows = premise_brief.TEMPLATE.replace("\n", "  \r\n")
        decomposed = unicodedata.normalize("NFD", premise_brief.TEMPLATE.replace("Leeds", "Léeds"))
        composed = premise_brief.parse(premise_brief.TEMPLATE.replace("Leeds", "Léeds"))
        self.assertEqual(premise_brief.brief_hash(premise_brief.parse(windows)), premise_brief.brief_hash(plain))
        self.assertEqual(premise_brief.brief_hash(premise_brief.parse(decomposed)), premise_brief.brief_hash(composed))
        self.assertEqual(premise_brief.brief_hash({**plain, "hash": "anything"}), premise_brief.brief_hash(plain))

    def test_saving_the_same_brief_rewrites_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            first = premise_brief.save(temp, premise_brief.parse(premise_brief.TEMPLATE))
            markdown, record = Path(temp, premise_brief.MARKDOWN_FILE), Path(temp, premise_brief.JSON_FILE)
            os.utime(markdown, (1000000, 1000000))
            os.utime(record, (1000000, 1000000))
            again = premise_brief.save(temp, premise_brief.parse(premise_brief.TEMPLATE.replace("\n", " \r\n")))
            self.assertEqual((markdown.stat().st_mtime, record.stat().st_mtime), (1000000, 1000000))
            self.assertNotIn(b"\r", markdown.read_bytes())                        # LF on disk
            premise_brief.save(temp, premise_brief.parse(premise_brief.TEMPLATE.replace("Leeds", "York")))
            self.assertNotEqual(markdown.stat().st_mtime, 1000000)
        self.assertEqual(again["hash"], first["hash"])

    def test_story_brief_matches_its_brief_including_phase_3_7_hashes(self):
        from studio import projects
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp, "p")
            folder.mkdir()
            atomic_write_json(folder / "project.json", {"slug": "p", "stages": {}})
            record = premise_brief.save(folder, premise_brief.parse(premise_brief.TEMPLATE))
            legacy = stable_hash({key: value for key, value in record.items() if key != "raw"})   # had "hash" in it
            for requested in (legacy, premise_brief.brief_hash(record)):
                atomic_write_json(folder / "story_brief.json", dict(story_brief(), requested_brief_hash=requested))
                status = {item["stage"]: item["status"] for item in projects.stage_status(folder)}
                self.assertEqual(status["story_brief"], "done")


class DurationTests(unittest.TestCase):
    def test_the_brief_wins(self):
        brief = premise_brief.parse(premise_brief.TEMPLATE)
        self.assertEqual(premise_brief.target_minutes(brief, 15), 12.0)
        self.assertEqual(premise_brief.target_minutes({}, 15), 15.0)
        self.assertEqual(premise_brief.target_minutes({"target_minutes": 40.0}, 15), 15.0)   # outside 12-20

    def test_research_uses_the_briefs_duration(self):
        from agents import researcher
        with patch.object(researcher, "_cached_brief", return_value={"cached": True}) as cached, \
                redirect_stdout(io.StringIO()):
            researcher.research_topic("channel", target_duration_minutes=15, reuse_cached=True,
                                      premise_brief=premise_brief.parse(premise_brief.TEMPLATE))
        request_hashes = cached.call_args.args[1]
        self.assertEqual(len(request_hashes), 2)                                  # current + Phase 3.7 key

    def test_gui_parse_reports_the_duration(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            parsed = gui.app.test_client().post("/api/premise-brief/parse",
                                                json={"text": premise_brief.TEMPLATE}).get_json()
        self.assertEqual(parsed["target_minutes"], 12.0)


def expansion_script(counts, targets):
    script = screenplay_with_counts(counts, targets)
    plan = outline()
    for scene, planned in zip(script["sections"], plan["scenes"]):
        scene["outline_scene_id"] = planned["scene_id"]
        scene["section_type"] = planned["section_type"]
        scene["narrative_function"] = planned["narrative_function"]
        scene["target_words"] = planned["target_words"]
    return script, plan


def answer(*word_counts, key="scene_id"):
    """Gemini's reply: scenes with the given sizes, numbered with its own (wrong) key."""
    return json.dumps({"scenes": [{key: 99, "narration": " ".join(f"new{n}" for n in range(count))}
                                  for count in word_counts]})


class ExpansionTests(unittest.TestCase):
    def expand(self, replies, counts=(40, 80, 80), batch=1, calls=1):
        script, plan = expansion_script(list(counts), [80, 80, 80])          # the outline's targets: 80 words
        with patch.object(scriptwriter, "generate", side_effect=replies) as generated, \
                patch.object(config, "NARRATIVE_EXPANSION_BATCH_SIZE", batch), \
                patch.object(config, "NARRATIVE_EXPANSION_MAX_CALLS", calls):
            revised, attempts = scriptwriter._expand_underlength_scenes(script, story_brief(), bible(), plan)
        return script, revised, attempts, generated.call_count

    def test_code_owns_ids_and_order(self):
        script, revised, attempts, _ = self.expand([answer(76)])
        self.assertEqual([scene["id"] for scene in revised["sections"]], [1, 2, 3])
        self.assertEqual(len(revised["sections"][0]["narration"].split()), 76)
        self.assertEqual(revised["sections"][0]["outline_scene_id"], script["sections"][0]["outline_scene_id"])
        self.assertTrue(attempts[0]["success"])

    def test_within_two_points_of_the_band_is_accepted(self):
        _, revised, attempts, _ = self.expand([answer(67)])                  # 83.75%
        self.assertEqual(attempts[0]["accepted"], [1])
        self.assertTrue(attempts[0]["success"])

    def test_an_improvement_outside_the_band_is_kept(self):
        _, revised, attempts, _ = self.expand([answer(60)])
        self.assertEqual(len(revised["sections"][0]["narration"].split()), 60)  # better than 40: not discarded
        self.assertEqual(attempts[0]["kept_best"], [1])
        self.assertFalse(attempts[0]["success"])

    def test_one_attempt_per_scene_and_never_a_loop(self):
        script, revised, attempts, calls = self.expand([answer(76), answer(76)], counts=(40, 40, 80), batch=3)
        self.assertEqual(calls, 2)                       # the batch + its one repair (wrong count); no more
        self.assertIn("exactly 2 scene(s)", attempts[0]["error"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual([len(scene["narration"].split()) for scene in revised["sections"]], [40, 40, 80])

    def test_no_expansion_when_the_total_is_in_range(self):
        script, plan = expansion_script([60, 100, 100], [80, 80, 80])
        script["target_duration_minutes"] = 2.0                               # 260/250 words, one scene short
        with patch.object(scriptwriter, "generate", side_effect=AssertionError("no call")):
            revised, attempts = scriptwriter._expand_underlength_scenes(script, story_brief(), bible(), plan)
        self.assertEqual(attempts, [])
        self.assertIs(revised, script)


class DialogueAndEndingTests(unittest.TestCase):
    BRIEF = premise_brief.parse(ENDING_BRIEF)
    OUTLINE = {"scenes": [{"scene_id": "s01", "arc_movements": ["hook"]},
                          {"scene_id": "s02", "arc_movements": ["ending"]}]}

    def check(self, first, last):
        script = {"sections": [{"id": 1, "outline_scene_id": "s01", "narration": first},
                               {"id": 2, "outline_scene_id": "s02", "narration": last}]}
        return premise_brief.faithfulness(self.BRIEF, bible=None, outline=self.OUTLINE, script=script) +             premise_brief.candidates(self.BRIEF, outline=self.OUTLINE, script=script)

    def test_items(self):
        self.assertEqual(premise_brief.beat_quotes(self.BRIEF)[0],
                         ("hook", "That's the best conversation I've had in two years."))
        self.assertEqual(premise_brief.ending_items(self.BRIEF),
                         ["at the grave Walter calls June", "Back home his phone glows gold",
                          "He turns it face down and plays one wrong note on the piano",
                          "who will he call tomorrow?"])

    def test_reported_speech_and_a_missing_ending_are_findings(self):
        found = self.check("Walter said that was the best conversation he had had in two years.",
                           "At the grave Walter calls June. Back home, the phone glowed gold. He went to bed.")
        claims = [item["claim"] for item in found]
        self.assertIn("the brief's line is not spoken as direct dialogue: "
                      "\"That's the best conversation I've had in two years.\"", claims)
        quoted = next(item for item in found if "I'm here whenever" in item["claim"])
        self.assertEqual(quoted["scene_ids"], [2])
        self.assertEqual(scriptwriter.semantic_issue_type(quoted["issue_type"]), "brief_faithfulness")
        ending = [item for item in found if item["claim"].startswith("ending detail missing")]
        self.assertEqual([item["claim"] for item in ending],
                         ["ending detail missing: 'He turns it face down and plays one wrong note on the piano'",
                          "ending detail missing: 'who will he call tomorrow?'"])
        self.assertEqual(ending[0]["scene_ids"], [2])
        self.assertEqual(ending[0]["kind"], "ending")                  # a lexical miss is only a candidate

    def test_a_faithful_script_has_no_findings(self):
        found = self.check(
            "Walter laughed. “That's the best conversation I've had in two years,” he told the phone.",
            "At the grave Walter called June. Back home his phone glowed gold. \"I'm here whenever you need me, "
            "Walter.\" He turned it face down and played one wrong note on the piano. Who would he call tomorrow?")
        self.assertEqual(found, [])

    def test_scene_contracts_ask_for_quoted_dialogue(self):
        rule = scriptwriter._author_rule({"author_brief": {"beats": {"hook": 'Nell says: "Stay."'}}}, "scenes")
        self.assertIn("(spoken aloud, in double quotes) Stay.", rule)
        self.assertIn("never copy their sentences", rule)


class RegenerationTests(unittest.TestCase):
    def run_dir(self, temp):
        folder = Path(temp)
        atomic_write_json(folder / "story_brief.json", story_brief())
        atomic_write_json(folder / "script.json", {"sections": [{"id": 1}, {"id": 2}]})
        atomic_write_json(folder / "character_bible.json", bible())
        atomic_write_json(folder / "visual_bible.json", {"characters": [], "references": {}})
        premise_brief.save(folder, premise_brief.parse(premise_brief.TEMPLATE))
        return folder

    def test_impact_lists_what_is_discarded(self):
        from studio import projects
        with tempfile.TemporaryDirectory() as temp:
            impact = projects.regeneration_impact(self.run_dir(temp))
            empty = projects.regeneration_impact(Path(temp, "nothing"))
        self.assertEqual(impact["discard"][:2], ["Character Bible", "roteiro (2 cenas)"])
        self.assertIn("visual bible (fica desatualizada e é refeita)", impact["discard"])
        self.assertIn("o briefing (premise_brief.md)", impact["keep"])
        self.assertEqual(empty, {"discard": [], "keep": []})

    def test_gui_asks_before_regenerating(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(gui.threading, "Thread") as thread:
            self.run_dir(temp)
            client = gui.app.test_client()
            refused = client.post("/api/run", json={"steps": ["research"], "target_duration": 12})
            self.assertEqual(refused.status_code, 409)
            self.assertTrue(refused.get_json()["needs_regenerate_confirmation"])
            thread.assert_not_called()
            stage = client.post("/api/projects/stage/story_brief", json={"action": "rerun"})
            self.assertEqual(stage.status_code, 409)
            accepted = client.post("/api/run", json={"steps": ["research"], "target_duration": 12,
                                                     "confirm_regenerate": True})
            later = client.post("/api/run", json={"steps": ["script"], "target_duration": 12})
        self.assertTrue(accepted.get_json()["ok"])
        self.assertTrue(later.get_json()["ok"])                       # other steps never ask
        self.assertIn("confirmRegenerate", Path("gui_templates/index.html").read_text(encoding="utf-8"))


class DiscardTests(unittest.TestCase):
    def test_only_empty_projects_are_discarded(self):
        import gui
        from studio import projects
        with tempfile.TemporaryDirectory() as temp, patch.object(projects, "root", return_value=Path(temp)):
            empty = projects.create("")
            used = projects.create("Used")
            (used / "story_brief.json").write_text("{}", encoding="utf-8")
            projects.set_active(empty.name)
            self.assertTrue(projects.summary(empty)["empty"])
            self.assertFalse(projects.summary(used)["empty"])
            client = gui.app.test_client()
            refused = client.post("/api/projects/discard", json={"slug": used.name})
            with patch.object(config, "OUTPUT_DIR", str(used)):
                done = client.post("/api/projects/discard", json={"slug": empty.name})
            self.assertEqual(refused.status_code, 400)
            self.assertTrue(done.get_json()["ok"])
            self.assertFalse(empty.exists())
            self.assertTrue(used.exists())
            self.assertIsNone(projects.active_slug())
        self.assertIn("Descartar projeto", Path("gui_templates/index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
