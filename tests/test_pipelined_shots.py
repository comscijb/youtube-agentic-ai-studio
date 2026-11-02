"""Phase 3.8-A: the shot planner (Gemini) and the renderer (ComfyUI) run at the same time (all mocked)."""
import io
import itertools
import json
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents import gemini_client
from agents import shot_list as shots_module
from tests import test_shot_planning as planning  # not imported by name: it would run twice
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import OUTLINE, SCRIPT, VISUAL_BIBLE, planned_shots, scene_responder
from video import shot_generation as gen

WAIT = 5            # seconds an event may take before the test fails instead of hanging


class FakeClient:
    def free_memory(self):
        pass


class FakeGenerator:
    """Writes a file per job and records which thread called "ComfyUI"; ``on_render`` hooks each call."""

    def __init__(self, on_render=None):
        self.client = FakeClient()
        self.rendered, self.threads = [], set()
        self.on_render = on_render

    def generate_with_retry(self, job, fallback=None, new_seeds=1):
        self.threads.add(threading.get_ident())
        cached = Path(job.output_path).is_file()
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_bytes(b"jpg")
        self.rendered.append(job.job_id)
        if self.on_render:
            self.on_render(job)
        return {"job_id": job.job_id, "provider": "comfyui", "seed": job.seed, "cache_hit": cached}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.case = planning.ShotListTests("test_budget_is_applied_before_planning")
        self.case.setUp()
        self.root = self.case.root
        self.plan = planned_shots(self.case.segments())
        self.ids = [shot["shot_id"] for shot in self.plan["shots"]]
        self.s01 = [sid for sid in self.ids if sid.startswith("s01")]
        self.s02 = [sid for sid in self.ids if sid.startswith("s02")]
        (self.root / "visual_bible.json").write_text(json.dumps(VISUAL_BIBLE), encoding="utf-8")

    def tearDown(self):
        self.case.tearDown()

    def run_pipeline(self, respond, generator, **kwargs):
        statuses = []
        hook = kwargs.pop("on_status", None)

        def on_status(snapshot):
            statuses.append(snapshot)
            if hook:
                hook(snapshot)
        clock = itertools.count()
        with patch.object(gen, "_artifacts", return_value=(SCRIPT, {}, {}, OUTLINE)), \
                patch.object(gen, "ensure_visual_bible", return_value=VISUAL_BIBLE), \
                patch.object(gen, "characters_to_approve", return_value=[]), \
                patch.object(gen.visual_qa, "enabled", return_value=False), \
                patch.object(gen, "missing_approvals", return_value=[]), \
                patch.object(shots_module, "generate", side_effect=respond), redirect_stdout(io.StringIO()):
            summary = gen.plan_and_render(self.root, generator, progress=lambda _: None,
                                          on_status=on_status, clock=lambda: float(next(clock)), **kwargs)
        return summary, statuses

    def test_rendering_starts_before_planning_finishes_in_order_on_one_thread(self):
        first_image = threading.Event()
        base = scene_responder(self.plan)

        def respond(prompt, **kwargs):
            if "SCENE_ID: s02" in prompt:                 # scene 2 is planned only once scene 1 is rendering
                self.assertTrue(first_image.wait(WAIT), "the renderer did not start before planning ended")
            return base(prompt, **kwargs)
        generator = FakeGenerator(on_render=lambda job: first_image.set())
        summary, statuses = self.run_pipeline(respond, generator)
        self.assertEqual(generator.rendered, self.ids)                           # scene/shot order kept
        self.assertEqual(generator.threads, {threading.get_ident()})             # the only ComfyUI caller
        self.assertEqual(summary["generated"], len(self.ids))
        self.assertEqual(summary["planned_scenes"], 2)
        saved = json.loads((self.root / shots_module.FILE_NAME).read_text(encoding="utf-8"))
        self.assertTrue(all(shot.get("image_path") for shot in saved["shots"]))
        self.assertEqual(statuses[-1]["images"], {"done": len(self.ids), "total": len(self.ids)})
        self.assertEqual(statuses[-1]["planning"]["done"], 2)

    def test_a_scene_the_planner_cannot_plan_is_flagged_and_the_others_render(self):
        base = scene_responder(self.plan)

        def respond(prompt, **kwargs):
            if "SCENE_ID: s01" in prompt:
                raise RuntimeError("Gemini unavailable")
            return base(prompt, **kwargs)
        generator = FakeGenerator()
        summary, _ = self.run_pipeline(respond, generator)
        self.assertEqual(generator.rendered, self.s02)
        self.assertIn("s01", summary["failed_scenes"])
        saved = json.loads((self.root / shots_module.FILE_NAME).read_text(encoding="utf-8"))
        flagged = [shot for shot in saved["shots"] if shot["shot_id"] in self.s01]
        self.assertTrue(all("planning failed" in shot["flags"][0] for shot in flagged))

    def test_stop_drains_and_a_rerun_resumes_from_checkpoints_and_cache(self):
        stop = threading.Event()
        base = scene_responder(self.plan)

        def stop_after_scene_one(snapshot):              # Stop pressed once scene 1 is planned
            if snapshot["planning"]["done"] == 1:
                stop.set()
        generator = FakeGenerator()
        summary, _ = self.run_pipeline(base, generator, stop=stop, on_status=stop_after_scene_one)
        self.assertTrue(summary["stopped"])
        self.assertLessEqual(len(generator.rendered), 1)                          # at most the current image
        self.assertTrue((self.root / ".checkpoints" / "shot_list" / "s01.json").is_file())
        self.assertFalse((self.root / shots_module.FILE_NAME).exists())

        calls = []

        def again(prompt, **kwargs):
            calls.append(prompt)
            return base(prompt, **kwargs)
        second = FakeGenerator()
        summary, _ = self.run_pipeline(again, second)
        self.assertEqual(len(calls), 1)                                          # s01 came from its checkpoint
        self.assertIn("SCENE_ID: s02", calls[0])
        self.assertEqual(second.rendered, self.ids)
        self.assertGreaterEqual(summary["cached"], len(generator.rendered))      # earlier images are cache hits

    def test_planner_concurrency_is_one_or_two(self):
        self.assertEqual([shots_module.planner_concurrency(value) for value in (None, 0, 1, 2, 5)], [1, 1, 1, 2, 2])
        both = threading.Barrier(2, timeout=WAIT)        # passes only if two scenes are with Gemini at once
        base = scene_responder(self.plan)

        def respond(prompt, **kwargs):
            both.wait()
            return base(prompt, **kwargs)
        generator = FakeGenerator()
        summary, _ = self.run_pipeline(respond, generator, concurrency=5)
        self.assertEqual(sorted(generator.rendered), sorted(self.ids))
        self.assertEqual(summary["planned_scenes"], 2)

    def test_status_shows_the_planner_waiting_on_gemini(self):
        base = scene_responder(self.plan)

        def respond(prompt, **kwargs):
            if "SCENE_ID: s01" in prompt:
                gemini_client._wait(30, "shot_list-s01", "gemini-test", "overloaded")
            return base(prompt, **kwargs)
        with patch.object(gemini_client, "_sleep"):
            _, statuses = self.run_pipeline(respond, FakeGenerator())
        self.assertIn(30, [item["planning"]["waiting_seconds"] for item in statuses])
        self.assertEqual(gemini_client.wait_listeners, [])                        # the hook is removed

    def test_a_current_shot_list_goes_straight_to_rendering(self):
        generator = FakeGenerator()
        self.run_pipeline(scene_responder(self.plan), generator)
        with patch.object(gen, "generate_shots", return_value={"generated": 0}) as direct:
            self.run_pipeline(scene_responder(self.plan), FakeGenerator())
        direct.assert_called_once()


if __name__ == "__main__":
    unittest.main()
