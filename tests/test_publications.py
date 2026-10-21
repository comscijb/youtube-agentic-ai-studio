"""Phase 2.3 publication ledger, sync and banned topics (YouTube client mocked)."""
import io
import json
import pickle
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from agents import researcher
from agents.narrative import atomic_write_json, validate_story_brief
from scripts import register_publication
from tests.test_narrative import story_brief
from uploader import publications, youtube


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result

    def next_chunk(self):
        return SimpleNamespace(progress=lambda: 0.5), self.result


class FakeVideos:
    def __init__(self, items=None, inserted_id="vid000001"):
        self.items = {item["id"]: item for item in items or []}
        self.list_calls = []
        self.insert_calls = []
        self.inserted_id = inserted_id

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        ids = kwargs["id"].split(",")
        return FakeRequest({"items": [self.items[i] for i in ids if i in self.items]})

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return FakeRequest({"id": self.inserted_id})


class FakeService:
    def __init__(self, items=None, **kwargs):
        self._videos = FakeVideos(items, **kwargs)
        self.thumbnail_calls = []

    def videos(self):
        return self._videos

    def thumbnails(self):
        service = self

        class Thumbs:
            def set(self, **kwargs):
                service.thumbnail_calls.append(kwargs)
                return FakeRequest({})
        return Thumbs()


def video(video_id, privacy="public", upload="processed", publish_at=None):
    status = {"privacyStatus": privacy, "uploadStatus": upload}
    if publish_at:
        status["publishAt"] = publish_at
    return {"id": video_id, "status": status, "snippet": {"title": "t"}}


class FakeCreds:
    """Picklable stand-in for google.oauth2 credentials."""

    def __init__(self, scopes, valid=True):
        self.scopes = list(scopes)
        self.valid = valid
        self.expired = not valid
        self.refresh_token = None

    def has_scopes(self, scopes):
        return set(scopes).issubset(self.scopes)


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger = root / "data" / "publications.json"
        self.banned = root / "banned_topics.txt"
        self.banned.write_text("# comment\nA legacy plain premise\n", encoding="utf-8")
        self.run_dir = root / "run"
        brief = validate_story_brief(story_brief())
        brief.update(requested_premise="The Man Who Needed Everyone to Envy Him",
                     psychological_theme="envy",
                     psychological_angle="needing to be envied as a substitute for self-worth",
                     thumbnail_text="I hope he loses.")
        atomic_write_json(self.run_dir / "story_brief.json", brief)
        atomic_write_json(self.run_dir / "script.json", {"title": "He Needed Them to Envy Him",
                                                         "sections": []})
        (self.run_dir / "final_video.mp4").write_bytes(b"\x00" * 1024)
        self.patches = [
            patch.object(config, "PUBLICATIONS_FILE", str(self.ledger)),
            patch.object(researcher, "_BANNED_TOPICS_FILE", str(self.banned)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def register(self, video_id, **kwargs):
        return publications.record_upload(video_id, self.run_dir, **kwargs)

    def state(self, video_id):
        return next(item for item in publications.load_ledger()["publications"]
                    if item["video_id"] == video_id)

    def banned_lines(self):
        return [line for line in self.banned.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")]


class SyncTests(LedgerTestCase):
    def test_record_contains_required_fields(self):
        record = self.register("vid000001", title="He Needed Them to Envy Him")
        for field in ("video_id", "url", "run_dir", "uploaded_at", "scheduled_publish_at", "title",
                      "thumbnail_text", "premise", "logline", "psychological_theme",
                      "psychological_angle", "narrative_type", "central_contradiction",
                      "source_hashes", "state", "state_checked_at", "published_at"):
            self.assertIn(field, record)
        self.assertEqual(record["state"], "uploaded_pending_publication")
        self.assertEqual(record["premise"], "The Man Who Needed Everyone to Envy Him")
        self.assertEqual(len(record["source_hashes"]["video"]), 64)
        self.assertIsNotNone(record["source_hashes"]["script"])

    def test_public_processed_bans_once_and_resync_is_idempotent(self):
        self.register("vid000001")
        service = FakeService([video("vid000001")])
        summary = publications.sync_publications(service)
        self.assertEqual(summary["published"], ["vid000001"])
        record = self.state("vid000001")
        self.assertEqual(record["state"], "published")
        self.assertTrue(record["published_at"])
        lines = self.banned_lines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("theme: envy | angle: needing to be envied"))
        self.assertIn("video: vid000001", lines[1])
        self.assertIn("premise: The Man Who Needed Everyone to Envy Him", lines[1])
        # Re-sync: nothing pending, no new call, no new line.
        self.assertEqual(publications.sync_publications(service)["checked"], 0)
        # Even a forced re-check of the same video never duplicates its line.
        ledger = publications.load_ledger()
        ledger["publications"][0]["state"] = "uploaded_pending_publication"
        publications.save_ledger(ledger)
        publications.sync_publications(service)
        self.assertEqual(len(self.banned_lines()), 2)

    def test_not_yet_public_stays_pending(self):
        cases = {
            "vidprivate1": video("vidprivate1", privacy="private"),
            "vidunlisted": video("vidunlisted", privacy="unlisted"),
            "vidprocess1": video("vidprocess1", upload="uploaded"),
            "vidschedule": video("vidschedule", privacy="private",
                                 publish_at="2099-01-01T12:00:00Z"),
        }
        for video_id in cases:
            self.register(video_id)
        summary = publications.sync_publications(FakeService(list(cases.values())))
        self.assertEqual(sorted(summary["pending"]), sorted(cases))
        for video_id in cases:
            self.assertEqual(self.state(video_id)["state"], "uploaded_pending_publication")
            self.assertTrue(self.state(video_id)["state_checked_at"])
        self.assertEqual(self.banned_lines(), ["A legacy plain premise"])

    def test_failed_rejected_deleted_or_missing_are_never_banned(self):
        for video_id in ("vidfailed01", "vidreject01", "viddelete01", "vidmissing1"):
            self.register(video_id)
        service = FakeService([
            video("vidfailed01", upload="failed"), video("vidreject01", upload="rejected"),
            video("viddelete01", upload="deleted"),
        ])
        summary = publications.sync_publications(service)
        self.assertEqual(sorted(summary["failed"]), ["vidfailed01", "vidreject01"])
        self.assertEqual(sorted(summary["removed"]), ["viddelete01", "vidmissing1"])
        self.assertEqual(self.banned_lines(), ["A legacy plain premise"])

    def test_ids_are_requested_in_batches_of_50(self):
        for index in range(51):
            self.register(f"vid{index:06d}")
        service = FakeService([])
        publications.sync_publications(service)
        calls = service.videos().list_calls
        self.assertEqual([len(call["id"].split(",")) for call in calls], [50, 1])
        self.assertEqual(calls[0]["part"], "status,snippet")

    def test_sync_failure_does_not_raise_into_generation(self):
        self.register("vid000001")
        payload = story_brief()
        payload["premise_candidates"] = [dict(payload["premise_candidates"][0], premise=f"P{i}")
                                         for i in range(4)]
        with tempfile.TemporaryDirectory() as out, patch.object(config, "OUTPUT_DIR", out), \
             patch.object(youtube, "get_service", side_effect=youtube.YouTubeAuthError("no token")), \
             patch.object(researcher, "generate", return_value=json.dumps(payload)):
            output = io.StringIO()
            with redirect_stdout(output):
                brief = researcher.research_topic("channel", target_duration_minutes=12)
        self.assertIn("publication sync skipped", output.getvalue())
        self.assertTrue(brief["video_title"])
        self.assertEqual(self.state("vid000001")["state"], "uploaded_pending_publication")


class BannedTopicTests(LedgerTestCase):
    def test_plain_and_structured_lines_load(self):
        researcher.append_banned_entry({"theme": "envy", "angle": "being envied", "premise": "P",
                                        "title": "T | x", "video": "vid1", "published": "2026-10-21"})
        entries = researcher.load_banned_topics()
        self.assertEqual(entries[0], {"raw": "A legacy plain premise",
                                      "premise": "A legacy plain premise"})
        self.assertEqual(entries[1]["theme"], "envy")
        self.assertEqual(entries[1]["angle"], "being envied")
        self.assertEqual(entries[1]["title"], "T / x")
        self.assertFalse(researcher.append_banned_entry({"premise": "Other", "video": "vid1"}))

    def test_prompt_states_angle_rule_and_lists_entries(self):
        researcher.append_banned_entry({"theme": "envy", "angle": "being envied", "premise": "P1",
                                        "video": "vid1"})
        payload = story_brief()
        payload["premise_candidates"] = [dict(payload["premise_candidates"][0], premise=f"P{i}")
                                         for i in range(4)]
        with tempfile.TemporaryDirectory() as out, patch.object(config, "OUTPUT_DIR", out), \
             patch.object(researcher, "generate", return_value=json.dumps(payload)) as generated:
            quiet(researcher.research_topic, "channel", target_duration_minutes=12)
        prompt = generated.call_args_list[0].args[0]
        self.assertIn(researcher.BANNED_RULE, prompt)
        self.assertIn('"angle":"being envied"', prompt)
        self.assertIn('"premise":"A legacy plain premise"', prompt)
        self.assertIn("psychological_angle", prompt)

    def test_exact_premise_matching_published_entry_warns_without_blocking(self):
        researcher.append_banned_entry({"theme": "regret", "premise": "My Exact Premise!",
                                        "video": "vid2"})
        with tempfile.TemporaryDirectory() as out, patch.object(config, "OUTPUT_DIR", out), \
             patch.object(researcher, "generate", return_value=json.dumps(story_brief())):
            output = io.StringIO()
            with redirect_stdout(output):
                brief = researcher.research_topic("channel", topic_override="my exact premise",
                                                  target_duration_minutes=12)
        self.assertIn("matches an already published topic", output.getvalue())
        self.assertEqual(len(brief["published_topic_warning"]), 1)

    def test_missing_angle_is_repaired_for_new_briefs(self):
        payload = story_brief()
        del payload["psychological_angle"]
        with tempfile.TemporaryDirectory() as out, patch.object(config, "OUTPUT_DIR", out), \
             patch.object(researcher, "generate",
                          side_effect=[json.dumps(payload), json.dumps(story_brief())]) as generated:
            brief = quiet(researcher.research_topic, "channel", topic_override="P",
                          target_duration_minutes=12)
        self.assertIn("psychological_angle", generated.call_args_list[1].args[0])
        self.assertTrue(brief["psychological_angle"])
        # Old briefs without the field still validate.
        validate_story_brief(payload)

    def test_gui_banned_topics_route_keeps_raw_lines(self):
        import gui
        response = gui.app.test_client().get("/api/banned-topics").get_json()
        self.assertEqual(response["topics"], ["A legacy plain premise"])
        self.assertEqual(response["entries"][0]["premise"], "A legacy plain premise")


class UploadAndRegistrationTests(LedgerTestCase):
    def test_upload_run_uploads_then_records_pending(self):
        service = FakeService(inserted_id="vidupload1")
        progress = []
        # The review gate is covered by tests/test_approval.py.
        with patch("uploader.approval.require_approval", return_value={}):
            record = quiet(youtube.upload_run, str(self.run_dir),
                           video_path=str(self.run_dir / "final_video.mp4"), title="He Waited",
                           description="d", tags=["a"], thumbnail_path=None,
                           progress=progress.append, service=service,
                           publish_at="2099-01-01T12:00:00Z")
        body = service.videos().insert_calls[0]["body"]
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], "2099-01-01T12:00:00Z")
        self.assertEqual(record["video_id"], "vidupload1")
        self.assertEqual(record["scheduled_publish_at"], "2099-01-01T12:00:00Z")
        self.assertEqual(self.state("vidupload1")["state"], "uploaded_pending_publication")
        self.assertEqual(progress[-1], 100)

    def test_default_privacy_is_public(self):
        service = FakeService()
        quiet(youtube.upload_video, str(self.run_dir / "final_video.mp4"), title="t",
              description="d", service=service)
        self.assertEqual(service.videos().insert_calls[0]["body"]["status"]["privacyStatus"],
                         config.VIDEO_PRIVACY)
        self.assertEqual(config.VIDEO_PRIVACY, "public")

    def test_manual_registration_reads_run_artifacts(self):
        quiet(register_publication.main, ["--video-id", "vidmanual1", "--run-dir", str(self.run_dir)])
        record = self.state("vidmanual1")
        self.assertEqual(record["title"], "He Needed Them to Envy Him")
        self.assertEqual(record["psychological_angle"],
                         "needing to be envied as a substitute for self-worth")
        self.assertEqual(record["premise"], "The Man Who Needed Everyone to Envy Him")
        self.assertTrue(record["registered_manually"])
        quiet(register_publication.main, ["--video-id", "vidmanual1", "--run-dir", str(self.run_dir)])
        self.assertEqual(len(publications.load_ledger()["publications"]), 1)

    def test_manual_registration_rejects_urls(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            register_publication.main(["--video-id", "https://youtu.be/x", "--run-dir",
                                       str(self.run_dir)])


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token = Path(self.temp.name, "token.pickle")
        self.secret = Path(self.temp.name, "client_secret.json")
        self.secret.write_text("{}", encoding="utf-8")
        self.patches = [patch.object(youtube, "TOKEN_FILE", str(self.token)),
                        patch.object(config, "YOUTUBE_CLIENT_SECRET", str(self.secret))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def store(self, creds):
        self.token.write_bytes(pickle.dumps(creds))

    def test_scopes_include_readonly_for_videos_list(self):
        self.assertIn("https://www.googleapis.com/auth/youtube.readonly", config.YOUTUBE_SCOPES)
        self.assertIn("https://www.googleapis.com/auth/youtube.upload", config.YOUTUBE_SCOPES)

    def test_token_missing_scope_requires_reconsent(self):
        self.store(FakeCreds(["https://www.googleapis.com/auth/youtube.upload"]))
        with self.assertRaises(youtube.YouTubeAuthError):
            quiet(youtube.load_credentials, interactive=False)
        fresh = FakeCreds(config.YOUTUBE_SCOPES)
        flow = SimpleNamespace(run_local_server=lambda port: fresh)
        with patch.object(youtube.InstalledAppFlow, "from_client_secrets_file",
                          return_value=flow) as factory:
            output = io.StringIO()
            with redirect_stdout(output):
                creds = youtube.load_credentials(interactive=True)
        self.assertIn("lacks required scope", output.getvalue())
        self.assertIn("youtube.readonly", output.getvalue())
        self.assertEqual(factory.call_args.args[1], config.YOUTUBE_SCOPES)
        self.assertIs(creds, fresh)
        self.assertTrue(pickle.loads(self.token.read_bytes()).has_scopes(config.YOUTUBE_SCOPES))

    def test_token_with_all_scopes_is_reused(self):
        self.store(FakeCreds(config.YOUTUBE_SCOPES))
        with patch.object(youtube.InstalledAppFlow, "from_client_secrets_file") as factory:
            creds = youtube.load_credentials(interactive=False)
        factory.assert_not_called()
        self.assertTrue(creds.valid)


if __name__ == "__main__":
    unittest.main()
