"""Phase 2.4 pre-publication human review gate (YouTube client mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json, validate_story_brief
from tests.test_narrative import story_brief
from tests.test_publications import FakeService
from uploader import approval, publications, youtube

CHECKLIST = {key: True for key in approval.CHECKLIST}
GOOD_TITLE = "He Waited Twenty Years to Say It"


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name, "run")
        brief = validate_story_brief(story_brief())
        brief["thumbnail_text"] = "Say it now."
        atomic_write_json(self.run / "story_brief.json", brief)
        atomic_write_json(self.run / "script.json", {"title": GOOD_TITLE, "sections": [],
                                                     "thumbnail_text": "Say it now."})
        self.set_status("ready_for_human_review")
        atomic_write_json(self.run / "asset_manifest.json", {"assets": [
            {"source_url": "https://pixabay.example/1", "license": "Pixabay Content License",
             "license_url": "https://pixabay.com/service/license-summary/"},
        ], "sections": []})
        self.video = self.run / "final_video.mp4"
        self.video.write_bytes(b"\x00" * 2048)
        self.thumb = self.run / "thumbnail.jpg"
        self.thumb.write_bytes(b"\xff\xd8thumb")
        self.meta = {"video_path": str(self.video), "title": GOOD_TITLE, "description": "desc",
                     "tags": ["fiction"], "thumbnail_path": str(self.thumb)}
        self.ledger = Path(self.temp.name, "publications.json")
        self.ledger_patch = patch.object(config, "PUBLICATIONS_FILE", str(self.ledger))
        self.ledger_patch.start()

    def tearDown(self):
        self.ledger_patch.stop()
        self.temp.cleanup()

    def set_status(self, status):
        atomic_write_json(self.run / "narrative_review.json", {"status": status})

    def approve(self, **kwargs):
        options = dict(self.meta)
        options.update(kwargs)
        checklist = options.pop("checklist", CHECKLIST)
        return approval.approve(self.run, checklist=checklist, **options)

    def upload(self, service=None, **overrides):
        meta = dict(self.meta)
        meta.update(overrides)
        return quiet(youtube.upload_run, str(self.run), service=service or FakeService(), **meta)


class ApprovalGateTests(GateTestCase):
    def test_no_approval_refuses_upload_before_any_api_call(self):
        service = FakeService()
        with self.assertRaises(approval.UploadNotApproved) as caught:
            self.upload(service)
        self.assertIn("no review approval", str(caught.exception))
        self.assertEqual(service.videos().insert_calls, [])
        self.assertFalse(self.ledger.exists())

    def test_valid_approval_uploads_and_records_ledger(self):
        self.approve(notes="Watched twice.")
        stored = load_json(self.run / approval.APPROVAL_FILE)
        self.assertTrue(stored["approved_at"])
        self.assertEqual(set(stored["checklist"]), set(approval.CHECKLIST))
        self.assertEqual(set(stored["bindings"]),
                         {"script", "video", "title", "description", "tags", "thumbnail"})
        service = FakeService(inserted_id="vidapprov1")
        record = self.upload(service)
        self.assertEqual(len(service.videos().insert_calls), 1)
        self.assertEqual(len(service.thumbnail_calls), 1)
        self.assertEqual(record["video_id"], "vidapprov1")
        ledger = publications.load_ledger()["publications"]
        self.assertEqual(ledger[0]["state"], "uploaded_pending_publication")
        self.assertEqual(ledger[0]["review"]["approved_at"], stored["approved_at"])

    def test_stale_approval_after_editing_title_is_refused(self):
        self.approve()
        with self.assertRaises(approval.UploadNotApproved) as caught:
            self.upload(title="He Waited Twenty-One Years")
        self.assertIn("title", str(caught.exception))

    def test_any_bound_artifact_change_invalidates(self):
        self.approve()
        atomic_write_json(self.run / "script.json", {"title": GOOD_TITLE, "sections": [1]})
        self.assertIn("script", approval.check_approval(self.run, **self.meta)[0])
        self.approve()
        self.video.write_bytes(b"\x01" * 2048)
        self.assertIn("video", approval.check_approval(self.run, **self.meta)[0])
        self.approve()
        self.assertIn("description", approval.check_approval(
            self.run, **dict(self.meta, description="edited"))[0])
        self.thumb.write_bytes(b"\xff\xd8other")
        self.assertIn("thumbnail", approval.check_approval(self.run, **self.meta)[0])

    def test_incomplete_checklist_is_refused(self):
        partial = dict(CHECKLIST, watched_full_video=False)
        with self.assertRaises(approval.UploadNotApproved) as caught:
            self.approve(checklist=partial)
        self.assertIn("watched_full_video", str(caught.exception))
        self.assertFalse((self.run / approval.APPROVAL_FILE).exists())

    def test_blocked_status_without_override_is_refused(self):
        for status in ("revision_required", "structurally_invalid"):
            with self.subTest(status=status):
                self.set_status(status)
                with self.assertRaises(approval.UploadNotApproved) as caught:
                    self.approve()
                self.assertIn(f"narrative status is {status}", str(caught.exception))
                with self.assertRaises(approval.UploadNotApproved):
                    self.approve(status_override_reason="ok")  # too short

    def test_blocked_status_with_override_reason_is_allowed_and_recorded(self):
        self.set_status("revision_required")
        reason = "Remaining findings are stylistic; reviewed every scene."
        self.approve(status_override_reason=reason)
        stored = load_json(self.run / approval.APPROVAL_FILE)
        self.assertEqual(stored["status_override_reason"], reason)
        self.upload(FakeService(inserted_id="vidoverr01"))
        record = publications.load_ledger()["publications"][0]
        self.assertEqual(record["review"]["status_override_reason"], reason)
        self.assertEqual(record["review"]["narrative_status"], "revision_required")

    def test_status_change_after_approval_invalidates(self):
        self.approve()
        self.set_status("revision_required")
        problems = approval.check_approval(self.run, **self.meta)
        self.assertIn("narrative status changed", problems[0])

    def test_human_review_statuses_need_only_the_checklist(self):
        for status in ("human_review_required", "ready_for_human_review"):
            self.set_status(status)
            self.approve()
            self.assertEqual(approval.check_approval(self.run, **self.meta), [])

    def test_failing_title_needs_written_override(self):
        bad = "The Anatomy of Regret"
        with self.assertRaises(approval.UploadNotApproved) as caught:
            self.approve(title=bad)
        self.assertIn("title policy", str(caught.exception))
        self.approve(title=bad, title_override_reason="Series naming agreed with the editor.")
        stored = load_json(self.run / approval.APPROVAL_FILE)
        self.assertEqual(stored["title_override_reason"], "Series naming agreed with the editor.")

    def test_license_flags_are_reported(self):
        atomic_write_json(self.run / "asset_manifest.json", {"assets": [
            {"source_url": "https://x.example/2", "license": ""},
        ], "sections": [{"section_id": 3, "used_rendering_fallback": True}]})
        summary = approval.review_summary(self.run, **self.meta)
        self.assertEqual(len(summary["license_flags"]), 2)
        self.assertFalse(summary["approval_valid"])


class SyntheticDisclosureTests(GateTestCase):
    def mark_synthetic(self):
        manifest = load_json(self.run / "asset_manifest.json")
        manifest.update({"synthetic_media": True, "generated_assets": [
            {"job_id": "s01_01", "provider": "comfyui", "synthetic": True}]})
        atomic_write_json(self.run / "asset_manifest.json", manifest)

    def test_stock_only_run_keeps_base_checklist_and_flag_false(self):
        self.assertNotIn("synthetic_media_disclosed", approval.checklist_for(self.run))
        self.approve()
        service = FakeService(inserted_id="vidplain01")
        self.upload(service)
        self.assertFalse(service.videos().insert_calls[0]["body"]["status"]["containsSyntheticMedia"])

    def test_generated_images_require_disclosure_item_and_set_the_flag(self):
        self.mark_synthetic()
        summary = approval.review_summary(self.run, **self.meta)
        self.assertTrue(summary["synthetic_media"])
        self.assertIn("synthetic_media_disclosed", summary["checklist"])
        with self.assertRaises(approval.UploadNotApproved) as caught:
            self.approve()                                   # base checklist only
        self.assertIn("synthetic_media_disclosed", str(caught.exception))
        self.approve(checklist={**CHECKLIST, "synthetic_media_disclosed": True})
        service = FakeService(inserted_id="vidsynth01")
        self.upload(service)
        self.assertTrue(service.videos().insert_calls[0]["body"]["status"]["containsSyntheticMedia"])

    def test_old_approval_is_invalid_once_synthetic_images_are_added(self):
        self.approve()
        self.mark_synthetic()
        self.assertTrue(approval.check_approval(self.run, **self.meta))


class GuiGateTests(GateTestCase):
    def setUp(self):
        super().setUp()
        import gui
        self.gui = gui
        self.patches = [
            patch.object(config, "OUTPUT_DIR", str(self.run)),
            patch.dict(gui._state, {"video_path": str(self.video), "thumb_path": str(self.thumb),
                                    "description": "desc", "uploading": False,
                                    "script": {"title": GOOD_TITLE, "tags": ["fiction"]}}),
        ]
        for item in self.patches:
            item.start()
        self.client = gui.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def test_upload_route_refuses_without_approval(self):
        with patch.object(youtube, "upload_video") as uploaded:
            response = self.client.post("/api/upload", json={"privacy": "public"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("no review approval", response.get_json()["error"])
        uploaded.assert_not_called()

    def test_gate_and_approval_routes(self):
        gate = self.client.get("/api/review-gate").get_json()
        self.assertTrue(gate["ok"])
        self.assertFalse(gate["approval_valid"])
        self.assertEqual(set(gate["checklist"]), set(approval.CHECKLIST))
        refused = self.client.post("/api/approve-upload", json={"checklist": {}})
        self.assertEqual(refused.status_code, 400)
        accepted = self.client.post("/api/approve-upload", json={"checklist": CHECKLIST})
        self.assertTrue(accepted.get_json()["ok"])
        self.assertTrue(self.client.get("/api/review-gate").get_json()["approval_valid"])
        # Editing the title through the GUI invalidates the approval.
        with patch.dict(self.gui._state["script"], {"title": "He Lied Twice"}):
            self.assertFalse(self.client.get("/api/review-gate").get_json()["approval_valid"])


class PipelineDashboardTests(GateTestCase):
    def test_dashboard_writes_the_same_approval(self):
        from review import app as review_app
        with patch.dict(review_app._state, {"run_dir": str(self.run), "upload": self.meta,
                                            "review_data": {}, "decision": None}):
            client = review_app.app.test_client()
            data = client.get("/api/data").get_json()
            self.assertIn("gate", data)
            refused = client.post("/api/approve", json={"checklist": {}})
            self.assertEqual(refused.status_code, 400)
            self.assertIsNone(review_app._state["decision"])
            approved = client.post("/api/approve", json={"checklist": CHECKLIST})
            self.assertEqual(approved.get_json()["status"], "approved")
            self.assertEqual(review_app._state["decision"], "approved")
        self.assertEqual(approval.check_approval(self.run, **self.meta), [])
        review_app._decided.clear()

    def test_pipeline_description_matches_upload(self):
        script = {"title": GOOD_TITLE, "description": "Story.", "tags": ["a", "b"]}
        description = youtube.build_upload_description(script)
        self.assertIn("#a #b", description)
        self.assertIn(config.CHANNEL_NAME, description)


if __name__ == "__main__":
    unittest.main()
