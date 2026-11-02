"""Phase 3.9-F: one synthetic-content decision per episode — Shorts inherit it, the ledger records it,
older records are backfilled, and an override needs a typed reason (YouTube mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shorts_upload import GOOD_TITLE, NOW, SequentialVideos, make_run
from tests.test_youtube_captions import FakeService
from uploader import approval, publications, shorts_upload

ILLUSTRATION = {"synthetic_media": True, "generated_assets": [
    {"job_id": "s01_01", "kind": "shot", "synthetic": True, "visual_style": "dark_illustration"}]}


def meta(folder: Path) -> dict:
    return dict(video_path=str(folder / "final_video.mp4"), title=GOOD_TITLE, description="d", tags=[],
                thumbnail_path=None)


class DecisionTests(unittest.TestCase):
    def test_rule_and_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            self.assertEqual(approval.disclosure_decision(run)["value"], False)
            atomic_write_json(run / "asset_manifest.json", ILLUSTRATION)
            decision = approval.disclosure_decision(run)
            self.assertFalse(decision["value"])
            self.assertIn("stylised", decision["reason"])
            flagged = json.loads(json.dumps(ILLUSTRATION))
            flagged["generated_assets"].append({"job_id": "thumb_1", "kind": "thumbnail", "synthetic": True,
                                                "visual_style": "dark_illustration", "realistic": True})
            decision = approval.disclosure_decision(run, manifest=flagged)
            self.assertTrue(decision["value"])
            self.assertIn("thumbnail", decision["reason"])

    def test_a_cloned_voice_without_clean_provenance_is_disclosed(self):
        from audio.tts import voices
        with tempfile.TemporaryDirectory() as temp, patch.object(voices, "folder", return_value=Path(temp, "voice")):
            run = Path(temp, "run")
            atomic_write_json(run / "asset_manifest.json", ILLUSTRATION)
            atomic_write_json(run / "narration_words.json", {"words": [], "voice": {"backend": "local", "reference": "me.wav"}})
            self.assertTrue(approval.disclosure_decision(run)["value"])
            voices.save_reference(b"RIFF", "me.wav", speaker="the author", licence="own voice", own_voice=True,
                                  not_public_person=True)
            self.assertFalse(approval.disclosure_decision(run)["value"])


class OverrideTests(unittest.TestCase):
    def test_differing_from_the_rule_needs_a_typed_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            make_run(folder)
            atomic_write_json(folder / "asset_manifest.json", ILLUSTRATION)
            checklist = {key: True for key in approval.checklist_for(folder)}
            with self.assertRaises(approval.UploadNotApproved) as refused:
                approval.approve(folder, checklist=checklist, synthetic_disclosure=True, **meta(folder))
            self.assertIn("override reason", " ".join(refused.exception.reasons))
            record = approval.approve(folder, checklist=checklist, synthetic_disclosure=True,
                                      disclosure_override_reason="the faces read as photographs", **meta(folder))
            self.assertTrue(record["synthetic_disclosure"])
            self.assertEqual(record["synthetic_disclosure_reason"], "reviewer override: the faces read as photographs")
            plain = approval.approve(folder, checklist=checklist, **meta(folder))
            summary = approval.review_summary(folder, **meta(folder))
        self.assertFalse(plain["synthetic_disclosure"])
        self.assertIn("stylised", plain["synthetic_disclosure_reason"])
        self.assertIn("stylised", summary["synthetic_disclosure_reason"])

    def test_gui_passes_the_reason(self):
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        self.assertIn("disclosure_override_reason", page)
        self.assertIn("gate-disclosure-decision", page)


class InheritanceTests(unittest.TestCase):
    def test_shorts_use_the_episode_decision_and_the_ledger_records_it(self):
        from uploader import youtube
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp, "ep1")
            folder.mkdir()
            make_run(folder, count=2)
            atomic_write_json(folder / "asset_manifest.json", ILLUSTRATION)
            ledger = Path(temp, "publications.json")
            approval.approve(folder, checklist={key: True for key in approval.checklist_for(folder)},
                             synthetic_disclosure=True, disclosure_override_reason="reviewer prefers to disclose",
                             **meta(folder))
            service = FakeService()
            service._videos = SequentialVideos(["episode1", "shortA", "shortB"])
            original = shorts_upload.schedule
            fixed = lambda count, **kwargs: original(count, now=NOW, episode_publish_at=kwargs.get("episode_publish_at", ""))
            with patch.object(config, "PUBLICATIONS_FILE", str(ledger)), \
                    patch.object(shorts_upload, "schedule", side_effect=fixed), \
                    patch("studio.projects.touch"), redirect_stdout(io.StringIO()):
                youtube.upload_run(folder, service=service, privacy="public", **meta(folder))
            saved = json.loads(ledger.read_text(encoding="utf-8"))["publications"]
        inserts = [call for name, call in service._videos.calls if name == "insert"]
        self.assertEqual([call["body"]["status"]["containsSyntheticMedia"] for call in inserts], [True, True, True])
        self.assertEqual(len(saved), 3)
        for record in saved:
            self.assertTrue(record["contains_synthetic_media"])
            self.assertEqual(record["synthetic_disclosure_reason"], "reviewer override: reviewer prefers to disclose")


class BackfillTests(unittest.TestCase):
    def test_older_records_get_the_archived_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp, "archive")
            atomic_write_json(root / "ep-one" / "text" / approval.APPROVAL_FILE,
                              {"synthetic_media": True, "synthetic_disclosure": False})
            atomic_write_json(root / "ep-one" / "manifests" / "asset_manifest.json", ILLUSTRATION)
            ledger = Path(temp, "publications.json")
            atomic_write_json(ledger, {"publications": [
                {"video_id": "ep", "run_dir": str(Path(temp, "gone")), "project": "ep-one"},
                {"video_id": "sh", "kind": "short", "run_dir": str(Path(temp, "gone")), "project": "ep-one"},
                {"video_id": "new", "contains_synthetic_media": True, "synthetic_disclosure_reason": "x"},
                {"video_id": "lost", "run_dir": str(Path(temp, "gone")), "project": "unknown"}]})
            dry = publications.backfill_disclosure(ledger, archive_root=root, dry_run=True)
            self.assertNotIn("contains_synthetic_media", load_json(ledger)["publications"][0])
            rows = publications.backfill_disclosure(ledger, archive_root=root)
            saved = load_json(ledger)["publications"]
        self.assertEqual(dry, rows)
        self.assertEqual([row["action"] for row in rows], ["backfilled", "backfilled", "kept", "missing"])
        self.assertEqual([item.get("contains_synthetic_media") for item in saved], [False, False, True, None])
        self.assertIn("stylised", saved[0]["synthetic_disclosure_reason"])


if __name__ == "__main__":
    unittest.main()
