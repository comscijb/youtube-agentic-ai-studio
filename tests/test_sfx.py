"""Phase 4.2-D: the SFX library and licences, the cue sheet, levels and the ffmpeg mix (Gemini mocked)."""
import io
import json
import math
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

from agents.narrative import atomic_write_json
from video import sfx, sound
from video.music import _run, media_duration

SENTENCES = [{"index": 0, "section": 0, "section_type": "opening", "text": "The door creaked open."},
             {"index": 1, "section": 0, "section_type": "opening", "text": "Rain hammered the windows."},
             {"index": 2, "section": 1, "section_type": "reversal", "text": "The deed had a second page."}]


def tone(path: Path, seconds: float, freq: float = 440.0, level: float = 0.3, rate: int = 48000):
    samples = (level * np.sin(2 * math.pi * freq * np.arange(int(seconds * rate)) / rate) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return path


class LibraryTests(unittest.TestCase):
    def test_only_registered_files_with_an_allowed_licence_are_used(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "door").mkdir()
            tone(root / "door" / "door_01.wav", 0.5)
            tone(root / "door" / "door_02.wav", 0.5)
            tone(root / "impact_hit.wav", 0.5)
            atomic_write_json(root / "LICENSES.json", {
                "door/door_01.wav": {"source": "YouTube Audio Library", "license": "YouTube Audio Library"},
                "door/door_02.wav": {"source": "x", "license": "CC BY 4.0 (attribution required)"},
                "impact_hit.wav": {"source": "y", "license": "CC0"}})
            found = sfx.library(root)
            self.assertEqual([item["relative"] for item in found["door"]], ["door/door_01.wav"])
            self.assertEqual([item["relative"] for item in found["impact"]], ["impact_hit.wav"])
            self.assertIn("door/door_02.wav", sfx.unregistered(root))
            self.assertIn("rain", sfx.missing_tags(root))
            placed, missing = sfx.assign_files([{"kind": "action", "tag": "door", "start": 1, "end": 2},
                                                {"kind": "bed", "tag": "rain", "start": 0, "end": 9}], root)
            self.assertEqual(len(placed), 1)
            self.assertEqual(missing, ["rain"])
            self.assertEqual(placed[0]["under_voice_db"], 18.0)


class CueTests(unittest.TestCase):
    def test_quotes_must_exist_in_the_narration(self):
        value = {"cues": [{"kind": "action", "tag": "door", "sentence": 0, "quote": "door creaked open"},
                          {"kind": "bed", "tag": "rain", "sentence": 1, "quote": "a storm outside"},
                          {"kind": "accent", "tag": "impact", "sentence": 2, "quote": "second page"},
                          {"kind": "accent", "tag": "laser", "sentence": 2, "quote": "second page"}]}
        kept = sfx.validate(value, SENTENCES)
        self.assertEqual([(cue["tag"], cue["sentence"]) for cue in kept], [("door", 0), ("impact", 2)])

    def test_timing_from_words_and_the_accent_cap(self):
        words, clock = [], 0.0
        for item in SENTENCES:
            for token in item["text"].split():
                words.append({"text": token, "start": clock, "end": clock + 0.3})
                clock += 0.4
        cues = sfx.time_cues([{"kind": "action", "tag": "door", "sentence": 0, "quote": "door creaked open"},
                              {"kind": "bed", "tag": "rain", "sentence": 1, "quote": "Rain hammered"},
                              {"kind": "accent", "tag": "riser", "sentence": 2, "quote": "The deed"}], words, SENTENCES,
                             [(0.0, 3.0), (3.0, 10.0)])
        door = next(cue for cue in cues if cue["tag"] == "door")
        self.assertAlmostEqual(door["start"], 0.4)
        rain = next(cue for cue in cues if cue["tag"] == "rain")
        self.assertEqual((rain["start"], rain["end"]), (0.0, 3.0))
        riser = next(cue for cue in cues if cue["tag"] == "riser")
        self.assertLess(riser["start"], riser["end"])
        many = [{"kind": "accent", "tag": "impact", "start": float(t), "end": t + 1} for t in range(0, 60, 5)]
        self.assertEqual(len(sfx.cap_accents(many)), sfx.ACCENTS_PER_MINUTE)
        close = [{"kind": "accent", "tag": "impact", "start": 0.0, "end": 1}, {"kind": "accent", "tag": "whoosh", "start": 0.5, "end": 1}]
        self.assertEqual(len(sfx.cap_accents(close)), 1)

    def test_build_is_one_cached_call(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "narration_words.json"), {"words": [{"text": token, "start": i * 0.4, "end": i * 0.4 + 0.3}
                                                                            for i, token in enumerate("The door creaked open.".split())]})
            atomic_write_json(Path(temp, "scene_timeline.json"), {"scenes": [{"start": 0, "end": 5}]})
            sections = [{"id": 1, "section_type": "opening", "narration": "The door creaked open."}]

            def generator(prompt, *args, **kwargs):
                calls.append(prompt)
                return json.dumps({"cues": [{"kind": "action", "tag": "door", "sentence": 0, "quote": "door creaked"}]})
            with redirect_stdout(io.StringIO()):
                first = sfx.build(temp, sections, generator=generator)
                sfx.build(temp, sections, generator=generator)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["cues"][0]["tag"], "door")


class MixTests(unittest.TestCase):
    def test_levels_and_a_real_ffmpeg_mix(self):
        self.assertAlmostEqual(sound.gain_for(-20.0, -20.0, 20.0), 0.1, places=4)
        with tempfile.TemporaryDirectory() as temp:
            voice = tone(Path(temp, "voice.wav"), 3.0, 220, 0.4)
            door = tone(Path(temp, "door.wav"), 0.6, 900, 0.5)
            rain = tone(Path(temp, "rain.wav"), 1.0, 300, 0.2)
            music = tone(Path(temp, "music.wav"), 1.5, 330, 0.3)
            graph, inputs = sound.graph(3.0, [{"path": str(music), "start": 0.0, "end": 3.0, "offset": 0.0, "fade_in": 0.5,
                                               "fade_out": 0.5, "volume": 0.12}],
                                        [{"path": str(door), "start": 1.0, "end": 1.6, "gain": 0.2, "kind": "action"},
                                         {"path": str(rain), "start": 0.0, "end": 3.0, "gain": 0.05, "kind": "bed"}],
                                        drops=[(1.8, 2.2)], swells=[2.6])
            self.assertIn("adelay=1000", graph)
            self.assertIn("sidechaincompress", graph)
            self.assertEqual([item["loop"] for item in inputs], [False, True, False, True])
            out = Path(temp, "mix.wav")
            result = _run(sound.command(str(voice), inputs, graph, str(out)))
            self.assertEqual(result.returncode, 0, result.stderr[-400:])
            self.assertAlmostEqual(media_duration(str(out)), 3.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
