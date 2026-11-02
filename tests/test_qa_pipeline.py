"""Phase 3.5-E: phase-batched generation — Klein and the QA VLM never resident together; resume; fix-only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from tests.qa_modes import auto_mode
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_region_edit import INFO
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator
from video.qa.layered import LayeredQA


def person(box, face=None):
    return {"box": box, "score": 0.9, "face": {"box": face, "score": 0.9} if face else None}


class Detector:
    def available(self):
        return True

    def detect(self, path):
        return {"persons": [person([0.3, 0.1, 0.6, 1.0], [0.4, 0.15, 0.5, 0.3])], "unsure": [], "face_pairs": [],
                "seconds": 0.0}


class LoggingComfy(FakeComfy):
    def __init__(self, log):
        super().__init__(object_info=INFO)
        self.log = log

    def request(self, method, url, **kwargs):
        path = url.split("8188", 1)[-1]
        if path == "/prompt":
            graph = kwargs["json"]["prompt"]
            self.log.append("klein edit" if "33" in graph else "klein")
        if path == "/free" and kwargs.get("json", {}).get("unload_models"):
            self.log.append("unload all")
        return super().request(method, url, **kwargs)


class GlassesVLM:
    """Answers "no glasses" until the shot was edited ``fixed_after`` times (read from the shot list)."""
    name = "comfy_textgenerate"

    def __init__(self, log, answers):
        self.log, self.answers = log, list(answers)

    def available(self):
        return True

    def ask(self, questions):
        self.log.append("vlm")
        reply = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return {q.qid: (reply if q.kind == "marker" else "no") for q in questions}

    def unload(self):
        self.log.append("unload vlm")


@auto_mode
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
        shots = [dict(SHOTS[0], others_present=False), dict(SHOTS[0], shot_id="s01_03", others_present=False)]
        atomic_write_json(self.output / "shot_list.json", {"version": 2, "shots": shots, "unique_shots": 2})
        self.log = []
        self.fake = LoggingComfy(self.log)
        self.patches = [patch.object(config, "IMAGEGEN_VISUAL_QA", True),
                        patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False),
                        patch.object(gen, "missing_approvals", return_value=[]),
                        patch("agents.visual_bible.approved_reference", return_value=None)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_gen(self, answers, **kwargs):
        client = ComfyClient("http://127.0.0.1:8188", session=self.fake, sleep=lambda _: None)
        qa = LayeredQA(["deterministic", "local_vlm"], detector=Detector(), vlm=GlassesVLM(self.log, answers),
                       comfy=client, progress=lambda _: None)
        with redirect_stdout(io.StringIO()):
            summary = gen.generate_shots(self.output, ImageGenerator(self.output, client), qa=qa,
                                         progress=lambda _: None, **kwargs)
        return summary, load_json(self.output / "shot_list.json")["shots"]

    def assert_never_together(self):
        """Between the VLM's use and its unload nothing is queued, and Klein is unloaded before it."""
        for index, event in enumerate(self.log):
            if event == "vlm":
                self.assertEqual(self.log[index - 1], "unload all")
                self.assertEqual(self.log[index + 1], "unload vlm")

    def test_generate_all_then_qa_batch_then_edit_then_recheck(self):
        summary, shots = self.run_gen(["no", "yes"])
        self.assertEqual(self.log, ["klein", "klein", "unload all", "vlm", "unload vlm",
                                    "klein edit", "klein edit", "unload all", "vlm", "unload vlm"])
        self.assert_never_together()
        self.assertEqual((summary["qa_edits"], summary["qa_failed"], summary["qa_checked"]), (2, [], 4))
        for shot in shots:
            qa = shot["qa"]
            self.assertEqual((qa["status"], qa["verdict"], qa["edits"]), ("pass", "pass", 1))
            self.assertEqual(qa["actions"][0]["action"], "edit")
            self.assertIn("horn-rimmed glasses", qa["actions"][0]["instruction"])
            self.assertTrue((self.output / qa["history"][0]["path"]).is_file())
        record = next(item for item in load_json(self.output / "asset_manifest.json")["generated_assets"]
                      if item["job_id"] == "s01_01")
        self.assertEqual(record["visual_qa"]["verdict"], "pass")
        self.assertEqual(record["qa_actions"][0]["action"], "edit")

    def test_rounds_are_bounded_and_fix_only_resumes(self):
        with patch.object(config, "IMAGEGEN_QA_ROUNDS", 1):
            summary, shots = self.run_gen(["no"])
        self.assertEqual(summary["qa_failed"], ["s01_01", "s01_03"])           # 1 round: edited, still failing
        self.assertEqual(self.log.count("klein"), 2)
        self.log.clear()
        summary, shots = self.run_gen(["no", "yes"], fix_only=True)
        # Fixes first (QA is stored), no pending shot is generated; the 2nd edit still fails, so with
        # the edit budget spent the next fix is a new seed (a plain Klein render), then QA passes.
        self.assertEqual(self.log, ["klein edit", "klein edit", "unload all", "vlm", "unload vlm",
                                    "klein", "klein", "unload all", "vlm", "unload vlm"])
        self.assertEqual([(shot["qa"]["edits"], shot["qa"]["regenerations"], shot["qa"]["status"])
                          for shot in shots], [(2, 1, "pass")] * 2)
        self.assert_never_together()

    def test_spent_budget_keeps_the_best_and_flags(self):
        with patch.object(config, "IMAGEGEN_QA_MAX_EDITS", 0), patch.object(config, "IMAGEGEN_QA_RETRIES", 0):
            summary, shots = self.run_gen(["no"])
        self.assertEqual(summary["qa_flagged"], ["s01_01", "s01_03"])
        self.assertTrue(all(shot["qa"]["flagged"] for shot in shots))
        self.assertEqual(self.log.count("klein edit"), 0)

    def test_qa_disabled_generates_only(self):
        with patch.object(config, "IMAGEGEN_VISUAL_QA", False):
            summary, shots = self.run_gen(["no"])
        self.assertEqual(self.log, ["klein", "klein"])
        self.assertNotIn("qa_checked", summary)
        self.assertTrue(all("qa" not in shot for shot in shots))


if __name__ == "__main__":
    unittest.main()
