"""Phase 3.9.1-B: voice previews in the GUI — catalogue filtered by the installed Kokoro voices, cached
samples, one failing voice never stops the others, the CLI, the Vozes routes (no live TTS)."""
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from audio.tts import catalogue
from scripts import voice_audition
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

TEXT = "Walter was laughing."


def fake_voices_file(folder: Path, names) -> Path:
    path = folder / "voices-v1.0.bin"
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(f"{name}.npy", b"x")
    return path


def fake_audition(calls, failing=()):
    def audition(text, backend, voice, out):
        calls.append(f"{backend}:{voice}")
        if voice in failing:
            raise RuntimeError(f"{voice} exploded")
        name = voice_audition.sample_name(backend, voice, text)
        (Path(out) / name).write_bytes(b"mp3")
        return {"backend": backend, "voice": voice, "file": name, "rtf": 0.5, "coverage": 1.0,
                "text_hash": voice_audition.text_hash(text)}
    return audition


class CatalogueTests(unittest.TestCase):
    def test_only_installed_kokoro_voices_are_offered(self):
        with tempfile.TemporaryDirectory() as temp:
            path = fake_voices_file(Path(temp), ["am_michael", "bm_george", "zf_other"])
            with patch.dict("os.environ", {"ELEVENLABS_API_KEY": ""}):
                entries = catalogue.catalogue(path)
            missing = catalogue.catalogue(Path(temp, "none.bin"))
        local = [entry["voice"] for entry in entries if entry["backend"] == "local"]
        self.assertEqual(local, ["am_michael", "bm_george"])
        self.assertIn("en-GB-RyanNeural", [entry["voice"] for entry in entries])
        self.assertNotIn("elevenlabs", {entry["backend"] for entry in entries})
        self.assertEqual([entry for entry in missing if entry["backend"] == "local"], [])
        self.assertTrue(all(entry["licence"] for entry in entries))


class RunTests(unittest.TestCase):
    def test_cached_samples_are_skipped_and_failures_are_isolated(self):
        calls = []
        pairs = [("edge", "en-GB-RyanNeural"), ("local", "bm_broken"), ("local", "bm_george")]
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(voice_audition, "audition", side_effect=fake_audition(calls, failing={"bm_broken"})):
            first = voice_audition.run(pairs, TEXT, Path(temp), progress=lambda _: None)
            second = voice_audition.run(pairs, TEXT, Path(temp), progress=lambda _: None)
            saved = voice_audition.load_results(Path(temp))
            page = Path(temp, "index.html").read_text(encoding="utf-8")
        self.assertEqual(calls, ["edge:en-GB-RyanNeural", "local:bm_broken", "local:bm_george", "local:bm_broken"])
        self.assertIn("exploded", first[1]["error"])
        self.assertEqual(first[2]["file"], voice_audition.sample_name("local", "bm_george", TEXT))
        self.assertTrue(second[0]["cached"] and second[2]["cached"])
        self.assertEqual(len(saved["rows"]), 3)                          # one row per voice
        self.assertIn('preload="none"', page)

    def test_another_text_renders_again(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(voice_audition, "audition", side_effect=fake_audition(calls)):
            voice_audition.run([("edge", "x")], "one", Path(temp), progress=lambda _: None)
            voice_audition.run([("edge", "x")], "two", Path(temp), progress=lambda _: None)
        self.assertEqual(len(calls), 2)


class CliTests(unittest.TestCase):
    def test_catalogue_flag_and_default_text(self):
        seen = {}

        def run(pairs, text, out, progress):
            seen.update(pairs=pairs, text=text)
            return []
        entries = [{"backend": "edge", "voice": "en-GB-RyanNeural", "label": "Ryan", "licence": "x"},
                   {"backend": "local", "voice": "bm_george", "label": "George", "licence": "y"}]
        with patch.object(voice_audition, "run", side_effect=run), \
                patch.object(catalogue, "catalogue", return_value=entries), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(voice_audition.main(["--catalogue"]), 0)
            self.assertEqual(voice_audition.main(["--list"]), 0)
        self.assertEqual(seen["pairs"], [("edge", "en-GB-RyanNeural"), ("local", "bm_george")])
        self.assertEqual(seen["text"], voice_audition.DEFAULT_TEXT.read_text(encoding="utf-8").strip())
        self.assertIn("local:bm_george", out.getvalue())
        self.assertIn("index.html", out.getvalue())


class GuiTests(unittest.TestCase):
    def test_template_has_no_per_project_edge_select(self):
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        for gone in ("quick-voice", "applyQuickVoice", 'id="s-voice"'):
            self.assertNotIn(gone, page)
        self.assertIn("🎙 Vozes", page)
        self.assertIn("generateVoicePreviews", page)

    def test_voices_lists_play_urls_that_the_sample_route_serves(self):
        import gui
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            name = voice_audition.sample_name("edge", "en-GB-RyanNeural", TEXT)
            (folder / name).write_bytes(b"ID3fake")
            (folder / "results.json").write_text(json.dumps({"text": TEXT, "rows": [
                {"backend": "edge", "voice": "en-GB-RyanNeural", "file": name, "rtf": 0.2, "coverage": 0.99},
                {"backend": "local", "voice": "bm_george", "error": "Kokoro failed: boom"}]}), encoding="utf-8")
            with patch.object(gui, "_audition_dir", return_value=folder), \
                    patch.object(catalogue, "kokoro_installed", return_value={"bm_george"}):
                client = gui.app.test_client()
                data = client.get("/api/voices").get_json()
                rows = {row["voice"]: row for row in data["voices"]}
                served = client.get(rows["en-GB-RyanNeural"]["url"])
                body = served.data
                served.close()
                self.assertEqual(client.get("/api/voices/sample/..%2Fresults.json").status_code, 404)
        self.assertEqual(body, b"ID3fake")
        self.assertEqual(rows["bm_george"]["url"], "")
        self.assertIn("boom", rows["bm_george"]["error"])
        self.assertEqual(rows["en-US-AndrewNeural"]["url"], "")              # catalogue voice without a sample
        self.assertIn("preview_text", data)

    def test_previews_job_needs_voices(self):
        import gui
        client = gui.app.test_client()
        self.assertEqual(client.post("/api/voices/previews", json={"voices": []}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
