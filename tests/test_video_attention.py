"""Phase 4.2-F: the video attention map on synthetic timelines, the intelligibility check on synthetic mixes."""
import math
import tempfile
import unittest

import numpy as np

from video import cut_plan, video_attention

SECTIONS = [{"id": 1, "section_type": "cold_open"}, {"id": 2, "section_type": "opening"}]


def plan(views):
    return {"schedule": {1: [view for view in views if view["section"] == 1], 2: [view for view in views if view["section"] == 2]}}


def view(start, end, section=1, final=False):
    return {"start": start, "end": end, "section": section, "final": final}


def evaluate(views, durations=(10.0, 30.0), **extra):
    options = dict(narration_events=[2.0, 12.0, 25.0], key_lines=[], sfx_cues=[{"kind": "accent", "tag": "riser", "start": 0.5}],
                   music={"segments": [{"start": 0.0}]}, first_word=0.3)
    options.update(extra)
    return video_attention.build(plan=plan(views), sections=SECTIONS, durations=list(durations), **options)


class CheckTests(unittest.TestCase):
    GOOD = [view(0, 3), view(3, 6), view(6, 10), view(0, 5, 2), view(5, 10, 2), view(10, 15, 2), view(15, 20, 2),
            view(20, 25, 2), view(25, 30, 2, final=True)]

    def test_a_good_cut_passes(self):
        found = evaluate(self.GOOD)
        self.assertEqual([item["check"] for item in found["findings"] if item["severity"] == "major"], [])
        self.assertEqual(len(found["bins"]), 8)

    def test_a_long_hold_is_major_except_the_final_image(self):
        long_hold = self.GOOD[:3] + [view(0, 12, 2), view(12, 30, 2, final=True)]
        found = evaluate(long_hold)
        holds = [item for item in found["findings"] if item["check"] == "hold"]
        self.assertEqual(len(holds), 1)
        self.assertEqual((holds[0]["severity"], holds[0]["fix"]["action"]), ("major", "add_cut"))

    def test_twenty_quiet_seconds(self):
        sparse = [view(0, 6.9), view(6.9, 10), view(0, 30, 2, final=True)]
        found = evaluate(sparse, narration_events=[2.0], sfx_cues=[{"kind": "accent", "tag": "riser", "start": 0.5}])
        self.assertIn("quiet", [item["check"] for item in found["findings"]])

    def test_the_first_five_seconds(self):
        found = evaluate(self.GOOD, sfx_cues=[], music={"segments": []}, first_word=6.0, title_card=True)
        first = next(item for item in found["findings"] if item["check"] == "first_5_seconds")
        for part in ("sem evento de som", "a narração não começa", "cartão de título"):
            self.assertIn(part, first["claim"])
        self.assertEqual(first["severity"], "major")

    def test_key_lines_and_sfx_near_an_accent(self):
        lines = [{"text": "Who sold the sky?", "start": 2.0, "end": 2.8}]
        cues = [{"kind": "accent", "tag": "impact", "start": 14.0}, {"kind": "action", "tag": "door", "start": 14.4},
                {"kind": "accent", "tag": "riser", "start": 0.5}]
        found = evaluate(self.GOOD, key_lines=lines, sfx_cues=cues)
        checks = [item["check"] for item in found["findings"]]
        self.assertIn("key_line", checks)
        overlap = next(item for item in found["findings"] if item["check"] == "sfx_overlap")
        self.assertEqual(overlap["fix"], {"action": "remove_sfx", "tag": "door", "start": 14.4})


class IntelligibilityTests(unittest.TestCase):
    def tone(self, freq, level, seconds=4, rate=16000):
        return (level * np.sin(2 * math.pi * freq * np.arange(seconds * rate) / rate)).astype(np.float32)

    def test_voice_well_above_the_bed_passes_and_a_loud_bed_fails(self):
        voice = self.tone(2000, 0.3)
        quiet_bed = self.tone(2000, 0.03)                 # -20 dB
        loud_bed = self.tone(2000, 0.15)                  # -6 dB
        self.assertEqual(video_attention.intelligibility(voice, quiet_bed, 16000), [])
        failing = video_attention.intelligibility(voice, loud_bed, 16000)
        self.assertEqual(len(failing), 4)
        self.assertLess(failing[0]["margin_db"], 10)

    def test_pauses_are_not_judged(self):
        voice = np.concatenate([self.tone(2000, 0.3, 2), np.zeros(32000, dtype=np.float32)])
        bed = self.tone(2000, 0.2, 4)
        self.assertEqual([item["start"] for item in video_attention.intelligibility(voice, bed, 16000)], [0.0, 1.0])


class FixTests(unittest.TestCase):
    def test_fixes_and_the_override(self):
        with tempfile.TemporaryDirectory() as temp:
            video_attention.apply_fix(temp, {"action": "add_cut", "at": 15.0})
            video_attention.apply_fix(temp, {"action": "remove_sfx", "tag": "door", "start": 14.4})
            video_attention.apply_fix(temp, {"action": "lower_music", "scale": 0.8})
            data = video_attention.apply_fix(temp, {"action": "lower_music", "scale": 0.8})
            self.assertEqual(data["extra_cuts"], [15.0])
            self.assertEqual(data["music_scale"], 0.64)
            report = {"findings": [{"check": "hold", "claim": "12 s"}, {"check": "quiet", "claim": "20 s"}]}
            self.assertEqual(video_attention.blocked(report, temp), ["12 s"])
            with self.assertRaises(ValueError):
                video_attention.override(temp, "curto")
            video_attention.override(temp, "Plano longo proposital no final")
            self.assertEqual(video_attention.blocked(report, temp), [])

    def test_an_extra_cut_splits_the_view(self):
        views = [{"shot_id": "s01", "start": 0.0, "end": 12.0, "view": "full", "crop": None, "transition": "crossfade"}]
        found = cut_plan.extra_cuts(views, [6.0])
        self.assertEqual([(item["start"], item["end"]) for item in found], [(0.0, 6.0), (6.0, 12.0)])
        self.assertNotEqual(found[0]["shot_id"], found[1]["shot_id"])
        self.assertEqual(found[1]["motion_variant"], 1)


if __name__ == "__main__":
    unittest.main()
