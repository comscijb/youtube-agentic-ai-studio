"""Phase 3.9.1-C: a clean "Novo projeto" flow — no archived project at startup, a blank draft, one
activation that every panel follows (Flask test client, no live calls)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from studio import projects
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401


class ProjectStateTests(unittest.TestCase):
    def setUp(self):
        import gui
        self.gui = gui
        self.temp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp.name, "gui_state.json")
        self.patches = [patch.object(config, "PROJECTS_DIR", os.path.join(self.temp.name, "projects")),
                        patch.object(config, "GUI_STATE_FILE", str(self.state_file), create=True),
                        patch.object(config, "OUTPUT_DIR", config.OUTPUT_DIR),
                        patch.object(config, "IMAGES_DIR", config.IMAGES_DIR),
                        patch.dict(gui._state, {"running": False, "imagegen_busy": False, "draft": False})]
        for item in self.patches:
            item.start()
        self.client = gui.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def make_project(self, *, archived=False) -> Path:
        path = projects.create("The Only One")
        if archived:
            data = projects.load(path)
            data.update(status="archived", archive_path=str(Path(self.temp.name, "archive", path.name)))
            atomic_write_json(path / projects.PROJECT_FILE, data)
            (path / "ARCHIVED.txt").write_text("archived", encoding="utf-8")
        return path

    def test_startup_with_an_archived_last_project_loads_nothing(self):
        path = self.make_project(archived=True)
        atomic_write_json(self.state_file, {"active": path.name})
        self.gui.startup()
        current = self.client.get("/api/projects/current")
        data = current.get_json()
        self.assertTrue(data["draft"])
        self.assertIsNone(data["slug"])
        self.assertEqual(data["title"], "Novo projeto (não criado)")
        self.assertEqual(current.headers["X-Project-Slug"], "")
        self.assertNotEqual(Path(config.OUTPUT_DIR).resolve(), path.resolve())
        self.assertEqual(self.client.post("/api/run", json={"steps": ["research"]}).status_code, 409)

    def test_startup_reopens_a_live_project(self):
        path = self.make_project()
        atomic_write_json(self.state_file, {"active": path.name})
        self.gui.startup()
        self.assertEqual(self.client.get("/api/projects/current").get_json()["slug"], path.name)

    def test_new_project_is_what_every_panel_reads(self):
        old = self.make_project()
        self.client.post("/api/projects/open", json={"slug": old.name})
        self.assertTrue(self.client.post("/api/projects/draft").get_json()["draft"])
        created = self.client.post("/api/projects/new", json={"premise": "A man rehearses his own eulogy.",
                                                              "target_duration": 15}).get_json()
        self.assertTrue(created["ok"])
        slug = created["slug"]
        self.assertNotEqual(slug, old.name)
        self.assertEqual(Path(config.OUTPUT_DIR).name, slug)
        self.assertEqual(load_json(self.state_file)["active"], slug)
        for url in ("/api/projects/current", "/api/review-gate", "/api/shorts"):
            response = self.client.get(url)
            self.assertEqual(response.headers["X-Project-Slug"], slug, url)
        self.assertEqual(self.client.get("/api/projects/current").get_json()["slug"], slug)
        self.assertFalse(self.client.get("/api/projects/current").get_json().get("draft"))

    def test_opening_an_archived_project_is_refused_with_a_restore_hint(self):
        path = self.make_project(archived=True)
        response = self.client.post("/api/projects/open", json={"slug": path.name})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.get_json()["archived"])
        self.assertIn("Restaurar", response.get_json()["error"])
        with patch("studio.archive.restore_project") as restore:
            started = self.client.post("/api/projects/restore", json={"slug": path.name}).get_json()
            self.assertTrue(started["ok"])
            for _ in range(100):                          # the job runs in a thread
                if restore.called:
                    break
                __import__("time").sleep(0.01)
        self.assertEqual(restore.call_args.args[0], path.name)
        live = self.make_project()
        self.assertEqual(self.client.post("/api/projects/restore", json={"slug": live.name}).status_code, 400)

    def test_template_flow(self):
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        for needed in ("function refreshAll()", "projectFetch(", "X-Project-Slug", "/api/projects/draft",
                       "restoreProject(", "Novo projeto (não criado)", "project-empty"):
            self.assertIn(needed, page)


if __name__ == "__main__":
    unittest.main()
