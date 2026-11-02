"""Phase 3.6-E: analytics parsing, reach reports, flags, caching and the GUI tab (Google APIs mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import config
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_publications import FakeRequest
from uploader import analytics


def response(names, rows):
    return {"columnHeaders": [{"name": name} for name in names], "rows": rows}


class FakeReports:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        dimension = kwargs.get("dimensions")
        if dimension is None:
            return FakeRequest(response(["views", "estimatedMinutesWatched", "averageViewDuration",
                                         "averageViewPercentage", "subscribersGained"], [[400, 900, 135, 22.5, 3]]))
        if dimension == "country" and "country==US" in kwargs["filters"]:
            return FakeRequest(response(["country", "views"], [["US", 100]]))
        if dimension == "country":
            return FakeRequest(response(["country", "views"], [["US", 100], ["BR", 80], ["GB", 40]]))
        if dimension == "insightTrafficSourceType":
            return FakeRequest(response(["insightTrafficSourceType", "views", "estimatedMinutesWatched"],
                                        [["BROWSE", 300, 600], ["YT_SEARCH", 100, 300]]))
        return FakeRequest(response(["day", "views", "averageViewPercentage", "subscribersGained"],
                                    [["2026-10-27", 150, 20.0, 1], ["2026-10-28", 250, 24.0, 2]]))


class FakeAnalytics:
    def __init__(self):
        self._reports = FakeReports()

    def reports(self):
        return self._reports


class FakeReporting:
    def __init__(self, jobs=None, reports=None):
        self.created, self.listed_jobs = [], 0
        self._jobs, self._reports = list(jobs or []), list(reports or [])
        outer = self

        class Reports:
            def list(self, jobId):
                return FakeRequest({"reports": outer._reports})

        class Jobs:
            def list(self):
                outer.listed_jobs += 1
                return FakeRequest({"jobs": outer._jobs})

            def create(self, body):
                outer.created.append(body)
                return FakeRequest({"id": "job1", "reportTypeId": body["reportTypeId"]})

            def reports(self):
                return Reports()
        self._jobs_api = Jobs()

    def jobs(self):
        return self._jobs_api


CSV_DAY1 = ("date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
            "20261027,UC1,w-X,800,0.01\n20261027,UC1,other,50,0.2\n")
CSV_DAY2 = ("date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
            "20261028,UC1,w-X,1200,0.015\n")
CSV_DAY2_REISSUED = ("date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
                     "20261028,UC1,w-X,1300,0.02\n")
REPORTS = [
    {"id": "r1", "startTime": "2026-10-27T07:00:00Z", "createTime": "2026-10-29T01:00:00Z", "downloadUrl": "u1"},
    {"id": "r2", "startTime": "2026-10-28T07:00:00Z", "createTime": "2026-10-29T02:00:00Z", "downloadUrl": "u2"},
    {"id": "r3", "startTime": "2026-10-28T07:00:00Z", "createTime": "2026-10-30T02:00:00Z", "downloadUrl": "u3"},
]
FILES = {"u1": CSV_DAY1, "u2": CSV_DAY2, "u3": CSV_DAY2_REISSUED}
RECORD = {"video_id": "w-X", "title": "The Man Who Needed Everyone to Envy Him", "project": "ep1",
          "published_at": "2026-10-27T10:00:00+00:00", "state": "published"}


class AnalyticsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(analytics, "folder", return_value=Path(self.temp.name)),
                        patch.object(analytics, "published_records", return_value=[dict(RECORD)])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()


class ParsingTests(AnalyticsTestCase):
    def test_rows_and_video_stats(self):
        self.assertEqual(analytics.rows(response(["a", "b"], [[1, 2]])), [{"a": 1, "b": 2}])
        self.assertEqual(analytics.rows({"columnHeaders": [{"name": "a"}]}), [])
        fake = FakeAnalytics()
        stats = analytics.video_stats(fake, "w-X", "2026-10-27", "2026-10-29")
        self.assertEqual(stats["totals"]["averageViewPercentage"], 22.5)
        self.assertEqual(stats["us_share"], 0.25)
        self.assertEqual([row["country"] for row in stats["countries"]], ["US", "BR", "GB"])
        self.assertEqual(stats["traffic_sources"][0]["insightTrafficSourceType"], "BROWSE")
        self.assertEqual(len(stats["daily"]), 2)
        country = next(call for call in fake._reports.calls if call.get("dimensions") == "country"
                       and call.get("maxResults"))
        self.assertEqual((country["ids"], country["filters"], country["maxResults"]), ("channel==MINE", "video==w-X", 10))

    def test_reach_job_is_created_once_and_reports_are_cached(self):
        reporting = FakeReporting()
        self.assertEqual(analytics.ensure_reach_job(reporting), "job1")
        self.assertEqual(analytics.ensure_reach_job(reporting), "job1")
        self.assertEqual(reporting.created, [{"reportTypeId": "channel_reach_basic_a1", "name": "Private Gravity reach"}])
        reporting = FakeReporting(reports=REPORTS)
        downloads = []
        fetch = lambda url: downloads.append(url) or FILES[url].encode()
        paths = analytics.sync_reach_reports(reporting, "job1", download=fetch)
        analytics.sync_reach_reports(reporting, "job1", download=fetch)
        self.assertEqual(downloads, ["u1", "u2", "u3"])                     # each report downloaded once
        reach = analytics.reach_for("w-X", paths)
        # the re-issued day-2 report (r3) replaces r2: 800 + 1300 impressions
        self.assertEqual(reach["impressions"], 2100)
        self.assertAlmostEqual(reach["ctr"], (800 * 0.01 + 1300 * 0.02) / 2100, places=4)
        self.assertEqual([day["day"] for day in reach["daily"]], ["2026-10-27", "2026-10-28"])
        self.assertFalse(analytics.reach_for("none", paths)["available"])


class FlagTests(unittest.TestCase):
    def test_low_ctr_and_low_retention(self):
        stats = {"reach": {"impressions": 2100, "ctr": 0.012}, "totals": {"averageViewPercentage": 22.5}}
        found = analytics.flags(stats)
        self.assertEqual(len(found), 2)
        self.assertIn("thumbnail", found[0])
        self.assertIn("abertura", found[1])
        self.assertEqual(analytics.flags({"reach": {"impressions": 220, "ctr": 0.0},
                                          "totals": {"averageViewPercentage": 45}}), [])   # too few impressions


class CacheTests(AnalyticsTestCase):
    def test_refresh_caches_and_respects_six_hours(self):
        fake, reporting = FakeAnalytics(), FakeReporting(reports=REPORTS)
        first = analytics.refresh_video(RECORD, analytics=fake, reporting=reporting,
                                        download=lambda url: FILES[url].encode())
        self.assertEqual(first["published_at"], "2026-10-27")
        self.assertEqual(first["reach"]["impressions"], 2100)
        self.assertEqual(len(first["flags"]), 2)
        calls = len(fake._reports.calls)
        again = analytics.refresh_video(RECORD, analytics=fake, reporting=reporting)
        self.assertEqual(len(fake._reports.calls), calls)                     # fresh cache: no API call
        self.assertEqual(again["fetched_at"], first["fetched_at"])
        stale = dict(first, fetched_at=(datetime.now(timezone.utc) - timedelta(hours=7)).isoformat())
        self.assertFalse(analytics.is_fresh(stale))
        overview = analytics.overview()
        self.assertEqual((overview["views"], overview["impressions"]), (400.0, 2100.0))
        self.assertEqual(overview["videos"][0]["ctr"], first["reach"]["ctr"])

    def test_reach_failure_keeps_the_rest(self):
        class Broken(FakeReporting):
            def jobs(self):
                raise RuntimeError("quota")
        stats = analytics.refresh_video(RECORD, analytics=FakeAnalytics(), reporting=Broken(), force=True)
        self.assertEqual(stats["totals"]["views"], 400)
        self.assertIn("quota", stats["reach"]["error"])

    def test_background_refresh_never_raises(self):
        with patch.object(analytics, "services", side_effect=RuntimeError("no consent")), \
                redirect_stdout(io.StringIO()) as out:
            analytics.refresh_due_safely()
        self.assertIn("analytics refresh skipped", out.getvalue())

    def test_scope_is_requested(self):
        self.assertIn(analytics.SCOPE, config.YOUTUBE_SCOPES)


class GuiTests(AnalyticsTestCase):
    def test_overview_and_video_endpoints(self):
        import gui
        analytics.refresh_video(RECORD, analytics=FakeAnalytics(), reporting=None)
        client = gui.app.test_client()
        with patch.object(config, "OUTPUT_DIR", str(Path(self.temp.name, "ep1"))):
            data = client.get("/api/analytics").get_json()
        self.assertEqual(data["project_videos"], ["w-X"])
        self.assertEqual(client.get("/api/analytics/w-X").get_json()["totals"]["views"], 400)
        self.assertEqual(client.get("/api/analytics/none").status_code, 404)
        page = client.get("/").get_data(as_text=True)
        self.assertIn("📈 Desempenho", page)


if __name__ == "__main__":
    unittest.main()
