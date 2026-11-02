"""Phase 3.6-D: English language fields, caption upload, scope re-consent and the backfill (YouTube mocked)."""
import io
import json
import pickle
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_publications import FakeCreds, FakeRequest
from uploader import youtube
from video.timeline import build_scene_timeline

FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"


class FakeCaptions:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return FakeRequest({"items": self.existing})

    def insert(self, **kwargs):
        kwargs["media"] = kwargs["media_body"].getbytes(0, 10 ** 6)
        self.calls.append(("insert", kwargs))
        return FakeRequest({"id": "cap1"})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return FakeRequest({"id": kwargs["body"]["id"]})


class FakeVideos:
    def __init__(self, snippet=None):
        self.snippet = snippet or {"title": "Old title", "categoryId": "24", "description": "old", "tags": ["a"]}
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return FakeRequest({"items": [{"id": kwargs["id"], "snippet": dict(self.snippet)}]})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return FakeRequest({})

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return FakeRequest({"id": "vid000001"})


class FakeService:
    def __init__(self, captions=None, snippet=None):
        self._captions, self._videos = FakeCaptions(captions), FakeVideos(snippet)

    def captions(self):
        return self._captions

    def videos(self):
        return self._videos

    def thumbnails(self):
        raise AssertionError("no thumbnail expected")


class LanguageTests(unittest.TestCase):
    def test_upload_sets_english_language_fields(self):
        service = FakeService()
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp, "v.mp4")
            video.write_bytes(b"video")
            youtube.upload_video(str(video), title="T", description="D", service=service)
        body = next(call for name, call in service._videos.calls if name == "insert")["body"]
        self.assertEqual((body["snippet"]["defaultLanguage"], body["snippet"]["defaultAudioLanguage"]), ("en", "en"))

    def test_snippet_update_keeps_title_tags_and_category(self):
        service = FakeService()
        snippet = youtube.update_snippet("abc", service=service, description="New", language=True)
        sent = next(call for name, call in service._videos.calls if name == "update")["body"]
        self.assertEqual(sent["id"], "abc")
        self.assertEqual((sent["snippet"]["title"], sent["snippet"]["categoryId"], sent["snippet"]["tags"]),
                         ("Old title", "24", ["a"]))
        self.assertEqual((snippet["description"], snippet["defaultLanguage"], snippet["defaultAudioLanguage"]),
                         ("New", "en", "en"))
        untouched = youtube.update_snippet("abc", service=FakeService(), language=True)
        self.assertEqual(untouched["description"], "old")


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.srt = Path(self.temp.name, "narration.srt")
        self.srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello.\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_insert_an_english_track(self):
        service = FakeService()
        result = youtube.upload_captions("abc", self.srt, service=service)
        name, call = service._captions.calls[-1]
        self.assertEqual((name, result["action"]), ("insert", "inserted"))
        self.assertEqual(call["body"]["snippet"], {"videoId": "abc", "language": "en", "name": "English",
                                                   "isDraft": False})
        self.assertIn(b"Hello.", call["media"])

    def test_an_existing_track_is_kept_unless_replaced(self):
        track = {"id": "cap9", "snippet": {"language": "en", "name": "English", "trackKind": "standard"}}
        auto = {"id": "asr1", "snippet": {"language": "en", "name": "", "trackKind": "asr"}}
        service = FakeService(captions=[auto, track])
        self.assertEqual(youtube.upload_captions("abc", self.srt, service=service), {"action": "kept", "id": "cap9"})
        self.assertEqual(youtube.upload_captions("abc", self.srt, service=service, replace=True)["action"], "updated")
        self.assertEqual(youtube.upload_captions("abc", self.srt, service=FakeService(captions=[auto]))["action"],
                         "inserted")                                       # YouTube's automatic track is not ours

    def test_missing_file_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            youtube.upload_captions("abc", Path(self.temp.name, "none.srt"), service=FakeService())


class ScopeTests(unittest.TestCase):
    def test_force_ssl_is_required_and_old_tokens_need_re_consent(self):
        self.assertIn(FORCE_SSL, config.YOUTUBE_SCOPES)
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp, "token.pickle")
            token.write_bytes(pickle.dumps(FakeCreds(["https://www.googleapis.com/auth/youtube.upload",
                                                      "https://www.googleapis.com/auth/youtube.readonly"])))
            with patch.object(youtube, "TOKEN_FILE", str(token)), redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(youtube.YouTubeAuthError) as caught:
                    youtube.load_credentials(interactive=False)
        self.assertIn("force-ssl", out.getvalue())
        self.assertIn("test user", str(caught.exception))


class BackfillTests(unittest.TestCase):
    def test_backfill_uses_the_project_and_updates_everything(self):
        from scripts import youtube_backfill
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp, "projects")
            project = root / "ep1"
            project.mkdir(parents=True)
            atomic_write_json(project / "project.json", {"slug": "ep1"})
            (project / "narration.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHi.\n", encoding="utf-8")
            atomic_write_json(project / "script.json", {"title": "T", "tags": ["fiction"]})
            atomic_write_json(project / "story_brief.json", {"logline": "A man needs to be envied."})
            atomic_write_json(project / "scene_timeline.json",
                              build_scene_timeline([{"id": i, "title": f"S{i}"} for i in range(1, 4)], [60, 60, 60]))
            ledger = {"publications": [{"video_id": "w-X", "project": "ep1", "run_dir": str(project)}]}
            service = FakeService()
            with patch("studio.projects.root", return_value=root), \
                    patch("uploader.publications.load_ledger", return_value=ledger):
                dry = youtube_backfill.run("w-X", captions=True, language=True, describe=True, dry_run=True,
                                           use_gemini=False, service=service)
                self.assertEqual(service._videos.calls, [])                     # dry run: nothing on YouTube
                done = youtube_backfill.run("w-X", captions=True, language=True, describe=True,
                                            use_gemini=False, service=service)
        self.assertIn("0:00 S1", dry["description"])
        sent = next(call for name, call in service._videos.calls if name == "update")["body"]["snippet"]
        self.assertEqual(sent["defaultAudioLanguage"], "en")
        self.assertIn("Tell us in the comments.", sent["description"])
        self.assertEqual(done["captions"]["action"], "inserted")

    def test_unknown_video_is_reported(self):
        from scripts import youtube_backfill
        with patch("uploader.publications.load_ledger", return_value={"publications": []}):
            with self.assertRaises(LookupError):
                youtube_backfill.project_for("nope")


if __name__ == "__main__":
    unittest.main()
