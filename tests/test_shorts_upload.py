"""Phase 3.7-B.3: Shorts in the approval gate, upload order and schedule, ledger, analytics, archive (YouTube mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_publications import FakeRequest
from tests.test_youtube_captions import FakeService, FakeVideos
from uploader import approval, publications, shorts_upload

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
GOOD_TITLE = "The Only One Who Never Got Tired of Him"


def make_run(folder: Path, *, count=3):
    atomic_write_json(folder / "script.json", {"title": GOOD_TITLE, "tags": ["fiction"], "sections": []})
    atomic_write_json(folder / "story_brief.json", {"psychological_theme": "loneliness", "logline": "x"})
    atomic_write_json(folder / "narrative_review.json", {"status": "ready_for_human_review"})
    atomic_write_json(folder / "project.json", {"slug": folder.name})
    (folder / "final_video.mp4").write_bytes(b"episode")
    (folder / "narration.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi.\n", encoding="utf-8")
    segments = []
    (folder / "shorts").mkdir()
    for number in range(1, count + 1):
        sid = f"short_{number:02d}"
        (folder / "shorts" / f"{sid}.mp4").write_bytes(f"short {number}".encode())
        (folder / "shorts" / f"{sid}.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi.\n", encoding="utf-8")
        segments.append({"id": sid, "title": f"He Thanked the Voice {number} #Shorts", "hook_text": "The best talk",
                         "end_text": "Full story on the channel", "description": "Walter found a listener.",
                         "start": number * 60.0, "end": number * 60.0 + 50, "include": True})
    atomic_write_json(folder / "shorts" / "shorts_plan.json", {"segments": segments})


class SequentialVideos(FakeVideos):
    """videos.insert returns the given ids in order (the episode first, then the Shorts)."""

    def __init__(self, ids):
        super().__init__()
        self.ids = iter(ids)

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return FakeRequest({"id": next(self.ids)})


class ScheduleTests(unittest.TestCase):
    def test_default_plan_now_then_one_a_day_at_six(self):
        times = shorts_upload.schedule(3, now=NOW)
        self.assertIsNone(times[0])
        self.assertEqual([(when.day, when.hour, when.minute) for when in times[1:]], [(30, 18, 0), (31, 18, 0)])
        self.assertEqual([shorts_upload.label(when, NOW) for when in times],
                         ["agora", "amanhã 18:00", "depois de amanhã 18:00"])
        self.assertEqual(shorts_upload.rfc3339(times[1]), "2026-10-30T21:00:00Z")

    def test_scheduled_episode_moves_the_shorts(self):
        times = shorts_upload.schedule(2, now=NOW, episode_publish_at="2026-11-02T21:00:00Z")
        self.assertEqual(times[0], datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc))
        self.assertEqual((times[1].astimezone(NOW.tzinfo).day, times[1].astimezone(NOW.tzinfo).hour), (3, 18))

    def test_confirmation_text(self):
        with tempfile.TemporaryDirectory() as temp:
            make_run(Path(temp))
            plan = shorts_upload.plan_lines(temp, privacy="public", now=NOW)
        self.assertEqual(plan["text"], "o episódio será PÚBLICO agora; Shorts: agora, amanhã 18:00, "
                                       "depois de amanhã 18:00")
        self.assertEqual(plan["shorts"][0]["publish_at"], "")


class ApprovalTests(unittest.TestCase):
    def test_shorts_are_part_of_the_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            make_run(folder)
            self.assertIn("shorts_reviewed", approval.checklist_for(folder))
            first = shorts_upload.binding(folder)
            plan = load_json(folder / "shorts/shorts_plan.json")
            plan["segments"][1]["title"] = "Another Title #Shorts"
            atomic_write_json(folder / "shorts/shorts_plan.json", plan)
            second = shorts_upload.binding(folder)
            (folder / "shorts/short_03.mp4").write_bytes(b"re-rendered")
            third = shorts_upload.binding(folder)
            plan["segments"][0]["include"] = False
            atomic_write_json(folder / "shorts/shorts_plan.json", plan)
            self.assertEqual([item["id"] for item in shorts_upload.included(folder)], ["short_02", "short_03"])
            summary = approval.review_summary(folder, title=GOOD_TITLE, video_path=str(folder / "final_video.mp4"),
                                              description="d", tags=[], thumbnail_path=None)
        self.assertEqual(len({first, second, third}), 3)
        self.assertEqual([item["id"] for item in summary["shorts"]], ["short_02", "short_03"])
        self.assertEqual(summary["shorts"][0]["video"], "shorts/short_02.mp4")


class UploadTests(unittest.TestCase):
    def test_episode_first_then_shorts_with_its_url_schedule_and_ledger(self):
        from uploader import youtube
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp, "ep1")
            folder.mkdir()
            make_run(folder)
            ledger = Path(temp, "publications.json")
            meta = dict(video_path=str(folder / "final_video.mp4"), title=GOOD_TITLE, description="d", tags=[],
                        thumbnail_path=None)
            approval.approve(folder, checklist={key: True for key in approval.checklist_for(folder)}, **meta)
            service = FakeService()
            service._videos = SequentialVideos(["episode1", "shortA", "shortB", "shortC"])
            original = shorts_upload.schedule                       # the real schedule, at a fixed "now"
            fixed = lambda count, **kwargs: original(count, now=NOW, episode_publish_at=kwargs.get("episode_publish_at", ""))
            with patch.object(config, "PUBLICATIONS_FILE", str(ledger)), \
                    patch.object(shorts_upload, "schedule", side_effect=fixed), \
                    patch("studio.projects.touch") as touched, redirect_stdout(io.StringIO()):
                record = youtube.upload_run(folder, service=service, privacy="public", **meta)
            saved = json.loads(ledger.read_text(encoding="utf-8"))["publications"]
        inserts = [call for name, call in service._videos.calls if name == "insert"]
        self.assertEqual([call["body"]["snippet"]["title"] for call in inserts],
                         [GOOD_TITLE, "He Thanked the Voice 1 #Shorts", "He Thanked the Voice 2 #Shorts",
                          "He Thanked the Voice 3 #Shorts"])
        self.assertIn("https://www.youtube.com/watch?v=episode1", inserts[1]["body"]["snippet"]["description"])
        self.assertEqual(inserts[1]["body"]["status"]["privacyStatus"], "public")
        self.assertEqual((inserts[2]["body"]["status"]["privacyStatus"], inserts[2]["body"]["status"]["publishAt"]),
                         ("private", "2026-10-30T21:00:00Z"))
        self.assertEqual(inserts[3]["body"]["snippet"]["defaultLanguage"], "en")
        self.assertEqual(record["shorts"], ["shortA", "shortB", "shortC"])
        shorts = [item for item in saved if item.get("parent_video_id")]
        self.assertEqual([(item["video_id"], item["parent_video_id"], item["short_id"]) for item in shorts],
                         [("shortA", "episode1", "short_01"), ("shortB", "episode1", "short_02"),
                          ("shortC", "episode1", "short_03")])
        touched.assert_called_once()                                          # only the episode updates the project
        self.assertEqual(touched.call_args.kwargs["published_video_id"], "episode1")
        captions = [call for name, call in service._captions.calls if name == "insert"]
        self.assertEqual(len(captions), 4)                                    # episode + 3 Shorts

    def test_reminder_names_the_episode(self):
        self.assertIn("Vídeo relacionado", shorts_upload.reminder("https://youtu.be/x", 3))


class LedgerTests(unittest.TestCase):
    def test_a_published_short_never_bans_a_topic(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp, "publications.json")
            atomic_write_json(ledger, {"publications": [
                {"video_id": "ep", "state": "uploaded_pending_publication", "project": "p", "premise": "x", "title": "t"},
                {"video_id": "sh", "state": "uploaded_pending_publication", "project": "p", "premise": "x",
                 "title": "s #Shorts", "parent_video_id": "ep"}]})

            class Service:
                def videos(self):
                    class Videos:
                        def list(self, **kwargs):
                            from tests.test_publications import FakeRequest
                            return FakeRequest({"items": [{"id": video, "status": {"privacyStatus": "public",
                                                                                   "uploadStatus": "processed"}}
                                                          for video in kwargs["id"].split(",")]})
                    return Videos()
            with patch("agents.researcher.append_banned_entry") as banned, \
                    patch.object(publications, "_archive_published") as archived:
                summary = publications.sync_publications(Service(), path=ledger)
        self.assertEqual(sorted(summary["published"]), ["ep", "sh"])
        self.assertEqual(banned.call_count, 1)                               # the episode only
        self.assertEqual(archived.call_args.args[0], ["p"])

    def test_analytics_lists_shorts_under_their_episode(self):
        from uploader import analytics
        records = [{"video_id": "ep", "title": "Episode", "state": "published"},
                   {"video_id": "sh", "title": "Short #Shorts", "state": "published", "parent_video_id": "ep"}]
        with tempfile.TemporaryDirectory() as temp, patch.object(analytics, "folder", return_value=Path(temp)), \
                patch.object(analytics, "published_records", return_value=records):
            overview = analytics.overview()
        self.assertEqual([item["video_id"] for item in overview["videos"]], ["ep"])
        self.assertEqual(overview["videos"][0]["shorts"][0]["video_id"], "sh")

    def test_archive_keeps_a_shorts_folder(self):
        from studio import archive
        self.assertEqual(archive.destination_for(Path("shorts/short_01.mp4")), "shorts/short_01.mp4")
        self.assertEqual(archive.destination_for(Path("shorts/shorts_plan.json")), "shorts/shorts_plan.json")


if __name__ == "__main__":
    unittest.main()
