"""Phase 3.8-C: the GUI renders the one stage list; no legacy "images" step; the Novo projeto wizard."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import premise_brief
from studio import projects
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

PAGE = Path("gui_templates/index.html")


class StageApiTests(unittest.TestCase):
    def setUp(self):
        import gui
        self.gui = gui
        self.client = gui.app.test_client()

    def test_stages_order_labels_and_dependencies(self):
        data = self.client.get("/api/stages").get_json()
        labels = [stage["label"] for stage in data["stages"]]
        self.assertEqual(labels, ["Premissa / Story Brief", "Roteiro (outline + cenas + revisão)", "Narração",
                                  "Música", "Visual bible", "Referências (aprovação)", "Planos (ComfyUI)",
                                  "Revisão dos planos (visualizador)", "Vídeo", "Shorts", "Publicação"])
        shots = next(stage for stage in data["stages"] if stage["id"] == "shots")
        self.assertEqual((shots["depends_on"], shots["lane"], shots["position"]),
                         (["narration", "visual_bible", "references"], "gpu", 7))
        self.assertEqual(data["aliases"], {"images": "shots"})

    def test_an_old_images_request_runs_shots_with_a_warning(self):
        with patch.object(self.gui.threading, "Thread") as thread:
            response = self.client.post("/api/run", json={"steps": ["images", "narration", "shots"],
                                                         "target_duration": 12})
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(thread.call_args.kwargs["args"][0], ["shots", "narration"])
        logs = []
        while not self.gui._log_queue.empty():
            logs.append(self.gui._log_queue.get()[1])
        self.assertTrue(any("The 'images' step is now 'shots'" in line for line in logs))


class TemplateTests(unittest.TestCase):
    def test_no_hard_coded_or_legacy_steps(self):
        page = PAGE.read_text(encoding="utf-8")
        for legacy in ('id="step-images"', 'id="step-research"', "Steps to run", 'id="stage-research"',
                       "Generate images", "startGeneration"):
            self.assertNotIn(legacy, page)
        for current in ('id="sidebar-stages"', "loadStages()", "fetch('/api/stages')", "Rodar a partir daqui",
                        "aguardando aprovação", "renderLanes(d)", "createProject()", "🚀 Novo projeto"):
            self.assertIn(current, page)
        project = page[page.index('id="tab-project"'):page.index('id="tab-performance"')]
        self.assertIn('id="console"', project)                     # stages run (and log) in the Projeto view
        self.assertIn('id="stop-btn"', project)


class WizardAndRunTests(unittest.TestCase):
    def setUp(self):
        import gui
        self.gui = gui
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(projects, "root", return_value=self.root),
                        patch.object(config, "OUTPUT_DIR", str(self.root / "none")),
                        patch.object(gui, "_reload_state_from_disk")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        projects.activate(Path(config.OUTPUT_DIR))
        self.temp.cleanup()

    def test_novo_projeto_saves_the_choices_and_story_brief_uses_them(self):
        client = self.gui.app.test_client()
        made = client.post("/api/projects/new", json={"premise": "A man attends his own retirement party.",
                                                      "focus_angle": "", "narrative_type": "dark_human_parable",
                                                      "target_duration": 15, "narrative_review": False}).get_json()
        folder = self.root / made["slug"]
        data = projects.load(folder)
        self.assertEqual((data["premise"], data["narrative_type"], data["narrative_review"]),
                         ("A man attends his own retirement party.", "dark_human_parable", False))
        with patch.object(config, "OUTPUT_DIR", str(folder)), patch.object(self.gui.threading, "Thread") as thread:
            client.post("/api/projects/stage/story_brief", json={"action": "run"})
        args = thread.call_args.kwargs["args"]
        self.assertEqual((args[0], args[1], args[4], args[6]),
                         (["research"], "A man attends his own retirement party.", "dark_human_parable", False))

    def test_a_brief_goes_to_premise_brief_and_sets_the_duration(self):
        client = self.gui.app.test_client()
        made = client.post("/api/projects/new", json={"premise": premise_brief.TEMPLATE, "target_duration": 18}).get_json()
        folder = self.root / made["slug"]
        self.assertEqual(projects.load(folder)["target_duration_minutes"], 12.0)
        self.assertTrue((folder / premise_brief.MARKDOWN_FILE).is_file())
        self.assertEqual(projects.load(folder)["premise"], "")

    def test_run_from_here_includes_every_later_stage(self):
        folder = projects.create("Test")
        with patch.object(config, "OUTPUT_DIR", str(folder)), patch.object(self.gui.threading, "Thread") as thread:
            response = self.gui.app.test_client().post("/api/projects/stage/narration",
                                                        json={"action": "from_here", "confirm": True})
        self.assertEqual(response.get_json()["steps"], ["narration", "music", "shots", "video", "shorts"])
        thread.assert_called_once()

    def test_runs_record_time_and_duration(self):
        folder = projects.create("Test")
        with patch.object(config, "OUTPUT_DIR", str(folder)), patch.object(self.gui.time, "time", side_effect=[100.0, 130.5]), \
                redirect_stdout(io.StringIO()):
            self.gui._stage("narration", "running")
            self.gui._stage("narration", "done")
        run = projects.summary(folder)["runs"]["narration"]
        self.assertEqual((run["seconds"], run["status"]), (30.5, "done"))


if __name__ == "__main__":
    unittest.main()
