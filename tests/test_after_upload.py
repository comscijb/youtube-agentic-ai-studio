"""Phase 4.0-E/G: the after-upload checklist, the playlist insertion, the end card and the Community drafts."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import after_upload, discovery_settings, publications

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
TYPE_B = "philosophical_thought_experiment"


def ledger():
    return {"schema_version": 1, "publications": [
        {"video_id": "old_b", "state": "published", "published_at": "2026-11-01T00:00:00+00:00", "project": "b1",
         "title": "Everyone Heard What He Thought", "narrative_type": TYPE_B},
        {"video_id": "old_a", "state": "published", "published_at": "2026-11-05T00:00:00+00:00", "project": "a1",
         "title": "She Watched 9,000 Videos", "narrative_type": "realistic_psychological_fiction"},
        {"video_id": "new_b", "state": "published", "published_at": "2026-11-10T00:00:00+00:00", "project": "b2",
         "title": "For One Day, Everyone Had His Face", "narrative_type": TYPE_B},
        {"video_id": "short1", "state": "published", "parent_video_id": "new_b", "kind": "short", "title": "He Woke Up #Shorts"}]}


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        atomic_write_json(config.PUBLICATIONS_FILE, ledger())
        project = Path(config.PROJECTS_DIR) / "b2"
        project.mkdir(parents=True, exist_ok=True)
        atomic_write_json(project / "script.json", {"narrative_type": TYPE_B, "sections": [
            {"section_type": "opening", "narration": "a"}, {"section_type": "setup", "narration": "b"},
            {"section_type": "reversal", "narration": "c"}]})
        atomic_write_json(project / "scene_timeline.json", {"total_duration": 300, "scenes": [
            {"title": "One", "start": 0, "end": 60}, {"title": "Two", "start": 60, "end": 200}, {"title": "The Turn", "start": 200, "end": 300}]})
        atomic_write_json(project / "description_parts.json", {"pinned_comment": "Would you talk to yourself? Tell us."})
        atomic_write_json(project / "story_brief.json", {"logline": "A man wakes up beside his AI clone.",
                                                         "central_dramatic_question": "Who keeps the life?"})

    def test_values_ready_to_copy(self):
        found = after_upload.checklist("new_b")
        values = {item["id"]: item["value"] for item in found["items"]}
        self.assertEqual(found["end_screen"]["video_id"], "old_b")                     # same type, not the newer type A
        self.assertIn("3:20", values["card"])                                          # the reversal starts at 200 s
        self.assertIn("início da virada", values["card"])
        self.assertIn("short1", values["related_video"])
        self.assertIn("watch?v=new_b", values["related_video"])
        self.assertEqual(values["pinned_comment"], "Would you talk to yourself? Tell us.")
        self.assertIn("dublagem automática", values["auto_dubbing"])
        self.assertIn("Configurações", values["playlist"])
        self.assertTrue(values["community_poll"].startswith("What would you do?"))
        self.assertIn("Tomorrow on Private Gravity", values["community_teaser"])

    def test_ticks_persist_in_the_ledger_and_overdue_lists_the_rest(self):
        after_upload.tick("new_b", "card", now=NOW)
        record = next(item for item in publications.load_ledger()["publications"] if item["video_id"] == "new_b")
        self.assertEqual(record["after_upload"]["card"], "2026-11-12T12:00:00+00:00")
        overdue = {item["video_id"]: item["missing"] for item in after_upload.overdue(now=NOW)}
        self.assertNotIn("Card no pico de curiosidade", overdue["new_b"])
        self.assertIn("Tela final", overdue["new_b"])
        after_upload.tick("new_b", "card", False)
        self.assertNotIn("card", next(item for item in publications.load_ledger()["publications"]
                                      if item["video_id"] == "new_b")["after_upload"])
        with self.assertRaises(ValueError):
            after_upload.tick("new_b", "likes")

    def test_a_video_younger_than_a_day_is_not_overdue(self):
        self.assertNotIn("new_b", {item["video_id"] for item in after_upload.overdue(now=datetime(2026, 11, 10, 6, tzinfo=timezone.utc))})


class PlaylistTests(unittest.TestCase):
    def test_insert_into_the_type_playlist(self):
        service = MagicMock()
        service.playlistItems().insert().execute.return_value = {"id": "item1"}
        discovery_settings.save({"playlists": {TYPE_B: "PLabcdefghij123"}})
        found = after_upload.add_episode_to_playlist(service, "vid1", TYPE_B)
        self.assertEqual(found, {"action": "added", "playlist_id": "PLabcdefghij123", "id": "item1"})
        body = service.playlistItems().insert.call_args.kwargs["body"]
        self.assertEqual(body["snippet"], {"playlistId": "PLabcdefghij123", "resourceId": {"kind": "youtube#video", "videoId": "vid1"}})

    def test_unconfigured_or_failing_never_raises(self):
        self.assertEqual(after_upload.add_episode_to_playlist(MagicMock(), "vid1", "dark_human_parable")["action"], "not_configured")
        service = MagicMock()
        service.playlistItems().insert().execute.side_effect = RuntimeError("quota")
        discovery_settings.save({"playlists": {"dark_human_parable": "PLzzzzzzzzzz99"}})
        self.assertEqual(after_upload.add_episode_to_playlist(service, "vid1", "dark_human_parable")["action"], "failed")
        with self.assertRaises(ValueError):
            discovery_settings.save({"playlists": {"dark_human_parable": "not a playlist!"}})


class EndCardTests(unittest.TestCase):
    def test_the_hype_line_and_no_trade_for_likes(self):
        from video import creator
        lines = creator.end_card_lines()
        self.assertEqual(lines[-1], "If this stayed with you, hype it and share it.")
        self.assertTrue(lines[0].startswith("Written & directed by"))
        self.assertNotRegex(" ".join(lines).lower(), r"like (and|&) subscribe|in exchange")


class CommunityDraftTests(unittest.TestCase):
    def test_gemini_drafts_are_validated(self):
        reply = json.dumps({"poll": {"question": "What would you do? Keep your life or share it?",
                                     "options": ["Keep it", "Share it", "Walk away"]},
                            "teaser": {"text": "Tomorrow: a man meets the one person who knows him too well."}})
        found = after_upload.generate_drafts({"title": "t"}, {"logline": "x"}, TYPE_B, generator=lambda *a, **k: reply)
        self.assertEqual(found["poll"]["options"], ["Keep it", "Share it", "Walk away"])
        from agents.narrative import NarrativeValidationError
        with self.assertRaises(NarrativeValidationError):
            after_upload.validate_drafts({"poll": {"question": "What would you do?", "options": ["Only one"]},
                                          "teaser": {"text": "x"}}, "What would you do?")

    def test_type_questions(self):
        self.assertEqual(after_upload.POLL_QUESTIONS["realistic_psychological_fiction"], "Who was right?")
        self.assertEqual(after_upload.POLL_QUESTIONS["dark_human_parable"], "Which part of this village are you?")


if __name__ == "__main__":
    unittest.main()
