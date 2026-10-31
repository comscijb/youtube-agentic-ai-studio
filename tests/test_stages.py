"""Phase 3.8-B: one stage graph (dependencies, lanes) and a runner that overlaps independent stages."""
import threading
import unittest

from studio import projects, stages
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

WAIT = 5


class GraphTests(unittest.TestCase):
    def test_order_labels_dependencies_and_lanes(self):
        self.assertEqual(stages.STAGE_IDS, ("story_brief", "script", "narration", "music", "visual_bible",
                                            "references", "shots", "shot_review", "video", "shorts", "publish"))
        self.assertEqual(stages.get("shots").depends_on, ("narration", "visual_bible", "references"))
        self.assertEqual(stages.get("visual_bible").depends_on, ("script",))           # not on narration
        self.assertEqual({stage.id: stage.lane for stage in stages.STAGES if stage.lane == "gpu"},
                         {"references": "gpu", "shots": "gpu"})
        self.assertEqual(stages.get("shots").label, "Planos (ComfyUI)")
        self.assertEqual(stages.stage_for_step("images"), "shots")                      # legacy alias
        self.assertEqual(stages.downstream("visual_bible"), ["references", "shots", "shot_review", "video",
                                                             "shorts", "publish"])

    def test_projects_use_the_same_graph(self):
        self.assertEqual(projects.STAGE_KEYS, stages.STAGE_IDS)
        self.assertEqual(projects.UPSTREAM["video"], ("narration", "shots"))
        self.assertEqual(projects.steps_for("shots"), ("shots",))


class RunnerTests(unittest.TestCase):
    def test_narration_and_visual_bible_overlap_and_references_start_early(self):
        both = threading.Barrier(2, timeout=WAIT)
        references_started = threading.Event()
        order = []

        def narration():
            both.wait()                                  # visual bible is running at the same time
            self.assertTrue(references_started.wait(WAIT), "references waited for the narration")
            order.append("narration")

        def visual_bible():
            both.wait()
            order.append("visual_bible")

        def references():
            references_started.set()
            order.append("references")

        def shots():
            order.append("shots")
        result = stages.run(["narration", "visual_bible", "references", "shots"],
                            {"narration": narration, "visual_bible": visual_bible, "references": references,
                             "shots": shots})
        self.assertEqual(set(result.status.values()), {"done"})
        self.assertEqual(order[-1], "shots")                                   # waits for both and references

    def test_music_mixes_while_the_gpu_renders_and_one_gpu_job_at_a_time(self):
        rendering = threading.Event()
        music_done = threading.Event()
        active = {"gpu": 0, "peak": 0}
        lock = threading.Lock()

        def gpu(work=None):
            def run():
                with lock:
                    active["gpu"] += 1
                    active["peak"] = max(active["peak"], active["gpu"])
                if work:
                    work()
                with lock:
                    active["gpu"] -= 1
            return run

        def shots():
            rendering.set()
            self.assertTrue(music_done.wait(WAIT), "music did not run next to the shots")

        def music():
            self.assertTrue(rendering.wait(WAIT))
            music_done.set()
        result = stages.run(["music", "references", "shots"],
                            {"music": music, "references": gpu(), "shots": gpu(shots)})
        self.assertEqual(set(result.status.values()), {"done"})
        self.assertEqual(active["peak"], 1)

    def test_a_blocked_stage_skips_its_dependents_only(self):
        def blocked():
            raise stages.Blocked("Approve a reference portrait for: walter_hale")
        result = stages.run(["narration", "music", "shots", "video"],
                            {"narration": lambda: None, "music": lambda: None, "shots": blocked, "video": lambda: None})
        self.assertEqual(result.status, {"narration": "done", "music": "done", "shots": "blocked", "video": "skipped"})
        self.assertIsNone(result.first_error())

    def test_an_error_is_reported_and_stop_starts_nothing_new(self):
        def broken():
            raise RuntimeError("tts down")
        result = stages.run(["narration", "music"], {"narration": broken, "music": lambda: None})
        self.assertEqual(result.status["music"], "skipped")
        self.assertEqual(str(result.first_error()), "tts down")
        stop = threading.Event()
        stop.set()
        result = stages.run(["narration"], {"narration": lambda: self.fail("started after Stop")}, stop=stop)
        self.assertEqual(result.status, {"narration": "stopped"})

    def test_events_report_the_lane(self):
        events = []
        stages.run(["narration", "visual_bible"], {"narration": lambda: None, "visual_bible": lambda: None},
                   on_event=lambda stage, status, lane: events.append((stage, status, lane)))
        self.assertIn(("narration", "running", "network"), events)
        self.assertIn(("visual_bible", "done", "network"), events)


if __name__ == "__main__":
    unittest.main()
