"""Phase 3.6-C: description template with hook, chapters, CTA block, credits and hashtags (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import packaging
from agents.narrative import NarrativeValidationError, atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import description
from video.timeline import build_scene_timeline

SECTIONS = [{"id": index, "title": f"Scene {index}"} for index in range(1, 6)]
DURATIONS = [70.0, 6.0, 140.0, 95.0, 120.0]          # scene 2 is shorter than 10 s
PARTS = {"hook": "He needed the room to envy him. Tonight, the room stopped looking.",
         "chapter_titles": ["The Table", "A Glance", "The Toast", "Her Silence", "What Remains"],
         "comment_question": "Have you ever performed happiness for someone else's eyes?",
         "pinned_comment": "Have you ever performed happiness for someone else's eyes? Tell us below."}
SCRIPT = {"title": "He Turned His Wife's Grief Into a Show", "tags": ["Psychological Fiction", "short story", "envy"],
          "description": "Story."}


class ChapterTests(unittest.TestCase):
    def test_chapters_follow_youtube_rules(self):
        items = description.chapters(build_scene_timeline(SECTIONS, DURATIONS), PARTS["chapter_titles"])
        self.assertEqual([(round(item["start"]), item["title"]) for item in items],      # "A Glance" (6 s) absorbed
                         [(0, "The Table"), (76, "The Toast"), (216, "Her Silence"), (311, "What Remains")])
        short_first = description.chapters(build_scene_timeline(SECTIONS[:4], [4.0, 60.0, 60.0, 60.0]))
        self.assertEqual([(round(item["start"]), item["title"]) for item in short_first],
                         [(0, "Scene 2"), (64, "Scene 3"), (124, "Scene 4")])
        block = description.chapter_block(items)
        self.assertTrue(block.splitlines()[1].startswith("0:00 "))
        self.assertTrue(description.passes(description.validate(block + "\n" + description.cta_block("Why?"))))

    def test_fewer_than_three_chapters_are_left_out(self):
        items = description.chapters(build_scene_timeline(SECTIONS[:2], [40.0, 30.0]))
        self.assertEqual(description.chapter_block(items), "")
        findings = description.validate(description.cta_block("Why?"))
        self.assertEqual([item["rule"] for item in findings], ["no_chapters"])          # a warning only

    def test_validator_catches_each_rule(self):
        cta = description.cta_block("Why?")
        rules = lambda text: {item["rule"] for item in description.validate(text) if item["severity"] == "blocking"}
        self.assertEqual(rules("0:00 A\n0:30 B\n1:00 C"), {"no_cta"})
        self.assertIn("chapters_start", rules(cta + "\n0:05 A\n0:30 B\n1:00 C"))
        self.assertIn("chapters_count", rules(cta + "\n0:00 A\n0:30 B"))
        self.assertIn("chapters_short", rules(cta + "\n0:00 A\n0:05 B\n1:00 C"))
        self.assertIn("too_long", rules(cta + "\n" + "x" * 5001))
        self.assertEqual(rules(cta + "\n0:00 A\n0:30 B\n1:02:03 C"), set())


class BuildTests(unittest.TestCase):
    def build(self, parts=PARTS, template=None):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            atomic_write_json(output / "scene_timeline.json", build_scene_timeline(SECTIONS, DURATIONS))
            atomic_write_json(output / "asset_manifest.json", {"music": {"track": "x.mp3", "attribution": "Song by A (CC BY)"}})
            template_file = output / "template.txt"
            if template is not None:
                template_file.write_text(template, encoding="utf-8")
            with patch.object(description, "TEMPLATE_FILE", template_file):
                return description.build(output, SCRIPT, {"logline": "A man needs to be envied."}, parts)

    def test_full_description(self):
        text = self.build()
        self.assertTrue(text.startswith(PARTS["hook"]))
        for part in ("Chapters\n0:00 The Table", "1:16 The Toast", description.CTA_MARKER,
                     "share it with someone who'd recognise themselves",
                     "Have you ever performed happiness for someone else's eyes? Tell us in the comments.",
                     "Music: Song by A (CC BY)", "original work of fiction"):
            self.assertIn(part, text)
        self.assertEqual(text.strip().splitlines()[-1], "#PrivateGravity #PsychologicalFiction #ShortStory")
        self.assertTrue(description.passes(description.validate(text)))
        self.assertLessEqual(len(text), 5000)

    def test_without_parts_the_logline_is_the_hook_and_scene_titles_are_used(self):
        text = self.build(parts=None)
        self.assertTrue(text.startswith("A man needs to be envied."))
        self.assertIn("0:00 Scene 1", text)
        self.assertIn("What stayed with you after the ending?", text)

    def test_a_template_without_the_cta_is_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(description, "TEMPLATE_FILE", Path(temp, "t.txt")):
            with self.assertRaises(description.DescriptionError):
                description.save_template("{hook}\n{chapters}")
            with self.assertRaises(description.DescriptionError):
                description.save_template("{hook} {chapters} {cta} {unknown}")
            description.save_template("{title}\n{hook}\n\n{chapters}\n\n{cta}\n{hashtags}")
            self.assertIn("{title}", description.template())
        with self.assertRaises(description.DescriptionError):
            self.build(template="{hook}\n{chapters}\n" + "x" * 5001 + "\n{cta}")


class PartsTests(unittest.TestCase):
    def test_parts_are_validated_and_cached(self):
        calls = []

        def generate(prompt, *args, **kwargs):
            calls.append(prompt)
            return json.dumps(PARTS)
        timeline = build_scene_timeline(SECTIONS, DURATIONS)
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            first = packaging.generate_description_parts(temp, brief={"logline": "x"}, script=SCRIPT, timeline=timeline,
                                                         generator=generate)
            packaging.generate_description_parts(temp, brief={"logline": "x"}, script=SCRIPT, timeline=timeline,
                                                 generator=generate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["chapter_titles"], PARTS["chapter_titles"])
        self.assertIn("Never spoil the ending", calls[0])
        bad = dict(PARTS, comment_question="Tell us", chapter_titles=PARTS["chapter_titles"][:2])
        with self.assertRaises(NarrativeValidationError) as caught:
            packaging.validate_description_parts(bad, 5)
        self.assertIn("exactly 5", str(caught.exception))
        self.assertIn("ending with '?'", str(caught.exception))


class GuiTests(unittest.TestCase):
    def test_check_and_template_endpoints(self):
        import gui
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, packaging.DESCRIPTION_PARTS_FILE), PARTS)
            with patch.object(config, "OUTPUT_DIR", temp), patch.dict(gui._state, {"description": "no cta here"}), \
                    patch.object(description, "TEMPLATE_FILE", Path(temp, "t.txt")):
                client = gui.app.test_client()
                check = client.get("/api/description/check").get_json()
                self.assertEqual(client.post("/api/description-template", json={"template": "{hook}"}).status_code, 400)
                self.assertTrue(client.post("/api/description-template",
                                            json={"template": "{hook}\n{chapters}\n{cta}"}).get_json()["ok"])
                self.assertEqual(client.get("/api/description-template").get_json()["template"], "{hook}\n{chapters}\n{cta}")
        self.assertFalse(check["passes"])
        self.assertEqual(check["pinned_comment"], PARTS["pinned_comment"])


if __name__ == "__main__":
    unittest.main()
