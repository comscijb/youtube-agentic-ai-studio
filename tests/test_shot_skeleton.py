"""Phase 3.3.1-A: the code owns shot ids and timings; Gemini returns ordered contents only."""
import unittest

from agents import shot_list as shots_module
from agents.narrative import NarrativeValidationError
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import OUTLINE, VISUAL_BIBLE

SEGMENTS = [{"shot_id": sid, "scene_id": "s01", "text": "Julian set the table."}
            for sid in ("s01_01", "s01_02", "s01_03")]


def content(**extra):
    value = {"shot_type": "wide", "characters": ["julian_vance"], "location_id": "loft",
             "action": "He sets the long table", "face_visibility": "partial", "reuse_of": 0,
             "appearance": {"julian_vance": "composed"}, "others_present": False}
    value.update(extra)
    return value


class SkeletonTests(unittest.TestCase):
    def test_contents_are_zipped_by_position_and_ids_come_from_code(self):
        value = {"settings": {"loft": "immaculate"},
                 "shots": [content(), content(shot_id="s09_99", start=99, end=120), content(reuse_of=1)]}
        shots = shots_module.validate_shots(value, SEGMENTS, VISUAL_BIBLE, OUTLINE, "reference")
        self.assertEqual([shot["shot_id"] for shot in shots], ["s01_01", "s01_02", "s01_03"])
        self.assertNotIn("start", shots[1])                      # Gemini's timings are dropped
        self.assertEqual(shots[2]["reuse_of"], "s01_01")         # earlier shot by number

    def test_count_mismatch_and_bad_reuse_are_blocking(self):
        short = {"settings": {"loft": "immaculate"}, "shots": [content(), content()]}
        with self.assertRaises(NarrativeValidationError) as caught:
            shots_module.validate_shots(short, SEGMENTS, VISUAL_BIBLE, OUTLINE, "reference")
        self.assertIn("return exactly 3 shot objects", str(caught.exception))
        forward = {"settings": {"loft": "immaculate"}, "shots": [content(reuse_of=3), content(), content()]}
        with self.assertRaises(NarrativeValidationError) as caught:
            shots_module.validate_shots(forward, SEGMENTS, VISUAL_BIBLE, OUTLINE, "reference")
        self.assertIn("reuse_of must name an earlier shot", str(caught.exception))

    def test_contract_never_asks_for_ids_or_times(self):
        import inspect
        source = inspect.getsource(shots_module)
        self.assertIn("never return ids or times", source)
        self.assertNotIn("shot_id (unchanged)", source)


if __name__ == "__main__":
    unittest.main()
