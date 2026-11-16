"""Phase 4.1-D: the skeptical-viewer simulation and its gate (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import config
from agents import viewer_sim
from agents.narrative import NarrativeValidationError

LINE = "He walked down the long road again and again in the cold."
SECTIONS = [{"id": 1, "section_type": "opening", "title": "A", "narration": " ".join([LINE] * 6)},
            {"id": 2, "section_type": "reversal", "title": "B", "narration": " ".join([LINE] * 6)}]
PERSONAS = [{"id": "a", "description": "x"}, {"id": "b", "description": "y"}, {"id": "c", "description": "z"}]


def answer(block_list, scores, leave=None, quote=LINE):
    return {"personas": [{"id": persona["id"], "first_leave_second": (leave or {}).get(persona["id"]),
                          "blocks": [{"block": item["block"], "interest": scores[index] if isinstance(scores[index], int) else scores[index][number],
                                      "would_leave": False, "reason": "r", "quote": quote}
                                     for index, item in enumerate(block_list)]}
                         for number, persona in enumerate(PERSONAS)]}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.blocks = viewer_sim.blocks(SECTIONS, 150)

    def test_blocks_are_fifteen_seconds(self):
        self.assertGreaterEqual(len(self.blocks), 4)
        self.assertEqual(self.blocks[1]["start"], 15.0)

    def test_a_good_screenplay_passes(self):
        result = viewer_sim.validate(answer(self.blocks, [8] * len(self.blocks)), self.blocks, PERSONAS)
        verdict = viewer_sim.gate(result, self.blocks, reversal_start=self.blocks[-1]["start"])
        self.assertTrue(verdict["passes"], verdict["failures"])

    def test_each_rule_fails(self):
        early = viewer_sim.validate(answer(self.blocks, [8] * len(self.blocks), leave={"a": 10, "b": 20}), self.blocks, PERSONAS)
        self.assertIn("primeiros 30 s", " ".join(viewer_sim.gate(early, self.blocks)["failures"]))
        dull = viewer_sim.validate(answer(self.blocks, [6] * len(self.blocks)), self.blocks, PERSONAS)
        self.assertIn("interesse mediano", " ".join(viewer_sim.gate(dull, self.blocks)["failures"]))
        dip = [9] * len(self.blocks)
        dip[1] = dip[2] = 4
        sagging = viewer_sim.validate(answer(self.blocks, dip), self.blocks, PERSONAS)
        self.assertIn("dois blocos seguidos", " ".join(viewer_sim.gate(sagging, self.blocks)["failures"]))
        flat_end = [9] * (len(self.blocks) - 1) + [7]
        weak = viewer_sim.validate(answer(self.blocks, flat_end), self.blocks, PERSONAS)
        self.assertIn("virada", " ".join(viewer_sim.gate(weak, self.blocks, reversal_start=self.blocks[-1]["start"])["failures"]))

    def test_validation(self):
        with self.assertRaises(NarrativeValidationError):
            viewer_sim.validate({"personas": [{"id": "a", "blocks": []}]}, self.blocks, PERSONAS)
        bad = answer(self.blocks, [11] * len(self.blocks))
        with self.assertRaises(NarrativeValidationError):
            viewer_sim.validate(bad, self.blocks, PERSONAS)
        invented = viewer_sim.validate(answer(self.blocks, [8] * len(self.blocks), quote="never said"), self.blocks, PERSONAS)
        self.assertEqual(invented["personas"][0]["blocks"][0]["quote"], "")
        leaving = answer(self.blocks, [8] * len(self.blocks))
        leaving["personas"][0]["blocks"][2]["would_leave"] = True
        found = viewer_sim.validate(leaving, self.blocks, PERSONAS)
        self.assertEqual(found["personas"][0]["first_leave_second"], self.blocks[2]["start"])

    def test_predicted_retention_and_correlation(self):
        result = viewer_sim.validate(answer(self.blocks, [8] * len(self.blocks), leave={"a": 20}), self.blocks, PERSONAS)
        curve = viewer_sim.predicted_retention(result, self.blocks)
        self.assertEqual(curve[0]["share"], 1.0)
        self.assertAlmostEqual(curve[-1]["share"], 0.667, places=3)
        real = [{"ratio": ratio / 10, "watch": 1 - ratio / 20} for ratio in range(11)]
        sim = [{"start": index * 10, "share": 1 - index / 20} for index in range(10)]
        self.assertGreater(viewer_sim.correlation(sim, real, 100), 0.9)
        self.assertIsNone(viewer_sim.correlation([], real, 100))


class RunTests(unittest.TestCase):
    def test_one_call_cached_or_one_per_persona(self):
        block_list = viewer_sim.blocks(SECTIONS, 150)
        calls = []

        def generator(prompt, *args, **kwargs):
            calls.append(prompt)
            wanted = [persona for persona in PERSONAS if f"- {persona['id']}:" in prompt]
            full = answer(block_list, [8] * len(block_list))
            return json.dumps({"personas": [item for item in full["personas"] if item["id"] in {p["id"] for p in wanted}]})
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "VIEWER_PERSONAS", PERSONAS), redirect_stdout(io.StringIO()):
            record = viewer_sim.run(temp, {"sections": SECTIONS}, generator=generator, words_per_minute=150)
            viewer_sim.run(temp, {"sections": SECTIONS}, generator=generator, words_per_minute=150)
            self.assertEqual(len(calls), 1)
            self.assertEqual(record["label"], "simulação")
            self.assertTrue(record["gate"]["passes"])
            with patch.object(config, "VIEWER_SIM_MODE", "per_persona"):
                viewer_sim.run(temp, {"sections": SECTIONS}, generator=generator, words_per_minute=150, force=True)
            self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()


class CalibrationScriptTests(unittest.TestCase):
    def test_rows_compare_a_recorded_simulation_with_the_real_curve(self):
        import tempfile as _tempfile
        from pathlib import Path as _Path
        from agents.narrative import atomic_write_json
        from scripts import attention_calibration
        with _tempfile.TemporaryDirectory() as temp:
            recorded = _Path(temp, "recorded")
            atomic_write_json(_Path(temp, "calibration.json"), {"sunrise": {"slug": "2026-11-12-sunrise"}})
            atomic_write_json(recorded / "sunrise" / "viewer_sim.json", {"duration": 100, "personas": [{"first_leave_second": 20}],
                                                                       "predicted_retention": [{"start": t * 10, "share": 1 - t / 20} for t in range(10)]})
            ledger = {"publications": [{"video_id": "v1", "project": "2026-11-12-sunrise", "state": "published"}]}
            curve = [{"ratio": r / 10, "watch": 1 - r / 20} for r in range(11)]
            with patch("uploader.publications.load_ledger", return_value=ledger), \
                    patch("uploader.analytics.cached", return_value={"retention": curve}):
                found = attention_calibration.rows(recorded)
        self.assertEqual(found[0]["video_id"], "v1")
        self.assertGreater(found[0]["correlation"], 0.9)
