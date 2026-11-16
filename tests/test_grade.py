"""Phase 4.2-C: grades per beat, transitions, the photosensitivity guard."""
import unittest

import numpy as np

from video import cut_plan, grade


def frame(rgb=(110, 100, 95)):
    image = np.zeros((90, 160, 3), dtype=np.uint8)
    image[:] = rgb
    image[30:60, 60:100] = (170, 30, 35)                 # a crimson object
    image[:, :20] = (20, 20, 20)
    return image


class GradeTests(unittest.TestCase):
    def test_beats(self):
        self.assertEqual([grade.beat_for(kind) for kind in ("opening", "climax", "reversal", "aftermath", "cold_open")],
                         ["calm", "tension", "reversal", "ending", "cold_open"])

    def test_tension_deepens_blacks_and_cools_the_midtones(self):
        base = frame()
        tense = grade.apply(base, grade.grades()["tension"])
        self.assertLess(int(tense[0, 0].sum()), int(base[0, 0].sum()))      # deeper black
        mid = tense[10, 120].astype(int) - base[10, 120].astype(int)
        self.assertGreater(mid[2] - mid[0], 0)                               # cooler midtones

    def test_reversal_desaturates_but_keeps_the_crimson_accent(self):
        base = frame((120, 90, 60))
        found = grade.apply(base, grade.grades()["reversal"], local=0.0)
        background = found[10, 120].astype(int)
        self.assertLess(background.max() - background.min(), 60 - 30)
        self.assertEqual(tuple(found[45, 80]), tuple(base[45, 80]))        # the accent untouched
        later = grade.apply(base, grade.grades()["reversal"], local=10.0)
        self.assertGreater(int(later[10, 120].max()) - int(later[10, 120].min()), int(background.max() - background.min()))

    def test_ending_is_warmer(self):
        base = frame()
        warm = grade.apply(base, grade.grades()["ending"]).astype(int)
        self.assertGreater(warm[10, 120][0] - warm[10, 120][2], int(base[10, 120][0]) - int(base[10, 120][2]))


class TransitionTests(unittest.TestCase):
    def test_dip_on_time_jumps_and_after_the_cold_open(self):
        sections = [{"id": 1, "section_type": "cold_open"}, {"id": 2, "section_type": "opening"},
                    {"id": 3, "section_type": "development"}, {"id": 4, "section_type": "development"}]
        schedule = {2: [{"transition": "cut"}], 3: [{"transition": "cut"}], 4: [{"transition": "dip"}]}
        found = cut_plan.boundaries(sections, schedule)
        self.assertEqual(found[1], {"dip_in": False, "dip_out": True})
        self.assertTrue(found[2]["dip_in"])
        self.assertEqual(found[3], {"dip_in": False, "dip_out": True})
        self.assertTrue(found[4]["dip_in"])
        self.assertEqual(cut_plan._transition({"transition": "crossfade"}, 0, "development"), "dip")
        self.assertEqual(cut_plan._transition({"transition": "cut"}, 2, "tension"), "cut")
        self.assertEqual(cut_plan._transition({"transition": "cut"}, 2, "development"), "crossfade")

    def test_the_dip_lasts_point_four_seconds(self):
        self.assertEqual(grade.dip(0.0, 5.0, dip_in=True, dip_out=False), 0.0)
        self.assertEqual(grade.dip(0.2, 5.0, dip_in=True, dip_out=False), 1.0)
        self.assertAlmostEqual(grade.dip(4.9, 5.0, dip_in=False, dip_out=True), 0.5, places=5)


class GuardTests(unittest.TestCase):
    def test_grade_ramps_and_dips_stay_under_ten_percent_per_frame(self):
        base = frame((170, 160, 150))
        fps = 30
        ramp = [grade.apply(base, grade.grades()["tension"], grade.ramp(index / fps)) for index in range(20)]
        self.assertLessEqual(grade.max_step(ramp), grade.MAX_LUMA_STEP)
        dipping = [(base.astype(np.float32) * grade.dip(index / fps, 2.0, dip_in=True, dip_out=False)).astype(np.uint8)
                   for index in range(12)]
        self.assertLessEqual(grade.max_step(dipping), grade.MAX_LUMA_STEP)
        reversal = [grade.apply(base, grade.grades()["reversal"], grade.ramp(index / fps), local=index / fps) for index in range(20)]
        self.assertLessEqual(grade.max_step(reversal), grade.MAX_LUMA_STEP)


class SettingsTests(unittest.TestCase):
    def test_switches_and_rhythm_are_saved_and_applied(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import config
        from video import cut_settings
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(config, "CUT_SETTINGS_FILE", Path(temp, "c.json"), create=True), \
                patch.object(config, "KEY_LINES_ENABLED", True), patch.object(config, "RHYTHM", dict(config.RHYTHM)):
            found = cut_settings.save({"switches": {"KEY_LINES_ENABLED": False}, "rhythm": {"development": [2.5, 4.5]}})
            self.assertFalse(found["switches"]["KEY_LINES_ENABLED"])
            self.assertEqual(config.RHYTHM["development"], [2.5, 4.5])
            with self.assertRaises(ValueError):
                cut_settings.save({"rhythm": {"tension": [5, 3]}})


if __name__ == "__main__":
    unittest.main()
