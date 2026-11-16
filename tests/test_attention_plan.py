"""Phase 4.1-F: the outline's attention beats and loop plan, the ear-style block, the duration by type."""
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents import attention, attention_revision, scriptwriter

BEATS = {"opens_with": "\"Sign it,\" the Merchant says.", "inside": "The deed names the sunrise.", "ends_on": "Who will pay to look east?"}


def outline(beats=BEATS, plan=None, first=None):
    scenes = [{"scene_id": f"s{index}", "section_type": kind, "attention_beats": dict(beats)}
              for index, kind in enumerate(["opening", "development", "reversal", "aftermath"], 1)]
    if first:
        scenes[0]["attention_beats"]["opens_with"] = first
    return {"scenes": scenes, "loop_plan": plan if plan is not None else [
        {"id": "who_pays", "question": "Who will pay?", "opens": "s1", "closes": "s3"},
        {"id": "what_next", "question": "What will he sell next?", "opens": "s3", "closes": None}]}


class PlanTests(unittest.TestCase):
    def test_a_good_plan_passes(self):
        self.assertEqual(attention.outline_issues(outline()), [])

    def test_missing_beats_a_descriptive_opening_and_a_gap_without_a_question(self):
        missing = outline(beats={"opens_with": "x", "inside": "", "ends_on": "y"})
        self.assertIn("needs attention_beats", " ".join(attention.outline_issues(missing)))
        weather = outline(first="Dawn filtered over the quiet valley.")
        self.assertIn("opens on description", " ".join(attention.outline_issues(weather)))
        gap = outline(plan=[{"id": "who_pays", "question": "?", "opens": "s1", "closes": "s2"}])
        self.assertIn("no question is open during scene s3", " ".join(attention.outline_issues(gap)))
        backwards = outline(plan=[{"id": "who_pays", "question": "?", "opens": "s3", "closes": "s1"}])
        self.assertIn("closes before it opens", " ".join(attention.outline_issues(backwards)))
        self.assertIn("loop_plan", " ".join(attention.outline_issues(outline(plan=[]))))

    def test_the_cold_open_needs_no_beats(self):
        plan = outline()
        plan["scenes"].insert(0, {"scene_id": "s0", "section_type": "cold_open"})
        self.assertEqual(attention.outline_issues(plan), [])


class PromptTests(unittest.TestCase):
    def test_the_scene_prompt_writes_for_the_ear_and_the_outline_asks_for_beats(self):
        source = Path(scriptwriter.__file__).read_text(encoding="utf-8")
        self.assertIn("WRITE FOR THE EAR, FOR A PHONE", scriptwriter.EAR_RULE)
        self.assertIn("never clickbait-cheap", scriptwriter.EAR_RULE)
        self.assertIn("Days passed", scriptwriter.EAR_RULE)
        self.assertIn("attention_beats", source)
        self.assertIn("loop_plan", source)


class DurationTests(unittest.TestCase):
    def test_targets_by_type(self):
        self.assertEqual(config.ATTENTION_TARGET_MINUTES, {"A": 9, "B": 9, "C": 7})
        self.assertEqual(attention_revision.target_minutes("realistic_psychological_fiction"), 9)
        self.assertEqual(attention_revision.target_minutes("philosophical_thought_experiment"), 9)
        self.assertEqual(attention_revision.target_minutes("dark_human_parable"), 7)
        self.assertLessEqual(config.EPISODE_MINUTES[0], 7)

    def test_the_gui_sends_auto(self):
        import gui
        client = gui.app.test_client()
        with patch.object(gui, "_run_pipeline"), patch("threading.Thread.start"):
            response = client.post("/api/run", json={"video_type": "normal", "target_duration": "auto", "steps": ["research"]})
        self.assertNotEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
