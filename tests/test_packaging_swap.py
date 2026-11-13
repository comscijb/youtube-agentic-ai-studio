"""Phase 4.0-C: analytics status and the packaging-swap loop. The YouTube APIs are mocked."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import analytics, packaging_swap, publications

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def stats(impressions, ctr, days=None):
    daily = days or [{"day": "2026-11-08", "impressions": impressions, "ctr": ctr}]
    return {"reach": {"impressions": impressions, "ctr": ctr, "daily": daily}}


def record(video_id="v1", published="2026-11-08T12:00:00+00:00", **extra):
    return {"video_id": video_id, "state": "published", "published_at": published, "project": "slug", "title": "Old Title", **extra}


class RuleTests(unittest.TestCase):
    SETTINGS = {"hours": 72, "min_impressions": 300, "fallback_ctr": 0.03}

    def test_due_after_72_hours_with_enough_impressions_and_a_low_ctr(self):
        verdict = packaging_swap.evaluate(record(), stats(500, 0.02), {"v1": stats(500, 0.02)}, now=NOW, settings=self.SETTINGS)
        self.assertTrue(verdict["due"])
        self.assertEqual(verdict["threshold"], 0.03)                     # no median yet: 3 %

    def test_not_due_too_early_too_few_impressions_or_ctr_fine(self):
        early = packaging_swap.evaluate(record(published="2026-11-11T00:00:00+00:00"), stats(500, 0.01), {}, now=NOW, settings=self.SETTINGS)
        self.assertFalse(early["due"])
        self.assertIn("72 h", early["reason"])
        few = packaging_swap.evaluate(record(), stats(299, 0.01), {}, now=NOW, settings=self.SETTINGS)
        self.assertFalse(few["due"])
        fine = packaging_swap.evaluate(record(), stats(500, 0.05), {}, now=NOW, settings=self.SETTINGS)
        self.assertFalse(fine["due"])

    def test_the_channel_median_replaces_the_fallback(self):
        others = {"a": stats(400, 0.06), "b": stats(800, 0.04), "c": stats(100, 0.01), "v1": stats(500, 0.045)}
        self.assertEqual(packaging_swap.channel_median(others, "v1", 300), 0.05)       # c has too few impressions
        verdict = packaging_swap.evaluate(record(), stats(500, 0.045), others, now=NOW, settings=self.SETTINGS)
        self.assertTrue(verdict["due"])                                  # 4.5 % < median 5 %
        self.assertIsNone(packaging_swap.channel_median({"a": stats(400, 0.06)}, "v1", 300))

    def test_after_a_swap_only_the_new_window_counts(self):
        days = [{"day": "2026-11-05", "impressions": 1000, "ctr": 0.01}, {"day": "2026-11-09", "impressions": 100, "ctr": 0.01}]
        swapped = record(packaging_swaps=[{"kind": "thumbnail", "at": "2026-11-09T00:00:00+00:00"}])
        verdict = packaging_swap.evaluate(swapped, stats(1100, 0.01, days), {}, now=NOW, settings=self.SETTINGS)
        self.assertEqual(verdict["impressions"], 100)
        self.assertFalse(verdict["due"])


class StepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        project = Path(config.PROJECTS_DIR) / "slug"
        (project / "thumbnails").mkdir(parents=True, exist_ok=True)
        for number in (1, 2, 3):
            (project / "thumbnails" / f"variant_{number}.jpg").write_bytes(b"jpg")
        atomic_write_json(project / "thumbnails" / "thumbnails.json", {"selected": 1, "variants": [
            {"number": number, "path": f"thumbnails\\variant_{number}.jpg", "concept": {"text": f"text {number}"}} for number in (1, 2, 3)]})
        atomic_write_json(project / "titles.json", {"selected": "Old Title", "candidates": [
            {"title": "Old Title", "passes": True}, {"title": "Bad Thesis", "passes": False}, {"title": "New Title", "passes": True}]})

    def tearDown(self):
        self.temp.cleanup()

    def test_thumbnail_first_then_the_title(self):
        first = packaging_swap.next_step(record(), slug="slug")
        self.assertEqual((first["kind"], first["from"], first["to"]), ("thumbnail", "thumbnails/variant_1.jpg", "thumbnails/variant_2.jpg"))
        second = packaging_swap.next_step(record(packaging_swaps=[{"kind": "thumbnail", "to": "thumbnails/variant_2.jpg"}]), slug="slug")
        self.assertEqual((second["kind"], second["to"]), ("title", "New Title"))
        done = record(packaging_swaps=[{"kind": "thumbnail", "to": "x"}, {"kind": "title", "from": "Old Title", "to": "New Title"}])
        self.assertIsNone(packaging_swap.next_step(done, slug="slug"))

    def test_apply_sets_the_thumbnail_and_logs_it(self):
        atomic_write_json(config.PUBLICATIONS_FILE, {"schema_version": 1, "publications": [record()]})
        atomic_write_json(Path(analytics.folder()) / "v1.json", stats(500, 0.02))
        service = MagicMock()
        with patch("googleapiclient.http.MediaIoBaseUpload", MagicMock()):
            entry = packaging_swap.apply("v1", "thumbnail", service=service, now=NOW)
        service.thumbnails().set.assert_called()
        self.assertEqual(service.thumbnails().set.call_args.kwargs["videoId"], "v1")
        saved = publications.load_ledger()["publications"][0]["packaging_swaps"]
        self.assertEqual(saved[0]["to"], "thumbnails/variant_2.jpg")
        self.assertEqual(saved[0]["at"], "2026-11-12T12:00:00+00:00")
        self.assertEqual(saved[0]["before"], {"impressions": 500, "ctr": 0.02})
        self.assertEqual(entry["kind"], "thumbnail")
        with self.assertRaises(ValueError):
            packaging_swap.apply("v1", "thumbnail", service=service, now=NOW)       # already swapped: next is the title

    def test_a_title_swap_sends_the_whole_snippet_back(self):
        service = MagicMock()
        service.videos().list().execute.return_value = {"items": [{"snippet": {
            "title": "Old Title", "description": "d", "tags": ["t"], "categoryId": "24", "defaultLanguage": "en"}}]}
        snippet = packaging_swap.set_title(service, "v1", "New Title")
        body = service.videos().update.call_args.kwargs["body"]
        self.assertEqual(body, {"id": "v1", "snippet": snippet})
        self.assertEqual((snippet["title"], snippet["description"], snippet["defaultLanguage"]), ("New Title", "d", "en"))

    def test_test_compare_export(self):
        atomic_write_json(config.PUBLICATIONS_FILE, {"schema_version": 1, "publications": [record()]})
        with tempfile.TemporaryDirectory() as out:
            folder = packaging_swap.export_test_compare("v1", out_root=out)
            self.assertEqual(sorted(path.name for path in folder.iterdir()),
                             ["thumbnail_1.jpg", "thumbnail_2.jpg", "thumbnail_3.jpg", "titles.txt"])
            self.assertIn("New Title", (folder / "titles.txt").read_text(encoding="utf-8"))


class StatusTests(unittest.TestCase):
    def test_a_dead_token_leaves_a_status_with_the_reason(self):
        from uploader.youtube import YouTubeAuthError
        atomic_write_json(config.PUBLICATIONS_FILE, {"schema_version": 1, "publications": [record()]})
        with patch.object(analytics, "services", side_effect=YouTubeAuthError("expired")):
            with self.assertRaises(YouTubeAuthError):
                analytics.refresh_with_status(force=True)
        state = analytics.status()
        self.assertTrue(state["auth_required"])
        self.assertIn("7 dias", state["error"])
        self.assertIsNone(state["last_success"])
        self.assertTrue(os.path.isfile(Path(analytics.folder()) / "status.json"))

    def test_a_good_refresh_clears_the_error(self):
        atomic_write_json(config.PUBLICATIONS_FILE, {"schema_version": 1, "publications": [record()]})
        with patch.object(analytics, "refresh_all", return_value=[{"video_id": "v1"}]):
            done = analytics.refresh_with_status()
        self.assertEqual(done["status"]["videos"], 1)
        self.assertEqual(done["status"]["error"], "")
        self.assertIsNotNone(done["status"]["last_success"])


if __name__ == "__main__":
    unittest.main()
