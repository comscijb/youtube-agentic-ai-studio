"""Phase 3.5.1-C: one-click human edits — queued requests, one Klein batch, undo (ComfyUI mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_region_edit import INFO
from tests.test_shot_planning import VISUAL_BIBLE
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob
from video.qa import base, fixes, review

VB = json.loads(json.dumps(VISUAL_BIBLE))


class RequestTests(unittest.TestCase):
    def test_requests_are_validated(self):
        with self.assertRaises(ValueError):
            review.make_request("remove_figure", VB)                       # a figure needs a box
        with self.assertRaises(ValueError):
            review.make_request("add_marker", VB, character="julian_vance", marker="a hat")
        with self.assertRaises(ValueError):
            review.make_request("custom", VB, text="  ")
        with self.assertRaises(ValueError):
            review.make_request("paint", VB)
        with self.assertRaises(ValueError):
            review.make_request("remove_marks", VB, box=[0.5, 0.5, 0.2, 0.9])
        entry = review.make_request("add_marker", VB, character="julian_vance", marker="horn-rimmed glasses")
        self.assertEqual((entry["kind"], entry["label"], "box" in entry), ("add_marker", "Adicionar marcador", False))

    def test_plans_reuse_the_qa_instructions(self):
        figure = review.plan_for(review.make_request("remove_figure", VB, box=[0.7, 0.4, 0.8, 0.8]), VB, ".")
        self.assertIn("Remove the figure in <obj_1> and continue the background", figure["instruction"])
        self.assertEqual(figure["boxes"][0][0], [0.7, 0.4, 0.8, 0.8])
        with patch("agents.visual_bible.approved_reference", return_value="character_refs/julian_vance.png"):
            marker = review.plan_for(review.make_request("add_marker", VB, character="julian_vance",
                                                         marker="horn-rimmed glasses"), VB, ".")
        self.assertIn("add the horn-rimmed glasses of the man in the second image", marker["instruction"])
        self.assertEqual(marker["references"], ["character_refs/julian_vance.png"])
        marks = review.plan_for(review.make_request("remove_marks", VB), VB, ".")
        self.assertEqual(marks["boxes"], [])                                # instruction-only
        self.assertIn("every person's skin", marks["instruction"])

    def test_ignore_one_hint_or_mark_the_shot_ok(self):
        hint = lambda kind, **kwargs: dict(base.issue(kind, severity="advisory", **kwargs), hint=True)
        shot = {"shot_id": "s01_01", "qa": {"status": "pass", "verdict": "pass", "issues": [
            hint("extra_person", box=[0.7, 0.4, 0.8, 0.8]), hint("missing_marker"),
            base.issue("text", severity="advisory", note="visible writing (unclear)")]}}
        self.assertTrue(fixes.hinted(shot))
        review.mark(shot, "ignore", 0)
        self.assertTrue(fixes.hinted(shot))
        review.mark(shot, "ignore", 1)
        self.assertFalse(fixes.hinted(shot))
        other = {"shot_id": "s01_02", "qa": {"status": "pass", "issues": [hint("text")]}}
        review.mark(other, "ok")
        self.assertEqual((other["qa"]["reviewed"], fixes.hinted(other)), (True, False))
        with self.assertRaises(IndexError):
            review.mark(other, "ignore", 5)


class QueueRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.patches = [patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False),
                        patch("agents.visual_bible.approved_reference", return_value=None)]
        for item in self.patches:
            item.start()
        self.shots = []
        for shot_id, colour in (("s01_01", (10, 10, 10)), ("s01_02", (90, 90, 90))):
            image = self.output / "images" / "shots" / f"{shot_id}.jpg"
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1920, 1080), colour).save(image)
            self.shots.append({"shot_id": shot_id, "image_path": f"images/shots/{shot_id}.jpg",
                               "qa": {"status": "pass", "verdict": "pass", "issues": []}})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_queue(self, sizes):
        fake = FakeComfy(object_info=INFO, sizes=sizes)
        generator = ImageGenerator(self.output, ComfyClient("http://127.0.0.1:8188", session=fake, sleep=lambda _: None))
        shot_list = {"shots": self.shots}
        saves = []
        factory = lambda shot: (lambda offset: ImageJob(job_id=shot["shot_id"], prompt="a man at a table",
                                                        seed=100 + offset, output_path=str(self.output / shot["image_path"]),
                                                        size=tuple(config.IMAGEGEN_SIZE)))
        with redirect_stdout(io.StringIO()):
            summary = review.run_queue(generator, self.output, VB, shot_list, job_factory=factory,
                                       save=lambda: saves.append(1), progress=lambda _: None)
        return summary, fake, saves

    def test_every_queued_request_runs_in_one_batch_with_history(self):
        review.enqueue(self.shots[0], review.make_request("remove_figure", VB, box=[0.7, 0.4, 0.8, 0.8]))
        review.enqueue(self.shots[0], review.make_request("custom", VB, text="darken the window"))
        review.enqueue(self.shots[1], review.make_request("regenerate", VB))
        summary, fake, saves = self.run_queue([(1360, 768), (1360, 768), (1280, 720)])
        self.assertEqual((summary["edits"], summary["regenerations"], summary["failed"]), (2, 1, []))
        self.assertEqual(len(fake.prompts), 3)
        self.assertEqual([("33" in graph) for graph in fake.prompts], [True, True, False])
        self.assertEqual(fake.freed, 0)                                     # Klein never unloaded mid-batch
        self.assertEqual(len(saves), 3)
        first = self.shots[0]
        self.assertNotIn("edit_queue", first)
        self.assertEqual([a["request"] for a in first["qa"]["actions"]], ["Remover figura", "Instrução livre"])
        self.assertEqual((first["qa"]["human_edits"], first["qa"].get("edits", 0)), (2, 0))   # QA budget untouched
        self.assertEqual(len(first["qa"]["history"]), 2)
        self.assertFalse(first["qa"]["reviewed"])

    def test_a_failed_edit_keeps_the_original_and_consumes_the_request(self):
        review.enqueue(self.shots[0], review.make_request("remove_marks", VB))
        image = self.output / self.shots[0]["image_path"]
        before = image.read_bytes()
        summary, _, _ = self.run_queue([(1024, 1024)])
        self.assertEqual(summary["failed"], ["s01_01"])
        self.assertEqual(image.read_bytes(), before)
        self.assertNotIn("edit_queue", self.shots[0])
        self.assertEqual(self.shots[0]["qa"]["actions"][-1]["action"], "failed")

    def test_undo_restores_any_earlier_version(self):
        review.enqueue(self.shots[0], review.make_request("remove_marks", VB))
        image = self.output / self.shots[0]["image_path"]
        original = Image.open(image).convert("RGB").getpixel((5, 5))
        self.run_queue([(1360, 768)])
        action = fixes.restore_version(self.output, self.shots[0], image, 1)
        self.assertEqual((action["version"], action["replaced_version"]), (1, 2))
        self.assertEqual(Image.open(image).convert("RGB").getpixel((5, 5)), original)
        self.assertEqual(len(self.shots[0]["qa"]["history"]), 2)           # the edited version is kept too
        with self.assertRaises(FileNotFoundError):
            fixes.restore_version(self.output, self.shots[0], image, 9)


if __name__ == "__main__":
    unittest.main()
