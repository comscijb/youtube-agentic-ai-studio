"""Phase 3.7-A: detailed premise briefs — parsing, authority in Story Development, faithfulness (Gemini mocked)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import premise_brief, researcher, scriptwriter
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_narrative import story_brief

PEOPLE_PLEASER = """She Said Yes to Everyone for 20 Years

Margaret, 52, has never refused a favour. When her sister asks for one more, she finally says no
- and discovers nobody knows who she is when she is not useful.
"""


class ParseTests(unittest.TestCase):
    def test_template_fields(self):
        brief = premise_brief.parse(premise_brief.TEMPLATE)
        self.assertEqual(brief["narrative_type"], "realistic_psychological_fiction")
        self.assertEqual(brief["target_minutes"], 12.0)
        self.assertEqual(set(brief["beats"]), set(premise_brief.BEAT_KEYS))
        self.assertEqual(brief["must_include"], ["the untuned piano in the front room", "Sunday phone calls"])
        self.assertEqual(len(brief["other_characters"]), 2)
        self.assertTrue(brief["raw"].startswith("# Working title"))

    def test_free_text_json_and_a_single_sentence(self):
        free = premise_brief.parse(PEOPLE_PLEASER)
        self.assertIn("Margaret, 52", free["notes"])
        self.assertTrue(premise_brief.is_brief(PEOPLE_PLEASER))
        one = "A man secretly attends the retirement party held for his invented twin."
        self.assertFalse(premise_brief.is_brief(one))                     # unchanged behaviour: a premise
        self.assertFalse(premise_brief.is_brief(""))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "brief.json")
            path.write_text(json.dumps({"logline": "x", "narrative_type": "Type C", "must avoid": "gore; jump scares",
                                        "pet": "a cat"}), encoding="utf-8")
            parsed = premise_brief.parse(path=path)
            path.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(premise_brief.BriefError):
                premise_brief.parse(path=path)
        self.assertEqual(parsed["narrative_type"], "dark_human_parable")
        self.assertEqual(parsed["must_avoid"], ["gore", "jump scares"])
        self.assertEqual(parsed["notes"], "pet: a cat")

    def test_save_load_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            record = premise_brief.save(temp, premise_brief.parse(premise_brief.TEMPLATE))
            self.assertTrue(Path(temp, "premise_brief.md").read_text(encoding="utf-8").startswith("# Working title"))
            self.assertEqual(premise_brief.load(temp)["hash"], record["hash"])
        self.assertEqual(record["hash"], premise_brief.brief_hash(premise_brief.parse(premise_brief.TEMPLATE)))


class StoryDevelopmentTests(unittest.TestCase):
    def develop(self, brief=None, topic=""):
        payload = story_brief()
        payload["brief_coverage"] = {"tone": "used for the atmosphere"}
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(researcher, "generate", return_value=json.dumps(payload)) as generated, \
                patch.object(researcher, "_score_titles", new=lambda brief: None), \
                patch.object(researcher, "load_banned_topics", return_value=[
                    {"raw": "x", "theme": "Regret and alternative lives", "angle": "undo regret by controlling a younger self"}]), \
                redirect_stdout(io.StringIO()) as out:
            result = researcher.research_topic("channel", topic_override=topic, premise_brief=brief)
        return result, generated.call_args_list[0].args[0], out.getvalue()

    def test_brief_is_authoritative(self):
        brief = premise_brief.parse(premise_brief.TEMPLATE)
        result, prompt, out = self.develop(brief)
        self.assertIn("AUTHOR BRIEF (authoritative)", prompt)
        self.assertIn("the untuned piano in the front room", prompt)
        self.assertEqual(result["logline"], brief["logline"])                         # the model's is replaced
        self.assertEqual(result["topic"], brief["logline"])
        self.assertEqual(result["narrative_type"], "realistic_psychological_fiction")
        self.assertEqual(result["target_duration_minutes"], 12.0)
        self.assertEqual(result["thumbnail_concept"], brief["thumbnail_idea"])
        self.assertEqual(result["title_candidates"][0]["title"], "The Only One Who Never Got Tired of Him")
        self.assertEqual(result["requested_brief_hash"], premise_brief.brief_hash(brief))
        self.assertEqual(result["author_brief"]["must_avoid"], brief["must_avoid"])
        self.assertNotIn("raw", result["author_brief"])
        coverage = result["brief_coverage"]
        self.assertEqual((coverage["logline"], coverage["narrative_type"], coverage["working_title"]),
                         ("preserved", "preserved", "kept as a title candidate"))
        self.assertEqual(coverage["tone"], "passed to the bible, outline and scenes")
        self.assertIn("match a published episode", out)                                # a warning, not a block
        self.assertTrue(result["published_topic_warning"])

    def test_one_sentence_premise_is_unchanged(self):
        result, prompt, _ = self.develop(topic="My exact premise")
        self.assertEqual(result["topic"], "My exact premise")
        self.assertNotIn("AUTHOR BRIEF", prompt)
        for key in ("author_brief", "requested_brief_hash"):
            self.assertNotIn(key, result)

    def test_prompts_receive_only_the_relevant_parts(self):
        brief = {"author_brief": premise_brief.author_parts(premise_brief.parse(premise_brief.TEMPLATE))}
        for stage in ("bible", "outline", "scenes", "review"):
            self.assertIn("author_brief", scriptwriter._author_rule(brief, stage))
            self.assertEqual(scriptwriter._author_rule({}, stage), "")
        self.assertNotIn("logline", brief["author_brief"])                            # not the whole raw text


class FaithfulnessTests(unittest.TestCase):
    BRIEF = premise_brief.parse(premise_brief.TEMPLATE)

    def outline(self, movements):
        return {"scenes": [{"scene_id": f"s0{index}", "summary": "Walter talks to Ada", "arc_movements": [movement]}
                           for index, movement in enumerate(movements, 1)]}

    def script(self, *texts):
        return {"sections": [{"id": index, "narration": text} for index, text in enumerate(texts, 1)]}

    def test_each_rule(self):
        bible = {"characters": [{"name": "Walter Hale", "age": "71"}, {"name": "June", "age": "41"}]}
        script = self.script("Walter sits by the untuned piano in the front room.",
                             "Ada becomes sentient and evil, plotting against him.")
        found = premise_brief.faithfulness(self.BRIEF, bible=bible, outline=self.outline(["hook", "reversal"]),
                                           script=script)
        claims = [item["claim"] for item in found]
        self.assertTrue(all(item["severity"] == "major" and item["certainty"] == "confirmed" for item in found))
        claims += [item["claim"] for item in premise_brief.candidates(   # lexical misses wait for the editor
            self.BRIEF, outline=self.outline(["hook", "reversal"]), script=script)]
        self.assertIn("must_include item missing: 'Sunday phone calls'", claims)
        self.assertNotIn("must_include item missing: 'the untuned piano in the front room'", claims)
        avoid = next(item for item in found if item["claim"].startswith("must_avoid"))
        self.assertEqual(avoid["scene_ids"], [2])
        self.assertIn("beat 'hidden_cost' has no outline scene (arc_movements)", claims)
        self.assertNotIn("beat 'hook' has no outline scene (arc_movements)", claims)
        self.assertIn("Walter Hale's age changed (68 → 71)", claims)
        self.assertIn("character 'Ada' from the brief is not in the Character Bible", claims)
        self.assertEqual(scriptwriter.semantic_issue_type("brief_faithfulness"), "brief_faithfulness")

    def test_findings_join_the_review(self):
        review = {"status": "ready_for_human_review", "major_issues": []}
        story = {"author_brief": premise_brief.author_parts(self.BRIEF)}
        result = premise_brief.add_findings(review, story, bible={"characters": []}, outline={"scenes": []},
                                            script=self.script("Nothing relevant."))
        self.assertEqual(result["status"], "revision_required")
        self.assertTrue(result["brief_faithfulness"])
        untouched = premise_brief.add_findings({"status": "x"}, {}, bible=None, outline=None, script=None)
        self.assertEqual(untouched, {"status": "x"})


class ProjectTests(unittest.TestCase):
    def test_cli_new_project_and_stage_invalidation(self):
        from scripts import project as cli
        from studio import archive, projects
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp, "projects")
            brief_file = Path(temp, "ai-companion.md")
            brief_file.write_text(premise_brief.TEMPLATE, encoding="utf-8")
            with patch.object(projects, "root", return_value=root), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(["new", "--brief-file", str(brief_file), "--minutes", "14"]), 0)
                folder = root / projects.active_slug()
                no_target = Path(temp, "no-target.md")
                no_target.write_text(premise_brief.TEMPLATE.replace("# Target minutes\n12", ""), encoding="utf-8")
                self.assertEqual(cli.main(["new", "--brief-file", str(no_target), "--minutes", "3"]), 1)
                saved = premise_brief.load(folder)
                self.assertEqual(saved["target_minutes"], 12.0)                  # the brief wins over --minutes
                self.assertEqual(projects.load(folder)["target_duration_minutes"], 12.0)
                self.assertIn("the-only-one-who-never-got-tired-of-him", folder.name)
                status = {item["stage"]: item for item in projects.stage_status(folder)}
                self.assertEqual(status["story_brief"]["detail"], "premise brief ready")
                atomic_write_json(folder / "story_brief.json", dict(story_brief(), requested_brief_hash=saved["hash"]))
                self.assertEqual({item["stage"]: item["status"] for item in projects.stage_status(folder)}["story_brief"],
                                 "done")
                changed = premise_brief.parse(premise_brief.TEMPLATE.replace("Leeds", "York"))
                premise_brief.save(folder, changed)
                self.assertEqual({item["stage"]: item["status"] for item in projects.stage_status(folder)}["story_brief"],
                                 "stale")
        self.assertIn("Brief fields:", out.getvalue())
        self.assertIn("it wins over --minutes 14", out.getvalue())
        self.assertEqual(archive.FILES["premise_brief.md"], "text")
        self.assertEqual(archive.FILES["premise_brief.json"], "text")


class GuiTests(unittest.TestCase):
    def test_template_and_parse_endpoints(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            client = gui.app.test_client()
            info = client.get("/api/premise-brief").get_json()
            parsed = client.post("/api/premise-brief/parse", json={"text": info["template"]}).get_json()
            one = client.post("/api/premise-brief/parse", json={"text": "One sentence premise."}).get_json()
            page = client.get("/").get_data(as_text=True)
        self.assertEqual(info["current"], "")
        self.assertIn("must_include", parsed["fields"])
        self.assertFalse(one["brief"])
        for text in ("Inserir modelo", "Carregar arquivo", "brief-count"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
