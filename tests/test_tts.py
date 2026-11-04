"""Phase 3.9-A: pluggable TTS — backend selection, forced-alignment matcher, calibration per voice,
pronunciations, delivery hints, voice provenance, stale narration on a voice change (no live TTS)."""
import io
import json
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import voice_calibration
from audio import tts
from audio.tts import align, delivery, elevenlabs, local, pronunciations, voices
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401


class SelectionTests(unittest.TestCase):
    def test_backend_and_channel_voice(self):
        with patch.object(config, "TTS_BACKEND", "edge"), patch.object(config, "TTS_VOICE", ""):
            self.assertEqual(tts.get().name, "edge")
            self.assertEqual(tts.channel_voice(), config.VOICE_ID)
        with patch.object(config, "TTS_BACKEND", "local"), patch.object(config, "TTS_VOICE", "bm_george"):
            self.assertEqual(tts.get().name, "local")
            self.assertEqual(tts.channel_voice(), "bm_george")
            self.assertEqual(tts.channel_voice("edge"), config.VOICE_ID)       # other backends keep defaults
        with patch.object(config, "TTS_BACKEND", "nonsense"):
            self.assertEqual(tts.get().name, "edge")
        self.assertEqual(tts.get("elevenlabs").name, "elevenlabs")
        with patch.dict("os.environ", {"ELEVENLABS_API_KEY": ""}):
            self.assertFalse(tts.get("elevenlabs").available()[0])


def timed(words, step=300, length=250, start=0):
    return [{"text": word, "start_ms": start + index * step, "end_ms": start + index * step + length}
            for index, word in enumerate(words)]


class AlignmentTests(unittest.TestCase):
    SCRIPT = "\"Margaret hated music, you know,\" Walter said. In forty years she never listened."

    def test_matcher_handles_inserted_missing_and_misheard_words(self):
        words = align.script_words(self.SCRIPT)
        truth = timed(words)
        heard = [dict(item) for item in truth]
        heard[4]["text"] = "Walker"                                  # misheard
        del heard[7]                                                  # missed: "forty"
        heard.insert(2, {"text": "um", "start_ms": 610, "end_ms": 640})   # inserted filler
        events, coverage = align.match(words, heard)
        self.assertEqual([item["text"] for item in events], words)   # every script word, in order
        report = align.validate(events, coverage, truth=truth)
        self.assertTrue(report["monotonic"])
        self.assertLessEqual(report["drift_ms"], align.MAX_DRIFT_MS)
        self.assertAlmostEqual(coverage, (len(words) - 1) / len(words))
        self.assertTrue(events[7].get("interpolated"))
        self.assertFalse(align.validate(events, coverage)["ok"])     # 92 % < 98 %: flagged

    def test_full_coverage_passes_and_backwards_times_fail(self):
        words = align.script_words("One two three four.")
        events, coverage = align.match(words, timed(words))
        self.assertTrue(align.validate(events, coverage)["ok"])
        events[2]["start_ms"] = 0
        self.assertFalse(align.validate(events, coverage)["monotonic"])

    def test_aligner_is_optional(self):
        with patch.dict("sys.modules", {"faster_whisper": None}), patch.dict(align._model, clear=True):
            with self.assertRaises(align.AlignerUnavailable):
                align.transcribe("x.wav")


class CalibrationTests(unittest.TestCase):
    def test_keys_are_per_backend_and_voice_and_old_keys_read_as_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "cal.json")
            path.write_text(json.dumps({"voices": {"en-US-AndrewNeural|-5%": {"wpm": 162.9}}}), encoding="utf-8")
            self.assertEqual(voice_calibration.calibrated_wpm("en-US-AndrewNeural", "-5%", path, backend="edge"), 162.9)
            voice_calibration.record(300, 120, voice="bm_george", rate="-5%", path=path, backend="local")
            data = json.loads(path.read_text(encoding="utf-8"))["voices"]
        self.assertIn("local:bm_george|-5%", data)
        self.assertIn("edge:en-US-AndrewNeural|-5%", data)                   # migrated on write
        self.assertNotEqual(voice_calibration.voice_key("v", "-5%", "edge"), voice_calibration.voice_key("v", "-5%", "local"))


class FakeBackend:
    name = "local"
    audio_ext = "mp3"

    def __init__(self, supports_style=False):
        self.supports_style = supports_style
        self.calls = []

    def available(self):
        return True, "fake"

    def cache_tag(self):
        return "fake"

    def synthesize(self, text, out_path, *, voice, rate, pitch, style=None, label=""):
        self.calls.append({"text": text, "style": style, "voice": voice})
        Path(out_path).write_bytes(b"x")
        return {"audio": out_path, "words": timed(text.split()), "aligned": True}


class PronunciationAndDeliveryTests(unittest.TestCase):
    def test_respelling_applies_to_every_backend_keeping_case(self):
        entries = {"Siobhan": {"respell": "shiv-awn"}, "Leeds": {"respell": "leeds"}}
        self.assertEqual(pronunciations.apply("Siobhan left Leeds. siobhan's coat.", "edge", entries),
                         "Shiv-awn left Leeds. shiv-awn's coat.")
        self.assertEqual(pronunciations.apply("Siobhan", "local", entries), "Shiv-awn")
        self.assertNotEqual(pronunciations.fingerprint(entries), pronunciations.fingerprint({}))

    def test_narrator_sends_the_respelling_and_style_lines_only_when_supported(self):
        from video import narrator
        section = {"emotional_tone": "Quiet, intimate", "narration": ""}
        text = 'Siobhan waited. "Are you there?" she asked.'
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(pronunciations, "load", return_value={"Siobhan": {"respell": "Shiv-awn"}}):
            plain = FakeBackend()
            with patch.object(tts, "get", return_value=plain):
                narrator.synthesise_scene(text, str(Path(temp, "a.mp3")), "", section)
            styled = FakeBackend(supports_style=True)
            with patch.object(tts, "get", return_value=styled), patch.object(narrator, "_concat"), \
                    patch.object(narrator.assembly, "decode", return_value=[0.0] * narrator.assembly.RATE):
                events = narrator.synthesise_scene(text, str(Path(temp, "b.mp3")), "", section)
        self.assertEqual([call["text"] for call in plain.calls], ['Shiv-awn waited. "Are you there?" she asked.'])
        self.assertEqual([(call["text"], call["style"]) for call in styled.calls],
                         [("Shiv-awn waited.", "quiet"), ('"Are you there?"', "dialogue"), ("she asked.", "quiet")])
        self.assertEqual(events[2]["start_ms"], 1000)                       # second line offset by the first

    def test_delivery_never_invents_a_style(self):
        self.assertEqual(delivery.lines("He waited.", {"emotional_tone": "tense"}), [("He waited.", "narration")])


class ProvenanceTests(unittest.TestCase):
    def test_reference_clip_needs_a_provenance_note(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "VOICE_ASSETS_DIR", temp, create=True):
            clip = Path(temp, "me.wav")
            clip.write_bytes(b"RIFF")
            with self.assertRaises(voices.VoiceProvenanceError):
                voices.check_reference(clip)
            with self.assertRaises(voices.VoiceProvenanceError):
                voices.save_reference(b"RIFF", "me.wav", speaker="Me", licence="own voice", own_voice=True,
                                      not_public_person=False)
            saved = voices.save_reference(b"RIFF", "me.wav", speaker="Me", licence="own voice", own_voice=True,
                                          not_public_person=True)
            self.assertEqual(voices.check_reference(saved), saved)
            self.assertIn("real public person: no", voices.provenance_path(saved).read_text(encoding="utf-8"))

    def test_gui_refuses_a_clip_without_provenance(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "VOICE_ASSETS_DIR", temp, create=True):
            client = gui.app.test_client()
            refused = client.post("/api/voices/reference", data={"clip": (io.BytesIO(b"RIFF"), "x.wav"),
                                                                 "speaker": "Me", "licence": ""},
                                  content_type="multipart/form-data")
            self.assertEqual(refused.status_code, 400)
            self.assertFalse(Path(temp, "x.wav").exists())


class StaleNarrationTests(unittest.TestCase):
    def test_a_voice_change_marks_the_narration_stale(self):
        import gui
        from studio import projects
        with tempfile.TemporaryDirectory() as temp, patch.object(projects, "root", return_value=Path(temp)), \
                patch.object(config, "TTS_BACKEND", "edge"), patch.object(config, "TTS_VOICE", ""), \
                patch.object(gui, "ROOT", temp):
            folder = projects.create("Ep")
            Path(folder, "narration_words.json").write_text(json.dumps({"words": [], "voice": tts.identity()}),
                                                             encoding="utf-8")
            self.assertFalse(projects.narration_voice_changed(folder))
            with patch.object(config, "OUTPUT_DIR", str(folder)):
                answer = gui.app.test_client().post("/api/voices/channel",
                                                    json={"backend": "local", "voice": "bm_george"}).get_json()
                env = Path(temp, ".env").read_text(encoding="utf-8")
            self.assertTrue(answer["narration_stale"])
            self.assertTrue(projects.narration_voice_changed(folder))
        self.assertIn("TTS_BACKEND=local", env)
        Path(folder).parent  # noqa: B018
        legacy = {"words": []}                                                      # narrations before 3.9
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "narration_words.json").write_text(json.dumps(legacy), encoding="utf-8")
            self.assertFalse(projects.narration_voice_changed(temp))


class EngineHelperTests(unittest.TestCase):
    def test_elevenlabs_character_timings_become_words(self):
        chars = list("Hi you.")
        words = elevenlabs.words_from_characters(chars, [i * 0.1 for i in range(7)], [i * 0.1 + 0.05 for i in range(7)])
        self.assertEqual([(w["text"], round(w["start_ms"]), round(w["end_ms"])) for w in words],
                         [("Hi", 0, 150), ("you.", 300, 650)])

    def test_local_helpers(self):
        self.assertEqual(local.speed("-5%"), 0.95)
        with tempfile.TemporaryDirectory() as temp:
            path = local.write_wav(Path(temp, "a.wav"), [0.0, 0.5, -0.5], 24000)
            with wave.open(path) as handle:
                self.assertEqual((handle.getframerate(), handle.getnframes()), (24000, 3))
        with patch.object(config, "TTS_LOCAL_ENGINE", "chatterbox"):
            self.assertTrue(local.LocalBackend().supports_style)
        with patch.object(config, "TTS_LOCAL_ENGINE", "kokoro"):
            self.assertFalse(local.LocalBackend().supports_style)

    def test_kokoro_runs_out_of_process_through_the_worker_protocol(self):
        class FakeProcess:
            def __init__(self, args, **kwargs):
                self.args, self.sent = args, []
                self.stdin = self
                self.stdout = io.StringIO('{"ok": true, "ready": true}\n{"ok": true, "out": "a.wav"}\n'
                                          '{"ok": false, "error": "ValueError: bad voice"}\n')

            def write(self, text):
                self.sent.append(json.loads(text))

            def flush(self):
                pass

            def close(self):
                pass

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp, "kokoro-v1.0.onnx")
            model.write_bytes(b"m")
            Path(temp, "voices-v1.0.bin").write_bytes(b"v")
            with patch.object(config, "TTS_KOKORO_MODEL", str(model)), \
                    patch.object(local.subprocess, "Popen", FakeProcess):
                worker = local.KokoroWorker(Path("python.exe"))
                worker.synthesize("Hello there.", "bm_george", 0.95, "a.wav")
                with self.assertRaises(tts.TTSUnavailable):
                    worker.synthesize("Hello.", "zz_nobody", 1.0, "b.wav")
                worker.close()
        self.assertTrue(worker.process.args[1].endswith("kokoro_worker.py"))
        self.assertEqual(worker.process.sent[0], {"text": "Hello there.", "voice": "bm_george", "speed": 0.95,
                                                  "out": "a.wav"})
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "TTS_KOKORO_MODEL", str(Path(temp, "none.onnx"))):
            with self.assertRaises(tts.TTSUnavailable):
                local.KokoroWorker(Path("python.exe"))

    def test_alignment_decodes_with_ffmpeg_to_16k(self):
        with tempfile.TemporaryDirectory() as temp:
            path = local.write_wav(Path(temp, "a.wav"), [0.25] * 24000, 24000)
            samples = align.decode(path)
        self.assertAlmostEqual(len(samples), 16000, delta=200)
        self.assertAlmostEqual(float(samples[8000]), 0.25, places=2)


if __name__ == "__main__":
    unittest.main()
