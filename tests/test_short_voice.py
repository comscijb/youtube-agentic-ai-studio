"""Phase 4.3-C: the Shorts voice (speed, pauses, lead) and the mix (accent at 0, music, sting, -14 LUFS); TTS mocked."""
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from audio import assembly
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import short_voice

ITEM = {"id": "short_01", "format": "hook", "script": "They all heard it. What did he think?",
        "cta_text": "The full story is on the channel."}


def tone(path: Path, seconds: float, freq: float = 440.0, level: float = 0.3, rate: int = 48000):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * rate)) / rate
    data = (np.sin(2 * np.pi * freq * t) * level * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())


class FakeSynthesis:
    """Each sentence is 0.5 s of tone, one word event per word, evenly spaced."""
    def __init__(self, speed):
        self.speed = speed

    def scene(self, text, label=""):
        from agents.attention import split_sentences
        chunks, events, cuts, cursor = [], [], [], 0
        for sentence in split_sentences(text):
            samples = (np.sin(np.arange(int(0.5 * assembly.RATE)) / 8.0) * 0.3).astype(np.float32)
            words = sentence.split()
            for index, word in enumerate(words):
                start = cursor / assembly.RATE * 1000 + index * 500 / len(words)
                events.append({"text": word.strip(".?!,"), "start_ms": start, "end_ms": start + 400 / len(words)})
            chunks.append(samples)
            cursor += len(samples)
            cuts.append({"end": cursor / assembly.RATE, "after": "sentence"})
        return {"samples": np.concatenate(chunks), "events": events, "cuts": cuts, "stats": {}}


class FakeBackend:
    name = "local"
    sentence_mode = True
    audio_ext = "wav"

    def __init__(self):
        self.speeds = []

    def cache_tag(self):
        return "fake"

    def sentence_synthesis(self, voice, cache_dir=None, apply=None, progress=print, speed=None):
        self.speeds.append(speed)
        return FakeSynthesis(speed)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_speed_pauses_and_lead(self):
        backend = FakeBackend()
        with patch.object(config, "SHORTS_TTS_SPEED", 0.95), patch("audio.tts.channel_voice", return_value="am_lewis"), \
                patch("audio.heard_thoughts.enabled", return_value=False):
            voice = short_voice.synthesize(self.folder, ITEM, backend=backend, script={})
        self.assertEqual(backend.speeds, [0.95])
        self.assertEqual(voice["speed"], 0.95)
        self.assertGreaterEqual(voice["words"][0]["start"], short_voice.LEAD)
        # three sentences of 0.5 s (trimmed a little) with two 0.15 s pauses and the 0.3 s lead, no scene pause
        self.assertLess(voice["duration"], 0.3 + 3 * 0.5 + 2 * 0.15 + 0.05)
        self.assertGreater(voice["duration"], 0.3 + 3 * 0.4 + 2 * 0.15)
        self.assertIsNotNone(voice["cta_start"])
        self.assertTrue(Path(voice["voice"]).is_file())
        again = short_voice.synthesize(self.folder, ITEM, backend=backend, script={})
        self.assertEqual(backend.speeds, [0.95])              # cached
        self.assertEqual(again["duration"], voice["duration"])

    def test_pauses_are_short(self):
        self.assertEqual(short_voice.pauses()["sentence"], 0.15)
        self.assertLess(short_voice.pauses()["paragraph"], 0.2)

    def test_the_line_speaks_no_cta(self):
        backend = FakeBackend()
        line = {**ITEM, "format": "line"}
        with patch("audio.tts.channel_voice", return_value="am_lewis"), patch("audio.heard_thoughts.enabled", return_value=False):
            voice = short_voice.synthesize(self.folder, line, backend=backend, script={})
        self.assertIsNone(voice["cta_start"])
        self.assertNotIn("channel", voice["text"])


class MixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.library = {"impact": [{"path": str(self.folder / "sfx" / "impact_1.wav"), "relative": "impact_1.wav"}]}
        tone(self.folder / "sfx" / "impact_1.wav", 1.0, 90.0, 0.6)
        tone(self.folder / "music" / "Dark Drive.wav", 8.0, 220.0, 0.2)
        tone(self.folder / "music" / "Calm.wav", 8.0, 330.0, 0.2)
        self.tracks = [{"file": "Calm.wav", "mood": "melancholy", "energy": "low", "duration": 8.0},
                       {"file": "Dark Drive.wav", "mood": "dramatic", "energy": "high", "duration": 8.0, "rms_db": -17.0}]
        tone(self.folder / "voice.wav", 2.0, 600.0, 0.3, rate=assembly.RATE)

    def tearDown(self):
        self.temp.cleanup()

    def voice(self):
        return {"voice": str(self.folder / "voice.wav"), "duration": 2.0, "cta_start": 1.4, "words": []}

    def test_plan_accent_at_zero_sting_under_cta_dramatic_music(self):
        with patch("video.music_plan.library_dir", return_value=self.folder / "music"):
            layout = short_voice.plan(ITEM, self.voice(), salt="x", sfx_library=self.library, tracks=self.tracks)
        roles = {cue["role"]: cue for cue in layout["cues"]}
        self.assertEqual(roles["opening"]["start"], 0.0)
        self.assertAlmostEqual(roles["sting"]["start"], 1.3, places=2)
        self.assertEqual(layout["music"][0]["file"], "Dark Drive.wav")
        self.assertAlmostEqual(layout["duration"], 2.0 + short_voice.END_CARD)
        self.assertGreater(layout["music"][0]["volume"], config.MUSIC_VOLUME)

    def test_line_has_no_end_card(self):
        with patch("video.music_plan.library_dir", return_value=self.folder / "music"):
            layout = short_voice.plan({**ITEM, "format": "line"}, {**self.voice(), "cta_start": None}, salt="x",
                                      sfx_library=self.library, tracks=self.tracks)
        self.assertEqual(layout["duration"], 2.0)
        self.assertEqual([cue["role"] for cue in layout["cues"]], ["opening"])

    def test_missing_sfx_is_reported(self):
        layout = short_voice.plan(ITEM, self.voice(), salt="x", sfx_library={}, tracks=[])
        self.assertEqual(layout["cues"], [])
        self.assertTrue(layout["missing"])

    def test_real_mix_is_normalised(self):
        with patch("video.music_plan.library_dir", return_value=self.folder / "music"):
            result = short_voice.mix(self.folder, ITEM, self.voice(), sfx_library=self.library, tracks=self.tracks)
        self.assertTrue(Path(result["audio"]).is_file())
        self.assertAlmostEqual(result["loudness"]["output_integrated_lufs"], -14.0, delta=1.5)


if __name__ == "__main__":
    unittest.main()
