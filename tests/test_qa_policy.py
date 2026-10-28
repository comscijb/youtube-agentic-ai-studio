"""Phase 3.5.1-B: QA as advice — only reliable deterministic checks act on their own by default."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_qa_pipeline import Detector, GlassesVLM, LoggingComfy
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator
from video.qa import base, decision, fixes, policy
from video.qa import deterministic as det
from video.qa.layered import LayeredQA

GLASSES = base.issue("missing_marker", box=[0.4, 0.1, 0.5, 0.3], target="julian_vance", source="local_vlm",
                     note="face without black horn-rimmed glasses")
DUPLICATE = base.issue("duplicate_person", box=[0.6, 0.3, 0.8, 1.0], source="deterministic", note="same face twice")
EXTRA = base.issue("extra_person", box=[0.7, 0.4, 0.75, 0.7], source="deterministic")
BORDER = base.issue("border", note="paper margins", source="deterministic")
OCR_TEXT = base.issue("text", box=[0.1, 0.1, 0.3, 0.2], note="lettering 'ABC'", source="ocr")
VLM_TEXT = base.issue("text", note="visible writing", source="local_vlm")
BLANK = base.issue("blank", source="deterministic")


def severities(issues):
    return {(item["type"], item["source"]): item["severity"] for item in issues}


class PolicyTests(unittest.TestCase):
    def test_default_mode_is_advisory(self):
        self.assertEqual(config.IMAGEGEN_QA_MODE, "advisory")
        self.assertEqual(policy.mode(), "advisory")
        self.assertEqual(policy.mode("nonsense"), "advisory")

    def test_advisory_keeps_only_reliable_checks_blocking(self):
        issues = policy.apply([GLASSES, DUPLICATE, EXTRA, BORDER, OCR_TEXT, VLM_TEXT, BLANK], "advisory")
        self.assertEqual(severities(issues), {
            ("missing_marker", "local_vlm"): "advisory", ("duplicate_person", "deterministic"): "advisory",
            ("extra_person", "deterministic"): "advisory", ("border", "deterministic"): "blocking",
            ("text", "ocr"): "blocking", ("text", "local_vlm"): "advisory", ("blank", "deterministic"): "blocking"})
        hint = issues[0]
        self.assertEqual((hint["hint"], hint["raw_severity"]), (True, "blocking"))
        self.assertEqual(decision.verdict({"issues": policy.apply([GLASSES, EXTRA], "advisory")})[0], "pass")

    def test_auto_acts_only_on_types_measured_at_85_percent(self):
        accuracy = {"missing_glasses": 0.875, "extra_person": 0.75, "duplicate_person": 1.0}
        issues = policy.apply([GLASSES, EXTRA, DUPLICATE], "auto", accuracy)
        self.assertEqual([item["severity"] for item in issues], ["blocking", "advisory", "advisory"])
        self.assertIn("low confidence", issues[2]["note"])           # never automatic in this style

    def test_hints_are_promoted_again_when_the_mode_allows(self):
        hinted = policy.apply([GLASSES], "advisory")
        self.assertEqual(policy.apply(hinted, "auto", {"missing_glasses": 0.9})[0]["severity"], "blocking")
        self.assertEqual(policy.apply(hinted, "advisory"), hinted)    # idempotent

    def test_measured_accuracy_reads_the_latest_probe_per_backend(self):
        report = {"mode": "focused", "models": [
            {"model": config.IMAGEGEN_QA_VLM_MODEL, "mode": "focused", "metrics": {"accuracy": {"text": 1.0}}},
            {"model": "gemini-flash", "backend": "gemini", "mode": "focused",
             "metrics": {"accuracy": {"text": 0.5}}}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "report.json")
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(policy.measured_accuracy(path), {"text": 1.0})
            self.assertEqual(policy.measured_accuracy(path, backend="gemini"), {"text": 0.5})
            self.assertEqual(policy.measured_accuracy(Path(temp, "missing.json")), {})

    def test_stored_results_are_re_evaluated_under_the_mode(self):
        shot = {"shot_id": "s01_01", "qa": {"version": base.QA_VERSION, "status": "fail", "verdict": "edit",
                                            "issues": [GLASSES, EXTRA], "edits": 0}}
        qa = LayeredQA(["deterministic"], detector=Detector(), progress=lambda _: None, mode="advisory")
        self.assertTrue(fixes.reapply_policy(shot, qa))
        self.assertEqual((shot["qa"]["status"], shot["qa"]["verdict"]), ("pass", "pass"))
        self.assertFalse(fixes.needs_fix(shot))
        self.assertTrue(fixes.hinted(shot))
        shot["qa"]["hints_dismissed"] = True
        self.assertFalse(fixes.hinted(shot))


class ReliableChecksTests(unittest.TestCase):
    def test_blank_frame_is_found(self):
        self.assertEqual([item["type"] for item in det.blank_issues(Image.new("RGB", (320, 180), (9, 9, 9)))],
                         ["blank"])
        noisy = Image.effect_noise((320, 180), 60).convert("RGB")
        self.assertEqual(det.blank_issues(noisy), [])

    def test_corrupt_file_regenerates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "s01_01.jpg")
            path.write_bytes(b"not a jpeg")
            qa = LayeredQA(["deterministic"], detector=Detector(), progress=lambda _: None)
            result = qa.review(path, {"people": 1, "background": 0, "characters": [], "narration": ""})
        self.assertEqual((result["status"], result["verdict"], result["issues"][0]["type"]),
                         ("fail", "regenerate", "corrupt"))


class AdvisoryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
        atomic_write_json(self.output / "shot_list.json",
                          {"version": 2, "shots": [dict(SHOTS[0], others_present=False)], "unique_shots": 1})
        self.log = []
        self.patches = [patch.object(config, "IMAGEGEN_VISUAL_QA", True),
                        patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False),
                        patch.object(gen, "missing_approvals", return_value=[]),
                        patch("agents.visual_bible.approved_reference", return_value=None),
                        patch("video.qa.deterministic.blank_issues", new=lambda image: [])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_gen(self, **kwargs):
        client = ComfyClient("http://127.0.0.1:8188", session=LoggingComfy(self.log), sleep=lambda _: None)
        qa = LayeredQA(["deterministic", "local_vlm"], detector=Detector(), vlm=GlassesVLM(self.log, ["no"]),
                       comfy=client, progress=lambda _: None, **kwargs)
        with redirect_stdout(io.StringIO()):
            summary = gen.generate_shots(self.output, ImageGenerator(self.output, client), qa=qa,
                                         progress=lambda _: None)
        return summary, load_json(self.output / "shot_list.json")["shots"][0]

    def test_vlm_signals_become_hints_and_nothing_is_edited(self):
        summary, shot = self.run_gen()
        self.assertNotIn("klein edit", self.log)
        self.assertEqual((summary["qa_edits"], summary["qa_regenerations"]), (0, 0))
        self.assertEqual((summary["qa_mode"], summary["qa_hints"]), ("advisory", [shot["shot_id"]]))
        self.assertEqual((shot["qa"]["status"], shot["qa"]["verdict"]), ("pass", "pass"))
        self.assertTrue(shot["qa"]["issues"][0]["hint"])

    def test_off_mode_runs_no_qa(self):
        client = ComfyClient("http://127.0.0.1:8188", session=LoggingComfy(self.log), sleep=lambda _: None)
        with redirect_stdout(io.StringIO()):
            summary = gen.generate_shots(self.output, ImageGenerator(self.output, client), progress=lambda _: None,
                                         qa_mode="off")
        self.assertNotIn("qa_checked", summary)
        self.assertNotIn("qa", load_json(self.output / "shot_list.json")["shots"][0])

    def test_cli_prints_hints_and_the_gui_review_command(self):
        from scripts import generate_shots as cli
        summary = {"generated": 1, "cached": 0, "fallback": 0, "failed": [], "seconds": 6.0, "qa_checked": 1,
                   "qa_edits": 0, "qa_regenerations": 0, "qa_failed": [], "qa_flagged": [], "qa_mode": "advisory",
                   "qa_hints": ["s01_01"]}
        printed = io.StringIO()
        with redirect_stdout(printed), patch.object(cli.projects, "cli_output", return_value=self.output), \
                patch.object(cli.gen, "ensure_shot_list", return_value={"shots": [], "unique_shots": 0}), \
                patch.object(cli.gen, "estimate_gpu_seconds", return_value=0), \
                patch.object(cli.gen, "generate_shots", return_value=summary) as run, \
                patch.object(cli, "ImageGenerator"):
            cli.main(["--qa", "advisory", "--no-thumbnail"])
        self.assertEqual(run.call_args.kwargs["qa_mode"], "advisory")
        self.assertIn("QA hints to review (1 shot(s)): s01_01", printed.getvalue())
        self.assertIn("python gui.py", printed.getvalue())
        self.assertNotIn("--fix-only", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
