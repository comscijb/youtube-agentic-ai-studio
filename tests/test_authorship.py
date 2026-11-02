"""Phase 3.9-D: visible human authorship — credit line, end credit card, editorial record."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import editorial_record
from video import description


class CreditLineTests(unittest.TestCase):
    def test_line_names_the_channel_and_the_validator_requires_it(self):
        line = description.authorship()
        self.assertIn(config.CHANNEL_NAME, line)
        self.assertIn("AI tools under human direction", line)
        rules = {item["rule"] for item in description.validate(description.cta_block("Why?"))}
        self.assertIn("no_authorship", rules)
        with patch.object(config, "DESCRIPTION_CREDIT_LINE", ""):
            self.assertNotIn("no_authorship", {item["rule"] for item in description.validate("x")})

    def test_a_custom_template_without_the_placeholder_still_gets_it_before_the_hashtags(self):
        from tests.test_description import BuildTests
        text = BuildTests("test_full_description").build(template="{hook}\n\n{chapters}\n\n{cta}\n\n{hashtags}")
        line = description.authorship()
        self.assertIn(line, text)
        hashtags = [row for row in text.splitlines() if row.startswith("#")]
        if hashtags:
            self.assertLess(text.index(line), text.index(hashtags[0]))
        self.assertTrue(description.passes(description.validate(text)))

    def test_about_text_is_honest(self):
        about = description.about_text()
        self.assertIn("written, edited and reviewed by a person", about)
        self.assertIn("AI tools", about)


class EndCardTests(unittest.TestCase):
    def test_card_is_appended_and_timed(self):
        from moviepy.editor import ColorClip
        from video import creator
        base = ColorClip((creator.W, creator.H), color=(10, 10, 10), duration=2.0)
        with patch.object(config, "END_CREDIT_CARD", True):
            final = creator.with_end_card(base)
        self.assertAlmostEqual(final.duration, 2.0 + config.END_CREDIT_SECONDS, places=2)
        frame = final.get_frame(2.0 + config.END_CREDIT_SECONDS / 2)
        middle = frame[int(creator.H * 0.4):int(creator.H * 0.6)]
        self.assertGreater(int(np.abs(middle.astype(int) - np.array(config.COLORS["background"])).max()), 80)
        self.assertIn("Written & directed by", creator.end_card_text())
        with patch.object(config, "END_CREDIT_CARD", False):
            self.assertIs(creator.with_end_card(base), base)


def make_run(folder: Path) -> Path:
    atomic_write_json(folder / "premise_brief.json", {"logline": "x", "protagonist": "Walter", "hash": "h"})
    atomic_write_json(folder / "script.json", {"title": "The Only One", "editorial_edits": [
        {"scene_id": 3, "author": "human_editorial", "applied_at": "2026-10-30T18:52:33+00:00"},
        {"scene_id": 6, "author": "human_editorial", "applied_at": "2026-10-30T18:52:33+00:00"},
        {"scene_id": 2, "author": "gemini", "applied_at": "x"}]})
    atomic_write_json(folder / "shot_list.json", {"shots": [
        {"shot_id": "s01_01", "qa": {"reviewed": True, "actions": [{"action": "edit", "origin": "human"}]}},
        {"shot_id": "s01_02", "seed_offset": 2, "qa": {"reviewed": True}},
        {"shot_id": "s02_01", "action_edited_by_human": True, "qa": {"actions": [{"action": "edit", "origin": "qa"}]}},
        {"shot_id": "s02_02", "reuse_of": "s01_01"}]})
    atomic_write_json(folder / "visual_bible.json", {"references": {"character:walter_hale": {}, "location:front_room": {}}})
    atomic_write_json(folder / "titles.json", {"selected": "The Only One", "selected_reason": "kept",
                                               "candidates": [{"title": "a"}, {"title": "b"}]})
    atomic_write_json(folder / "thumbnails" / "thumbnails.json", {"selected": 4, "variants": [
        {"number": n, "concept": {"composition": "big_face"}} for n in range(1, 7)]})
    return folder


class EditorialRecordTests(unittest.TestCase):
    def test_summary_from_fixture_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            record = editorial_record.build(make_run(Path(temp)))
        self.assertTrue(record["premise_brief"])
        self.assertEqual([item["scene"] for item in record["script_edits"]], [3, 6])        # human edits only
        self.assertEqual((record["shots_total"], record["shots_reviewed"]), (3, 2))
        self.assertEqual(record["shots_edited"], ["s01_01"])
        self.assertEqual(record["shots_regenerated"], ["s01_02"])
        self.assertEqual(record["shot_descriptions_rewritten"], ["s02_01"])
        self.assertEqual(record["thumbnail"], {"selected_variant": 4, "variants_generated": 6, "composition": "big_face"})
        text = editorial_record.summary(record)
        for part in ("The author wrote the premise brief", "2 screenplay edit(s) applied by hand in scene(s) 3, 6",
                     "2 of 3 shots reviewed by hand; 1 edited; 1 regenerated; 1 shot descriptions rewritten",
                     "2 character/location reference(s)", "“The Only One”: the author's own title",
                     "Thumbnail variant #4 chosen from 6"):
            self.assertIn(part, text)

    def test_the_approval_keeps_the_record(self):
        from uploader import approval
        with tempfile.TemporaryDirectory() as temp:
            folder = make_run(Path(temp))
            atomic_write_json(folder / "narrative_review.json", {"status": "ready_for_human_review"})
            (folder / "final_video.mp4").write_bytes(b"v")
            title = "The Only One Who Never Got Tired of Him"
            with patch.object(approval, "packaging_findings", return_value=[]):
                approval.approve(folder, checklist={key: True for key in approval.checklist_for(folder)},
                                 video_path=str(folder / "final_video.mp4"), title=title, description="d")
            saved = load_json(folder / approval.APPROVAL_FILE)
        self.assertEqual(saved["editorial_record"]["shots_edited"], ["s01_01"])
        self.assertIn("screenplay edit(s) applied by hand", saved["editorial_summary"])


class SettingsTests(unittest.TestCase):
    def test_credit_line_and_end_card_switch(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(gui, "ROOT", temp), \
                patch.object(config, "DESCRIPTION_CREDIT_LINE", "x"), patch.object(config, "END_CREDIT_CARD", True):
            Path(temp, "config.py").write_text("RENDER_PRESET = 'veryfast'\n", encoding="utf-8")
            client = gui.app.test_client()
            self.assertIn("about_text", client.get("/api/settings").get_json())
            self.assertEqual(client.post("/api/save-settings", json={"credit_line": "  "}).status_code, 400)
            self.assertTrue(client.post("/api/save-settings", json={"credit_line": "Written by {channel}.",
                                                                    "end_credit_card": False}).get_json()["ok"])
            self.assertEqual((config.DESCRIPTION_CREDIT_LINE, config.END_CREDIT_CARD), ("Written by {channel}.", False))
            env = Path(temp, ".env").read_text(encoding="utf-8")
        self.assertIn("END_CREDIT_CARD=false", env)


if __name__ == "__main__":
    unittest.main()
