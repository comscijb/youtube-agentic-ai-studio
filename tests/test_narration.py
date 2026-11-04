"""Phase 3.0-B/C: narration robustness, cache, WPM calibration and caption cues."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from agents import voice_calibration
from audio.tts import edge
from video import captions, narrator

TEXT = "He set the table for his father’s dinner—eight guests, all watching. Elena didn’t move."
SPOKEN = ["He", "set", "the", "table", "for", "his", "father's", "dinner", "eight", "guests",
          "all", "watching", "Elena", "didn't", "move"]


def events(tokens=SPOKEN, step=380, length=300):
    return [{"text": token, "start_ms": i * step, "end_ms": i * step + length}
            for i, token in enumerate(tokens)]


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()) as output:
        result = function(*args, **kwargs)
    return result, output.getvalue()


class CaptionTests(unittest.TestCase):
    def test_alignment_keeps_screenplay_punctuation(self):
        words = captions.align_words(TEXT, events())
        texts = [word["text"] for word in words]
        self.assertIn("father’s", texts)
        self.assertIn("dinner—eight", texts)          # one token, two spoken events
        dash = words[texts.index("dinner—eight")]
        self.assertEqual((dash["start"], dash["end"]), (7 * .38, 8 * .38 + .3))
        self.assertEqual([word["text"] for word in words if word["sentence_end"]],
                         ["watching.", "move."])

    def test_punctuation_tokens_and_skipped_words(self):
        words = captions.align_words("Wait — now. Go", events(["Wait", "now"]))
        self.assertEqual([word["text"] for word in words], ["Wait —", "now.", "Go"])
        self.assertEqual(words[2]["start"], words[1]["end"])

    def test_cue_limits(self):
        long_text = ("one two three four five six seven eight nine ten eleven twelve. "
                     "Short one. extraordinarily lengthy vocabulary everywhere imaginable here.")
        tokens = [token.strip(".") for token in long_text.split()]
        cues = captions.build_cues(captions.align_words(long_text, events(tokens)))
        for start, end, text in cues:
            self.assertLessEqual(len(text.split()), 7)
            self.assertLessEqual(len(text), 42)
            self.assertGreaterEqual(end - start, 300)
            self.assertLessEqual(end - start, 4500 + 80)
        self.assertIn("Short one.", [text for _, _, text in cues])     # never crosses a sentence
        for (_, end, _), (start, _, _) in zip(cues, cues[1:]):
            self.assertLessEqual(end, start)                             # no overlap

    def test_lead_in_min_duration_and_end_limit(self):
        words = [{"text": "Hi.", "start": 1.0, "end": 1.2, "sentence_end": True},
                 {"text": "Go.", "start": 5.0, "end": 5.3, "sentence_end": True}]
        cues = captions.build_cues(words, end_limit=5.6)
        self.assertEqual(cues[0], (920, 1720, "Hi."))                  # 80 ms lead, 0.8 s min
        self.assertEqual(cues[1], (4920, 5600, "Go."))

    def test_max_seconds_splits_slow_speech(self):
        slow = [{"text": f"w{i}", "start": i * 1.0, "end": i * 1.0 + .9, "sentence_end": False}
                for i in range(6)]
        cues = captions.build_cues(slow)
        self.assertTrue(all(end - start <= 4580 for start, end, _ in cues))
        self.assertGreater(len(cues), 1)

    def test_proportional_fallback_never_strips_apostrophes(self):
        words = captions.proportional_words(TEXT, 6.0)
        cues = captions.build_cues(words, end_limit=6.0)
        self.assertIn("father’s", " ".join(text for _, _, text in cues))
        self.assertLessEqual(cues[-1][1], 6000)


class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, *, rate="+0%", pitch="+0Hz", boundary="SentenceBoundary"):
        FakeCommunicate.calls.append({"boundary": boundary, "rate": rate})
        self.text = text

    async def stream(self):
        yield {"type": "audio", "data": b"mp3"}
        yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "Hi"}


class OldCommunicate:
    def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
        self.kwargs = {"rate": rate}


class SynthesisTests(unittest.TestCase):
    def test_word_boundary_requested_when_supported(self):
        FakeCommunicate.calls = []
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(edge.edge_tts, "Communicate", FakeCommunicate):
            result = edge.synthesise_scene("Hi", str(Path(temp, "a.mp3")))
        self.assertEqual(FakeCommunicate.calls[0]["boundary"], "WordBoundary")
        self.assertEqual(result, [{"text": "Hi", "start_ms": 100.0, "end_ms": 300.0}])

    def test_older_edge_tts_without_boundary_parameter(self):
        with patch.object(edge.edge_tts, "Communicate", OldCommunicate):
            self.assertFalse(edge.supports_word_boundary())
            communicate = edge._communicate("Hi")
        self.assertNotIn("boundary", communicate.kwargs)

    def test_transient_failures_retry_with_backoff_then_succeed(self):
        attempts = []

        async def flaky(text, path):
            attempts.append(1)
            if len(attempts) < 3:
                raise edge.edge_tts.exceptions.NoAudioReceived("none")
            return [{"text": "ok"}]

        waits = []
        with patch.object(edge, "_synthesise", flaky), patch.object(edge, "_sleep", waits.append):
            result, _ = quiet(edge.synthesise_scene, "x", "unused.mp3", "scene 1/1")
        self.assertEqual(result, [{"text": "ok"}])
        self.assertEqual(waits, [2, 5])

    def test_403_prints_upgrade_hint_once_and_gives_up_after_three_retries(self):
        class Forbidden(Exception):
            status = 403

        async def always(text, path):
            raise Forbidden("Invalid response status 403")

        waits = []
        edge._upgrade_hint_shown = False
        with patch.object(edge, "_synthesise", always), patch.object(edge, "_sleep", waits.append):
            with self.assertRaises(Forbidden):
                output = io.StringIO()
                with redirect_stdout(output):
                    edge.synthesise_scene("x", "unused.mp3")
        self.assertEqual(waits, [2, 5, 10])
        self.assertEqual(output.getvalue().count("pip install -U edge-tts"), 1)

    def test_non_transient_error_is_not_retried(self):
        async def broken(text, path):
            raise ValueError("bad voice")

        with patch.object(edge, "_synthesise", broken), patch.object(edge, "_sleep") as wait:
            with self.assertRaises(ValueError):
                edge.synthesise_scene("x", "unused.mp3")
        wait.assert_not_called()

    def test_scene_cache_reuses_unchanged_text_only(self):
        calls = []

        def fake_synth(text, path, label="", section=None):
            calls.append(text)
            Path(path).write_bytes(text.encode("utf-8"))
            return [{"text": text, "start_ms": 0, "end_ms": 10}]

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(narrator, "synthesise_scene", side_effect=fake_synth):
            cache = str(Path(temp, "cache"))
            first = narrator.cached_scene("Scene one.", cache, str(Path(temp, "1.mp3")), "")
            again = narrator.cached_scene("Scene one.", cache, str(Path(temp, "2.mp3")), "")
            edited = narrator.cached_scene("Scene one, edited.", cache, str(Path(temp, "3.mp3")), "")
            self.assertEqual(Path(temp, "2.mp3").read_bytes(), b"Scene one.")
        self.assertEqual((first[1], again[1], edited[1]), (False, True, False))
        self.assertEqual(calls, ["Scene one.", "Scene one, edited."])

    def test_cache_key_covers_voice_settings(self):
        base = narrator.cache_key("Text")
        with patch.object(config, "VOICE_RATE", "+10%"):
            self.assertNotEqual(narrator.cache_key("Text"), base)


class FakeClip:
    def __init__(self, path):
        self.duration = 3.0

    def close(self):
        pass


def tone(path, rate=24000):
    """3 seconds of signal for any decoded file."""
    import numpy as np
    return (0.2 * np.sin(np.arange(3 * rate) * 0.1)).astype(np.float32)


class GenerateNarrationTests(unittest.TestCase):
    def test_end_to_end_with_mocks_writes_word_cues_and_calibration(self):
        script = {"sections": [{"id": 1, "narration": " ".join(["word"] * 60) + "."},
                               {"id": 2, "narration": "Elena didn’t move."}]}

        def fake_synth(text, path, label="", section=None):
            Path(path).write_bytes(b"x")
            tokens = [token.strip(".") for token in text.split()]
            return events(tokens, step=40, length=30)

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(narrator, "synthesise_scene", side_effect=fake_synth), \
                patch.object(narrator.assembly, "decode", side_effect=tone), \
                patch.object(narrator.assembly, "encode", side_effect=lambda samples, path, rate=0: Path(path).write_bytes(b"x")), \
                patch.object(narrator.joins, "check", return_value={"ok": True, "bursts": [], "short_gaps": []}), \
                patch.object(narrator, "_get_audio_duration_ms", return_value=30000.0), \
                patch.object(config, "VOICE_CALIBRATION_FILE", str(Path(temp, "cal.json"))):
            _, output = quiet(narrator.generate_narration, script, temp)
            srt =Path(temp, "narration.srt").read_text(encoding="utf-8")
            calibration = json.loads(Path(temp, "cal.json").read_text(encoding="utf-8"))
            cached = [path for path in Path(temp, ".tts_cache").iterdir() if path.suffix in (".mp3", ".wav")]
        self.assertIn("Elena didn’t move.", srt)
        self.assertNotIn("Microsoft server change", output)
        entry = calibration["voices"][voice_calibration.voice_key()]
        self.assertEqual((entry["words"], entry["seconds"], entry["wpm"]), (63, 30.0, 126.0))
        self.assertEqual(len(cached), 2)

    def test_neutral_warning_without_word_boundaries(self):
        script = {"sections": [{"id": 1, "narration": "A short line without timings."}]}

        def no_words(text, path, label="", section=None):
            Path(path).write_bytes(b"x")
            return []

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(narrator, "synthesise_scene", side_effect=no_words), \
                patch.object(narrator.assembly, "decode", side_effect=tone), \
                patch.object(narrator.assembly, "encode", side_effect=lambda samples, path, rate=0: Path(path).write_bytes(b"x")), \
                patch.object(narrator.joins, "check", return_value={"ok": True, "bursts": [], "short_gaps": []}), \
                patch.object(narrator, "_get_audio_duration_ms", return_value=3000.0), \
                patch.object(config, "VOICE_CALIBRATION_FILE", str(Path(temp, "cal.json"))):
            _, output = quiet(narrator.generate_narration, script, temp)
        self.assertIn('No WordBoundary events for scene(s) [1]', output)
        self.assertIn('boundary="WordBoundary"', output)
        self.assertNotIn("switch voice", output.lower())


class CalibrationTests(unittest.TestCase):
    def test_record_and_planning_use_calibrated_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp, "cal.json"))
            with patch.object(config, "VOICE_CALIBRATION_FILE", path), \
                    patch.object(config, "VOICE_ID", "en-US-AndrewNeural"), \
                    patch.object(config, "TTS_BACKEND", "edge"), patch.object(config, "TTS_VOICE", ""), \
                    patch.object(config, "VOICE_RATE", "-5%"), \
                    patch.object(voice_calibration, "audition_wpm", return_value=None):
                self.assertEqual(voice_calibration.planning_wpm(), config.NARRATION_WPM)
                entry = voice_calibration.record(1558, 616.52)
                self.assertEqual(entry["wpm"], 151.6)
                self.assertEqual(voice_calibration.planning_wpm(), 151.6)
                self.assertEqual(round(12 * voice_calibration.planning_wpm()), 1819)
                with patch.object(config, "USE_CALIBRATED_WPM", False):
                    self.assertEqual(voice_calibration.planning_wpm(), config.NARRATION_WPM)
                with patch.object(config, "VOICE_RATE", "+0%"):
                    self.assertIsNone(voice_calibration.calibrated_wpm())

    def test_implausible_measurements_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(config, "VOICE_CALIBRATION_FILE", str(Path(temp, "cal.json"))):
            self.assertIsNone(voice_calibration.record(20, 10))           # too few words
            self.assertIsNone(voice_calibration.record(1000, 60))         # 1000 wpm
            self.assertFalse(Path(temp, "cal.json").exists())

    def test_repository_seed_matches_the_first_render(self):
        seed = json.loads(Path("data/voice_calibration.json").read_text(encoding="utf-8"))
        # Seeded with 151.6 wpm; every narration run updates it with the latest measurement.
        voices = seed["voices"]                                       # legacy key, or backend:voice since 3.9-A
        entry = voices.get("edge:en-US-AndrewNeural|-5%") or voices["en-US-AndrewNeural|-5%"]
        self.assertTrue(140 <= entry["wpm"] <= 170)

    def test_outline_targets_follow_calibrated_wpm(self):
        from agents import scriptwriter
        from agents.narrative import validate_story_brief
        from tests.test_narrative import bible, outline, story_brief
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(scriptwriter, "planning_wpm", return_value=151.6), \
                patch.object(scriptwriter, "generate", return_value=json.dumps(outline())):
            result, _ = quiet(scriptwriter._outline, validate_story_brief(story_brief()), bible(),
                              "normal", 12, Path(temp), True)
        self.assertEqual(result["target_word_count"], 1819)

    def test_gui_settings_show_calibrated_and_configured_wpm(self):
        import gui
        with patch.object(gui.voice_calibration, "calibrated_wpm", return_value=151.6):
            data = gui.app.test_client().get("/api/settings").get_json()
        self.assertEqual(data["calibrated_wpm"], 151.6)
        self.assertEqual(data["configured_wpm"], float(config.NARRATION_WPM))


class PreflightLiveTests(unittest.TestCase):
    def test_live_check_reports_audio_and_word_timings(self):
        from scripts import render_preflight

        def fake(text, path, label=""):
            Path(path).write_bytes(b"audio")
            return [{"text": "The"}]

        with patch.object(narrator, "synthesise_scene", side_effect=fake):
            self.assertEqual(render_preflight.live_tts_check()[1], "PASS")
        with patch.object(narrator, "synthesise_scene",
                          side_effect=lambda text, path, label="": Path(path).write_bytes(b"a") and []):
            self.assertEqual(render_preflight.live_tts_check()[1], "WARN")
        with patch.object(narrator, "synthesise_scene", side_effect=OSError("offline")):
            self.assertEqual(render_preflight.live_tts_check()[1], "FAIL")

    def test_preflight_reports_calibrated_wpm(self):
        from scripts import render_preflight
        with patch.object(voice_calibration, "calibrated_wpm", return_value=151.6):
            wpm, rate, note = render_preflight.estimate_wpm()
        self.assertEqual((wpm, rate), (151.6, "+0%"))
        self.assertIn("measured", note)


if __name__ == "__main__":
    unittest.main()
