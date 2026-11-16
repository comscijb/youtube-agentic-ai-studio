"""Phase 4.2-E: the music index and the per-episode plan (no real library)."""
import tempfile
import unittest
from pathlib import Path

from agents.narrative import atomic_write_json
from video import music_plan

TRACKS = [{"file": f"{mood}-{level}.mp3", "mood": mood, "energy": level}
          for mood in ("dark", "melancholy", "dramatic") for level in ("low", "mid", "high")]
TYPES = ["cold_open", "opening", "setup", "development", "climax", "reversal", "aftermath"]


def project(temp):
    scenes, words, cursor = [], [], 0.0
    for index, kind in enumerate(TYPES):
        length = 15.0 if kind == "cold_open" else 60.0
        scenes.append({"start": cursor, "end": cursor + length})
        text = "The deed had a second page." if kind == "reversal" else "Who owns the morning now?" if kind == "aftermath" else "Nothing happens here."
        for offset, token in enumerate(text.split()):
            words.append({"text": token, "start": cursor + 5 + offset * 0.4, "end": cursor + 5.3 + offset * 0.4,
                          "sentence_end": token.endswith((".", "?"))})
        cursor += length
    atomic_write_json(Path(temp, "scene_timeline.json"), {"scenes": scenes})
    atomic_write_json(Path(temp, "narration_words.json"), {"words": words})
    atomic_write_json(Path(temp, "attention_map.json"), {"sentences": [
        {"section_type": "reversal", "text": "The deed had a second page.", "tags": [{"tag": "reveal"}]}]})
    return [{"id": index + 1, "section_type": kind, "narration": "x"} for index, kind in enumerate(TYPES)], scenes


class IndexTests(unittest.TestCase):
    def test_energy_by_thirds_and_thin_moods(self):
        tracks = [{"rms_db": -30 + index, "onsets_per_second": 1.0} for index in range(9)]
        music_plan.classify(tracks)
        self.assertEqual([item["energy"] for item in tracks][:3], ["low"] * 3)
        self.assertEqual(tracks[-1]["energy"], "high")
        gaps = music_plan.gaps([{"mood": "dramatic", "energy": "high"}] * 6)
        self.assertIn("dark: 0 faixa(s)", gaps)
        self.assertTrue(any(item.startswith("ending:") for item in gaps))

    def test_mood_from_the_file_name(self):
        self.assertEqual(music_plan.mood_of({"file": "Orison - Dan Bodan - Classical - Sombrio.mp3", "moods": ["sombrio"]}), "dark")
        self.assertEqual(music_plan.mood_of({"file": "x - y - z - Melancólico.mp3", "moods": []}), "melancholy")


class PlanTests(unittest.TestCase):
    def test_tracks_by_part_with_a_drop_before_the_reversal_line_and_a_final_swell(self):
        with tempfile.TemporaryDirectory() as temp:
            sections, scenes = project(temp)
            plan = music_plan.build(temp, sections, volume=0.12, tracks=TRACKS)
        segments = plan["segments"]
        self.assertTrue(2 <= len(segments) <= 3)
        self.assertEqual(len({segment["file"] for segment in segments}), len(segments))
        parts = [segment["part"] for segment in segments]
        self.assertIn("ending", parts)
        ending = next(segment for segment in segments if segment["part"] == "ending")
        self.assertEqual(ending["mood"], "melancholy")
        for earlier, later in zip(segments, segments[1:]):
            self.assertAlmostEqual(earlier["end"] - later["start"], music_plan.CROSSFADE, places=3)   # crossfaded
        reversal_line = scenes[TYPES.index("reversal")]["start"] + 5
        self.assertEqual(len(plan["drops"]), 1)
        start, end = plan["drops"][0]
        self.assertAlmostEqual(start, reversal_line - music_plan.DROP_LEAD, places=2)
        self.assertGreater(end, reversal_line)
        self.assertGreater(plan["swells"][0], scenes[-1]["start"])

    def test_a_tension_part_gets_dramatic_high_energy(self):
        track = music_plan.choose(TRACKS, "tension", "salt", set())
        self.assertEqual((track["mood"], track["energy"]), ("dramatic", "high"))
        again = music_plan.choose(TRACKS, "tension", "salt", {track["file"]})
        self.assertNotEqual(again["file"], track["file"])


if __name__ == "__main__":
    unittest.main()
