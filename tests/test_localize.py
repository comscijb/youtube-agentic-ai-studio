"""Phase 4.0-B: localized titles and descriptions. Gemini and the YouTube API are mocked."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.narrative import NarrativeValidationError
from uploader import discovery_settings, localize

TITLE = "He Woke Up Next to His Own Face"
DESCRIPTION = ("A man wakes up next to an AI clone that wears his face.\n"
               "A Private Gravity philosophical thought experiment.\n\n"
               "Chapters\n0:00 Prologue\n1:16 The Second Chair\n4:02 Who Speaks First\n\n"
               "#PrivateGravity #thoughtexperiment")
PT = {"title": "Ele Acordou ao Lado do Próprio Rosto",
      "description": ("Um homem acorda ao lado de um clone de IA que usa o rosto dele.\n"
                      "Um experimento mental filosófico do Private Gravity.\n\n"
                      "Capítulos\n0:00 Prólogo\n1:16 A Segunda Cadeira\n4:02 Quem Fala Primeiro\n\n"
                      "#PrivateGravity #thoughtexperiment")}
ES = {"title": "Despertó Junto a Su Propio Rostro",
      "description": PT["description"].replace("Um homem acorda", "Un hombre despierta")}


class ValidationTests(unittest.TestCase):
    def test_good_translations_pass(self):
        found = localize.validate({"translations": {"pt": PT, "es": ES}}, ["pt", "es"], TITLE, DESCRIPTION)
        self.assertEqual(sorted(found), ["es", "pt"])

    def test_changed_timestamps_a_long_title_and_english_left_fail(self):
        moved = dict(PT, description=PT["description"].replace("1:16", "1:20"))
        self.assertIn("horários", " ".join(localize.problems("pt", moved, TITLE, DESCRIPTION, is_short=False)))
        long_title = dict(PT, title="x" * 101)
        self.assertIn("1-100", " ".join(localize.problems("pt", long_title, TITLE, DESCRIPTION, is_short=False)))
        english = dict(PT, description=PT["description"].replace(
            "Um homem acorda ao lado de um clone de IA que usa o rosto dele.", "A man wakes up next to an AI clone that wears his face."))
        self.assertIn("inglês", " ".join(localize.problems("pt", english, TITLE, DESCRIPTION, is_short=False)))
        with self.assertRaises(NarrativeValidationError):
            localize.validate({"translations": {"pt": PT}}, ["pt", "es"], TITLE, DESCRIPTION)

    def test_a_short_keeps_its_hashtag(self):
        short_title = "He Talked to His Own Duplicate #Shorts"
        lost = {"title": "Ele Falou com o Próprio Duplicado", "description": ""}
        self.assertIn("#Shorts", " ".join(localize.problems("pt", lost, short_title, "", is_short=True)))
        kept = {"title": "Ele Falou com o Próprio Duplicado #Shorts", "description": ""}
        self.assertEqual(localize.problems("pt", kept, short_title, "", is_short=True), [])

    def test_one_call_for_every_language(self):
        calls = []

        def generator(prompt, *args, **kwargs):
            calls.append(prompt)
            return json.dumps({"translations": {"pt": PT, "es": ES}})
        with redirect_stdout(io.StringIO()):
            found = localize.translate(TITLE, DESCRIPTION, ["pt", "es"], generator=generator)
        self.assertEqual(len(calls), 1)
        self.assertIn("keep every chapter timestamp", calls[0])
        self.assertEqual(found["pt"]["title"], PT["title"])


class PayloadTests(unittest.TestCase):
    def test_portuguese_first_and_the_payload(self):
        self.assertEqual(localize.ordered(["es", "pt", "fr"]), ["pt", "es", "fr"])
        self.assertEqual(localize.payload({"pt": PT, "es": {"title": "", "description": "x"}}), {"pt": PT})

    def test_upload_body_carries_the_localizations(self):
        service = MagicMock()
        service.videos().insert().next_chunk.return_value = (None, {"id": "vid1"})
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp, "v.mp4")
            video.write_bytes(b"0")
            from uploader import youtube
            with patch.object(youtube, "MediaIoBaseUpload", MagicMock()):
                youtube.upload_video(str(video), title=TITLE, description=DESCRIPTION, service=service,
                                     localizations={"pt": PT})
        kwargs = service.videos().insert.call_args.kwargs
        self.assertEqual(kwargs["part"], "snippet,status,localizations")
        self.assertEqual(kwargs["body"]["localizations"], {"pt": PT})
        self.assertEqual(kwargs["body"]["snippet"]["defaultLanguage"], "en")

    def test_update_keeps_the_snippet_and_adds_the_languages(self):
        service = MagicMock()
        service.videos().list().execute.return_value = {"items": [{"snippet": {
            "title": TITLE, "description": DESCRIPTION, "categoryId": "24", "tags": ["a"]}, "localizations": {"fr": {"title": "t", "description": "d"}}}]}
        body = localize.update_video("vid1", {"pt": PT}, service=service)
        self.assertEqual(body["snippet"]["defaultLanguage"], "en")
        self.assertEqual(sorted(body["localizations"]), ["fr", "pt"])
        self.assertEqual(service.videos().update.call_args.kwargs["part"], "snippet,localizations")


class StoreTests(unittest.TestCase):
    def test_saved_translations_follow_the_exact_title_and_description(self):
        with tempfile.TemporaryDirectory() as temp:
            localize.save(temp, TITLE, DESCRIPTION, {"pt": PT})
            self.assertEqual(localize.current(temp, TITLE, DESCRIPTION), {"pt": PT})
            self.assertIsNone(localize.current(temp, TITLE + "!", DESCRIPTION))
            with self.assertRaises(ValueError):
                localize.save(temp, TITLE, DESCRIPTION, {"pt": dict(PT, title="")})

    def test_languages_come_from_settings(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(discovery_settings, "path", lambda: Path(temp, "d.json")):
            self.assertEqual(localize.languages(), ["es", "pt", "fr", "de", "hi"])
            discovery_settings.save({"localize_languages": ["pt", "ja"]})
            self.assertEqual(localize.languages(), ["pt", "ja"])
            with self.assertRaises(ValueError):
                discovery_settings.save({"localize_languages": ["Portuguese!"]})


class BackfillTests(unittest.TestCase):
    def test_backfill_translates_the_episode_and_its_shorts(self):
        from scripts import youtube_backfill
        service = MagicMock()
        service.videos().list().execute.side_effect = lambda: {"items": [{"snippet": {"title": TITLE, "description": DESCRIPTION}}]}
        ledger = {"publications": [{"video_id": "ep1"}, {"video_id": "sh1", "parent_video_id": "ep1"}]}
        reply = json.dumps({"translations": {"pt": PT, "es": ES}})
        with patch.object(youtube_backfill.publications, "load_ledger", return_value=ledger), \
                patch.object(localize, "languages", return_value=["pt", "es"]), redirect_stdout(io.StringIO()):
            done = youtube_backfill.run_localize("ep1", with_shorts=True, service=service, generator=lambda *a, **k: reply)
        self.assertEqual([item["video_id"] for item in done], ["ep1", "sh1"])
        self.assertEqual(service.videos().update.call_count, 2)
        with patch.object(youtube_backfill.publications, "load_ledger", return_value=ledger), \
                patch.object(localize, "languages", return_value=["pt", "es"]), redirect_stdout(io.StringIO()):
            dry = youtube_backfill.run_localize("ep1", with_shorts=False, dry_run=True, service=service,
                                                generator=lambda *a, **k: reply)
        self.assertTrue(dry[0]["dry_run"])
        self.assertEqual(service.videos().update.call_count, 2)


if __name__ == "__main__":
    unittest.main()
