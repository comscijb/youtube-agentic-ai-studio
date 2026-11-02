"""Phase 3.5-A: the local-VLM feasibility probe, with an in-memory ComfyUI (no network, no models)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image

from scripts import probe_local_vlm as probe
from tests.fake_comfy import OBJECT_INFO, FakeComfy, Response
from video.imagegen_comfyui import ComfyClient

FIXTURE = json.loads(probe.FIXTURE.read_text(encoding="utf-8"))
VLM = "qwen3vl_4b_int8_convrot.safetensors"
UPSAMPLER = "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors"
INFO = {**OBJECT_INFO,
        "PreviewAny": {"input": {"required": {"source": ["*", {}]}}},
        "TextGenerate": {"input": {
            "required": {"prompt": ["STRING", {}], "clip": ["CLIP"], "max_length": ["INT", {}],
                         "sampling_mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                             {"key": "on", "inputs": {}}, {"key": "off", "inputs": {}}]}]},
            "optional": {"image": ["IMAGE"], "system_prompt": ["STRING", {}], "thinking": ["BOOLEAN"],
                         "use_default_template": ["BOOLEAN"], "mtp": [["off", "on"]]}}}}


def perfect(item) -> str:
    """The answer a perfect model would give for a fixture frame (boxes on Qwen's 0-1000 grid)."""
    truth, issues = item["truth"], []
    for kind, boxes in truth["boxes"].items():
        for box in boxes:
            target = "julian_vance" if kind in ("missing_marker", "duplicate_person") else ""
            issues.append({"type": kind, "severity": "blocking", "box": [round(v * 1000) for v in box],
                           "target": target, "note": "no glasses" if kind == "missing_marker" else kind})
    return "<think></think>```json\n" + json.dumps({"people_count": truth["people_count"], "issues": issues,
                                                     "verdict": "edit" if issues else "pass"}) + "\n```"


class FakeVlmComfy(FakeComfy):
    def __init__(self, answers, **kwargs):
        super().__init__(object_info=INFO, **kwargs)
        self.answers = list(answers)
        self.order = []

    def request(self, method, url, **kwargs):
        path = url.split("8188", 1)[-1]
        if path == "/free":
            self.order.append("free")
        if path == "/prompt":
            graph = kwargs["json"]["prompt"]
            self.order.append("vlm" if any(n["class_type"] == "TextGenerate" for n in graph.values()) else "klein")
        if path.startswith("/history/") and self.order and self.order[-1] == "vlm":
            self.calls.append((method, path))
            prompt_id = path.rsplit("/", 1)[-1]
            text = self.answers.pop(0) if self.answers else "sorry"
            return Response({prompt_id: {"status": {"status_str": "success", "completed": True},
                                         "outputs": {"4": {"text": [text]}}}})
        return super().request(method, url, **kwargs)


class ProbeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.output = base / "project"
        shots = self.output / "images" / "shots"
        shots.mkdir(parents=True)
        for number, item in enumerate(FIXTURE["images"]):
            Image.new("RGB", (192, 108), (number * 20, 40, 40)).save(shots / f"{item['shot_id']}.jpg")
        self.report, self.sheet = base / "data" / "qa_probe_report.json", base / "data" / "sheet.jpg"

    def tearDown(self):
        self.temp.cleanup()

    def run_probe(self, fake, **kwargs):
        client = ComfyClient(session=fake, sleep=lambda _: None)
        with redirect_stdout(io.StringIO()):
            return probe.run(self.output, client=client, report_path=self.report, sheet_path=self.sheet, **kwargs)


class InspectionTests(unittest.TestCase):
    def test_reports_what_textgenerate_and_the_loaders_offer(self):
        findings = probe.inspect(INFO)
        self.assertTrue(findings["text_generate_takes_image"])
        self.assertEqual(findings["sampling_modes"], ["on", "off"])
        self.assertEqual(findings["candidates"], [VLM, UPSAMPLER])
        self.assertEqual(findings["problems"], [])
        self.assertEqual(probe.clip_types(findings), ["flux3"])

    def test_missing_textgenerate_is_reported_not_guessed(self):
        info = {key: value for key, value in INFO.items() if key != "TextGenerate"}
        findings = probe.inspect(info)
        self.assertFalse(findings["text_generate"])
        self.assertIn("TextGenerate is not installed", findings["problems"][0])

    def test_graph_uses_only_listed_inputs_and_greedy_decoding(self):
        findings = probe.inspect(INFO)
        graph = probe.build_graph(findings, VLM, "flux3", "pg_x.png", "check")
        inputs = graph["3"]["inputs"]
        self.assertEqual((inputs["sampling_mode"], inputs["thinking"], inputs["image"]), ("off", False, ["2", 0]))
        self.assertFalse([key for key in inputs if key.startswith("sampling_mode.")])
        from video import imagegen_templates as templates
        self.assertEqual(templates.validate_graph(graph, INFO), [])


class AnswerTests(unittest.TestCase):
    def test_parse_normalizes_boxes_and_rejects_prose(self):
        answer, error = probe.parse_answer(perfect(FIXTURE["images"][0]))
        self.assertEqual(error, "")
        self.assertEqual(answer["issues"][0]["box"], [0.19, 0.07, 0.39, 0.98])
        self.assertEqual(probe.parse_answer("The image shows a man.")[1], "no JSON object")
        self.assertEqual(probe.parse_answer('{"verdict": "pass"}')[1], "JSON without people_count/issues")

    def test_perfect_answers_score_full_marks(self):
        results = [probe._result(item["shot_id"], perfect(item), 2.0, 5000.0) for item in FIXTURE["images"]]
        metrics = probe.score(results, FIXTURE)
        self.assertEqual(metrics["valid_json_rate"], 1.0)
        self.assertEqual(set(metrics["accuracy"].values()), {1.0})
        self.assertEqual(metrics["box_iou_mean"], 1.0)

    def test_invalid_answers_count_against_accuracy(self):
        results = [probe._result(item["shot_id"], "no idea", 2.0, None) for item in FIXTURE["images"]]
        metrics = probe.score(results, FIXTURE)
        self.assertEqual((metrics["valid_json_rate"], metrics["accuracy"]["text"]), (0.0, 0.0))


class LiveRunTests(ProbeTestCase):
    def test_full_probe_with_swap_writes_report_and_contact_sheet(self):
        answers = [perfect(item) for item in FIXTURE["images"]]
        fake = FakeVlmComfy(answers)
        report = self.run_probe(fake, models=[VLM])
        model = report["models"][0]
        self.assertEqual((model["clip_type"], len(model["results"])), ("flux3", 8))
        self.assertEqual(model["metrics"]["valid_json_rate"], 1.0)
        self.assertEqual(set(model["swap"]), {"klein_warmup_s", "unload_klein_s", "qa_load_and_run_s",
                                              "unload_qa_s", "reload_klein_s", "total_swap_s"})
        # Klein and the VLM are never resident together: /free separates them both ways.
        self.assertEqual(fake.order, ["klein", "free"] + ["vlm"] * 8 + ["free", "klein"])
        self.assertTrue(self.sheet.is_file())
        saved = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(saved["models"][0]["model"], VLM)
        self.assertTrue(any("valid JSON 100%" in line for line in probe.summary_lines(saved)))

    def test_a_model_that_fails_is_reported_and_the_next_is_tried(self):
        fake = FakeVlmComfy([perfect(item) for item in FIXTURE["images"]])
        fake.object_info = {**INFO, "CLIPLoader": {"input": {"required": {
            "clip_name": [[VLM]], "type": [["flux3"]]}}}}
        report = self.run_probe(fake, models=[UPSAMPLER, VLM], swap=False)
        failed, worked = report["models"]
        self.assertIsNone(failed["clip_type"])
        self.assertIn("not available", failed["attempts"][0]["error"])
        self.assertEqual(worked["metrics"]["accuracy"]["duplicate_person"], 1.0)

    def test_without_textgenerate_nothing_is_queued(self):
        fake = FakeVlmComfy([])
        fake.object_info = {key: value for key, value in INFO.items() if key != "TextGenerate"}
        report = self.run_probe(fake)
        self.assertEqual((report["models"], fake.prompts), ([], []))
        self.assertTrue(self.report.is_file())


if __name__ == "__main__":
    unittest.main()
