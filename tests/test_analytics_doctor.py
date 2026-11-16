"""Phase 4.1-A: why the YouTube data is blocked. Google services are mocked; the error bodies are recorded."""
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import config
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import analytics, analytics_doctor

DISABLED = {"error": {"code": 403, "message": "YouTube Analytics API has not been used in project 11872407110 before or it "
                      "is disabled. Enable it by visiting https://console.developers.google.com/apis/api/"
                      "youtubeanalytics.googleapis.com/overview?project=11872407110 then retry.",
                      "errors": [{"message": "...", "domain": "usageLimits", "reason": "accessNotConfigured"}],
                      "status": "PERMISSION_DENIED"}}
SCOPE = {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                   "errors": [{"message": "Insufficient Permission", "domain": "global", "reason": "insufficientPermissions"}]}}
FORBIDDEN = {"error": {"code": 403, "message": "Forbidden", "errors": [{"message": "Forbidden", "domain": "global", "reason": "forbidden"}]}}


def http_error(body, status=403):
    from googleapiclient.errors import HttpError
    response = MagicMock(status=status, reason="Forbidden")
    return HttpError(response, json.dumps(body).encode("utf-8"))


class ExplainTests(unittest.TestCase):
    def test_api_disabled_gives_the_project_and_the_links(self):
        found = analytics_doctor.explain(http_error(DISABLED))
        self.assertEqual((found["reason"], found["project"], found["status"]), ("accessNotConfigured", "11872407110", 403))
        self.assertIn("youtubeanalytics.googleapis.com?project=11872407110", found["fix"])
        self.assertIn("youtubereporting.googleapis.com?project=11872407110", found["fix"])

    def test_missing_scope_asks_to_authorize_and_pick_the_channel(self):
        found = analytics_doctor.explain(http_error(SCOPE))
        self.assertEqual(found["reason"], "insufficientPermissions")
        self.assertIn("escolha o canal Private Gravity", found["fix"])

    def test_forbidden_identity(self):
        found = analytics_doctor.explain(http_error(FORBIDDEN))
        self.assertEqual(found["reason"], "forbidden")
        self.assertIn("Permissões", found["fix"])

    def test_the_status_keeps_the_reason(self):
        from agents.narrative import atomic_write_json
        atomic_write_json(config.PUBLICATIONS_FILE, {"schema_version": 1, "publications": [
            {"video_id": "v1", "state": "published", "published_at": "2026-11-01T00:00:00+00:00"}]})
        with patch.object(analytics, "refresh_all", side_effect=http_error(DISABLED)):
            with self.assertRaises(Exception):
                analytics.refresh_with_status(force=True)
        state = analytics.status()
        self.assertEqual(state["reason"], "accessNotConfigured")
        self.assertIn("has not been used in project", state["message"])
        self.assertIn("Ative a YouTube Analytics API", state["fix"])
        self.assertFalse(state["auth_required"])


class DoctorTests(unittest.TestCase):
    def services(self, *, channel="UCLXUyBGw-ojvJDAT5RSOjZQ", analytics_error=None):
        youtube, report_api, reporting = MagicMock(), MagicMock(), MagicMock()
        youtube.channels().list().execute.return_value = {"items": [{"id": channel, "snippet": {"title": "Private Gravity"}}]}
        if analytics_error:
            report_api.reports().query().execute.side_effect = analytics_error
        else:
            report_api.reports().query().execute.return_value = {"columnHeaders": [{"name": "views"}], "rows": [[42]]}
        reporting.reportTypes().list().execute.return_value = {"reportTypes": [{"id": "channel_reach_basic_a1"}]}
        credentials = MagicMock(granted_scopes=list(config.YOUTUBE_SCOPES))
        return dict(credentials=credentials, youtube=youtube, analytics=report_api, reporting=reporting)

    def test_all_good(self):
        found = analytics_doctor.diagnose(**self.services(), today=date(2026, 11, 15))
        self.assertTrue(found["ok"])
        self.assertEqual(found["ids"], "channel==UCLXUyBGw-ojvJDAT5RSOjZQ")
        self.assertIn("42 views", found["checks"][2]["detail"])
        self.assertIn("Tudo certo", "\n".join(analytics_doctor.lines(found)))

    def test_disabled_api_and_wrong_channel_make_a_checklist(self):
        found = analytics_doctor.diagnose(**self.services(channel="UCother", analytics_error=http_error(DISABLED)))
        self.assertFalse(found["ok"])
        self.assertEqual(found["ids"], "channel==MINE")
        text = "\n".join(analytics_doctor.lines(found))
        self.assertIn("motivo: accessNotConfigured", text)
        self.assertIn("não é a do canal Private Gravity", text)
        self.assertNotIn("token", text.lower())


class ChannelIdTests(unittest.TestCase):
    def test_queries_use_the_resolved_channel(self):
        youtube = MagicMock()
        youtube.channels().list().execute.return_value = {"items": [{"id": "UCLXUyBGw-ojvJDAT5RSOjZQ", "snippet": {"title": "PG"}}]}
        try:
            self.assertEqual(analytics.use_channel(youtube), "channel==UCLXUyBGw-ojvJDAT5RSOjZQ")
            service = MagicMock()
            service.reports().query().execute.return_value = {"columnHeaders": [], "rows": []}
            analytics._query(service, "v1", "2026-11-01", "2026-11-15", "views")
            self.assertEqual(service.reports().query.call_args.kwargs["ids"], "channel==UCLXUyBGw-ojvJDAT5RSOjZQ")
            youtube.channels().list().execute.return_value = {"items": [{"id": "UCother", "snippet": {"title": "x"}}]}
            self.assertEqual(analytics.use_channel(youtube), "channel==MINE")
        finally:
            analytics._IDS["value"] = "channel==MINE"


if __name__ == "__main__":
    unittest.main()
