"""Phase 3.5.1-D: Gemini second opinion (qa_shots, GUI, probe) — advisory only, Gemini mocked."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from scripts import probe_local_vlm as probe
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_qa_focused import FakeDetector
from tests.test_qa_pipeline import Detector
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video.qa import policy
from video.qa.gemini_vlm import GeminiVLM
from video.qa.layered import LayeredQA


def gemini(reply="no", calls=None):
    """A GeminiVLM whose model call is a stub answering every question with ``reply``."""
    def generate(prompt, model=None, **kwargs):
        if calls is not None:
            calls.append(prompt)
        count = prompt.count('"q')
        return json.dumps({f"q{index}": reply for index in range(count)})
    return GeminiVLM(generator=generate, model="gemini-test")


class QaShotsSecondOpinionTests(unittest.TestCase):
    def test_gemini_answer_is_stored_apart_and_advisory(self):
        from scripts import qa_shots
        local = {"version": 2, "status": "pass", "verdict": "pass", "issues": [], "edits": 0}
        with tempfile.TemporaryDirectory() as temp, patch("video.qa.deterministic.blank_issues", new=lambda image: []):
            output = Path(temp)
            atomic_write_json(output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
            shots = [dict(SHOTS[0], others_present=False, image_path="images/shots/s01_01.jpg", qa=dict(local)),
                     dict(SHOTS[0], shot_id="s01_02", image_path="images/shots/s01_02.jpg")]
            atomic_write_json(output / "shot_list.json", {"version": 2, "shots": shots, "unique_shots": 2})
            (output / "images" / "shots").mkdir(parents=True)
            for shot in shots:
                Image.new("RGB", (320, 180)).save(output / shot["image_path"])
            calls = []
            qa = LayeredQA(["deterministic", "gemini"], detector=Detector(), gemini=gemini("no", calls),
                           progress=lambda _: None, mode="advisory")
            with redirect_stdout(io.StringIO()):
                summary = qa_shots.run(output, qa=qa, backend="gemini", shot_ids=["s01_01"], progress=lambda _: None)
            saved = load_json(output / "shot_list.json")["shots"]
        self.assertEqual(summary["second_opinions"], 1)
        self.assertEqual(len(calls), 1)                                    # one batched call
        first = saved[0]["qa"]
        self.assertEqual((first["status"], first["verdict"], first["issues"]), ("pass", "pass", []))   # untouched
        opinion = first["second_opinion"]
        self.assertEqual(opinion["backend"], "gemini")
        self.assertTrue(opinion["issues"])                                  # "no glasses" → a missing_marker
        self.assertTrue(all(item["severity"] == "advisory" for item in opinion["issues"]))
        self.assertNotIn("qa", saved[1])                                    # not selected


class GuiSecondOpinionTests(unittest.TestCase):
    def test_button_checks_one_shot_or_every_hinted_shot(self):
        import gui
        import config
        from scripts import qa_shots
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            hint = {"type": "extra_person", "severity": "advisory", "hint": True, "box": None, "target": "",
                    "note": "", "source": "deterministic"}
            shots = [dict(SHOTS[0], qa={"status": "pass", "issues": [hint]}), dict(SHOTS[0], shot_id="s01_02")]
            atomic_write_json(output / "shot_list.json", {"shots": shots, "unique_shots": 2})
            with patch.object(config, "OUTPUT_DIR", str(output)), \
                    patch.dict(gui._state, {"imagegen_busy": False, "running": False}), \
                    patch.object(qa_shots, "run", return_value={"second_opinions": 1}) as run:
                client = gui.app.test_client()
                self.assertEqual(client.post("/api/imagegen/second_opinion", json={}).status_code, 400)
                self.assertTrue(client.post("/api/imagegen/second_opinion", json={"hinted": True}).get_json()["ok"])
                for _ in range(100):
                    if run.called and not gui._state["imagegen_busy"]:
                        break
                    import time
                    time.sleep(0.02)
        self.assertEqual((run.call_args.kwargs["backend"], run.call_args.kwargs["shot_ids"]), ("gemini", ["s01_01"]))


class ProbeGeminiTests(unittest.TestCase):
    def test_gemini_entry_is_added_beside_the_local_one(self):
        fixture = json.loads(probe.FIXTURE.read_text(encoding="utf-8"))
        local = {"model": "qwen3vl_4b_int8_convrot.safetensors", "mode": "focused", "clip_type": "flux3",
                 "attempts": [], "results": [], "metrics": {
                     "images": 8, "valid_json_rate": 1.0, "accuracy": {"people_count": 0.25, "missing_glasses": 0.625},
                     "box_iou_mean": 0.0, "seconds_per_image": 4.4, "vram_peak_mb": 6789.0,
                     "detector_person_recall": 0.67, "layer1_seconds_per_image": 0.5, "questions_per_image": 3.2,
                     "ocr": False}}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "project"
            (output / "images" / "shots").mkdir(parents=True)
            for item in fixture["images"]:
                Image.new("RGB", (192, 108)).save(output / "images" / "shots" / f"{item['shot_id']}.jpg")
            report_path = Path(temp) / "r.json"
            stale = dict(local, model="old-gemini", backend="gemini")
            report_path.write_text(json.dumps({"version": 1, "mode": "focused", "findings": {
                "text_generate": True, "text_generate_takes_image": True, "clip_loader_types": ["flux3"],
                "candidates": [], "problems": []}, "models": [local, stale]}), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                report = probe.run_gemini(output, vlm=gemini("no"), detector=FakeDetector(fixture),
                                          report_path=report_path, sheet_path=Path(temp) / "sheet.jpg",
                                          progress=lambda _: None)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            accuracy = policy.measured_accuracy(report_path, backend="gemini")
            local_accuracy = policy.measured_accuracy(report_path)
        backends = [(entry["model"], entry.get("backend")) for entry in saved["models"]]
        self.assertEqual(backends, [("qwen3vl_4b_int8_convrot.safetensors", None), ("gemini-test", "gemini")])
        self.assertEqual(set(saved["models"][1]["metrics"]["accuracy"]),
                         {"people_count", "duplicate_person", "missing_glasses", "unstated_mark", "extra_person", "text"})
        self.assertIn("missing_glasses", accuracy)
        self.assertEqual(saved["models"][1]["calls"], 8)                  # one batched call per frame, all answered
        self.assertEqual(saved["models"][1]["attempts"], [])
        self.assertEqual(local_accuracy["missing_glasses"], 0.625)          # the local entry is kept
        lines = probe.summary_lines(report)
        self.assertIn(f"{'accuracy':<18}{'local':>8}{'gemini':>8}", lines)
        self.assertTrue(any(line.startswith("gemini-test (Gemini)") for line in lines))


if __name__ == "__main__":
    unittest.main()
