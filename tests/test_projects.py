"""Phase 3.3-D: projects — status, migration, --project, GUI endpoints, legacy output/."""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json, stable_hash
from studio import projects
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

BRIEF = {"video_title": "The Man Who Needed Everyone to Envy Him", "psychological_theme": "envy"}


def write_story(folder: Path, brief=BRIEF) -> None:
    bible = {"characters": [{"character_id": "julian_vance"}], "source_hash": stable_hash(brief)}
    outline = {"scenes": [{"scene_id": "s01"}], "source_hash": "o"}
    script = {"title": brief["video_title"], "video_type": "normal", "target_duration_minutes": 12,
              "sections": [{"id": 1, "narration": "x"}],
              "source_hash": stable_hash({"brief": brief, "bible": bible, "outline": outline})}
    for name, value in (("story_brief.json", brief), ("research.json", brief), ("character_bible.json", bible),
                        ("story_outline.json", outline), ("script.json", script),
                        ("narrative_review.json", {"source_hash": stable_hash(script)})):
        atomic_write_json(folder / name, value)


def statuses(folder) -> dict:
    return {item["stage"]: item["status"] for item in projects.stage_status(folder)}


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(config, "PROJECTS_DIR", str(self.base / "projects")),
                        patch.object(config, "OUTPUT_DIR", config.OUTPUT_DIR),
                        patch.object(config, "IMAGES_DIR", config.IMAGES_DIR),
                        patch.object(config, "PUBLICATIONS_FILE", str(self.base / "publications.json"))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()


class StatusTests(ProjectTestCase):
    def test_missing_done_stale_partial_and_skipped(self):
        folder = projects.create("The Man Who Needed Everyone to Envy Him", date="2026-10-23")
        self.assertEqual(folder.name, "2026-10-23-the-man-who-needed-everyone-to-envy-him")
        self.assertEqual(statuses(folder)["story_brief"], "missing")
        self.assertEqual(projects.next_step(projects.stage_status(folder))["stage"], "story_brief")

        write_story(folder)
        self.assertEqual(statuses(folder)["script"], "done")
        atomic_write_json(folder / "story_brief.json", dict(BRIEF, psychological_theme="pride"))
        self.assertEqual(statuses(folder)["script"], "stale")
        write_story(folder)

        for name in ("narration.mp3", "narration.srt"):
            (folder / name).write_bytes(b"x")
        self.assertEqual(statuses(folder)["narration"], "partial")
        atomic_write_json(folder / "scene_timeline.json", {"total_duration": 600})
        self.assertEqual(statuses(folder)["narration"], "done")
        old = time.time() - 100
        os.utime(folder / "scene_timeline.json", (old, old))
        self.assertEqual(statuses(folder)["narration"], "stale")      # the script is newer
        os.utime(folder / "scene_timeline.json", None)

        atomic_write_json(folder / "visual_bible.json", {"characters": [], "locations": [], "source_hash": "v"})
        shots = {"source_hash": "h", "shots": [
            {"shot_id": "s01_01", "image_path": "images/shots/s01_01.jpg", "characters": []},
            {"shot_id": "s01_02", "characters": []}, {"shot_id": "s01_03", "reuse_of": "s01_01"}]}
        atomic_write_json(folder / "shot_list.json", shots)
        (folder / "images" / "shots").mkdir(parents=True)
        (folder / "images" / "shots" / "s01_01.jpg").write_bytes(b"jpg")
        with patch("agents.visual_bible.source_hash_for", return_value="v"), \
                patch("agents.shot_list.expected_source_hash", return_value="h"):
            result = {item["stage"]: item for item in projects.stage_status(folder)}
            self.assertEqual(result["visual_bible"]["status"], "done")
            self.assertEqual((result["shots"]["status"], result["shots"]["detail"]), ("partial", "1/2 images"))
        with patch("agents.visual_bible.source_hash_for", return_value="v"), \
                patch("agents.shot_list.expected_source_hash", return_value="new rules"):
            self.assertEqual(statuses(folder)["shots"], "stale")

        data = projects.load(folder)
        data["skipped"] = ["music"]
        projects.save(folder, data)
        self.assertEqual(statuses(folder)["music"], "skipped")
        upstream = projects.blocking_upstream(projects.stage_status(folder), "video")
        self.assertIn("shots", [item["stage"] for item in upstream])

    def test_provisional_project_is_renamed_after_the_brief(self):
        folder = projects.create(video_type="normal", target_duration=12)
        projects.set_active(folder.name)
        self.assertTrue(projects.load(folder)["provisional"])
        renamed = projects.retitle(folder, "She Said Yes to Everyone for 20 Years")
        self.assertTrue(renamed.name.endswith("-she-said-yes-to-everyone-for-20-years"))
        self.assertFalse(folder.exists())
        self.assertEqual(projects.active_slug(), renamed.name)
        self.assertEqual(projects.retitle(renamed, "Another title"), renamed)      # only once

    def test_resolution_order_and_legacy_output(self):
        self.assertEqual(projects.resolve(), Path("output"))                          # no projects yet
        first = projects.create("One", date="2026-10-01")
        time.sleep(1.1)
        second = projects.create("Two", date="2026-10-02")
        self.assertEqual(projects.resolve(), second)                                  # most recent
        projects.set_active(first.name)
        self.assertEqual(projects.resolve(), first)                                   # active
        self.assertEqual(projects.resolve(second.name), second)                       # explicit
        with self.assertRaises(projects.ProjectNotFound):
            projects.resolve("../etc")
        args = type("Args", (), {"project": None, "output_dir": "output"})()
        before = config.OUTPUT_DIR
        self.assertEqual(projects.cli_output(args), Path("output"))                   # explicit folder
        self.assertEqual(config.OUTPUT_DIR, before)                                   # not activated


class MigrationTests(ProjectTestCase):
    def legacy(self) -> Path:
        legacy = self.base / "output"
        legacy.mkdir()
        write_story(legacy)
        (legacy / "images" / "shots").mkdir(parents=True)
        (legacy / "images" / "shots" / "s01_01.jpg").write_bytes(b"jpg")
        (legacy / "benchmarks" / "b1").mkdir(parents=True)
        (legacy / "style_calibration").mkdir()
        (legacy / "archive-old").mkdir()
        atomic_write_json(legacy / "asset_manifest.json", {"generated_assets": [
            {"job_id": "s01_01", "local_path": f"{legacy.as_posix()}/images/shots/s01_01.jpg"}]})
        atomic_write_json(Path(config.PUBLICATIONS_FILE), {"schema_version": 1, "publications": [
            {"video_id": "abc123XYZ", "run_dir": legacy.as_posix()}]})
        return legacy

    def test_dry_run_then_move(self):
        from scripts import migrate_output_to_project as migrate
        legacy = self.legacy()
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(migrate.main(["--source", str(legacy), "--dry-run"]), 0)
        self.assertIn("Dry run", out.getvalue())
        self.assertTrue((legacy / "script.json").exists())
        self.assertFalse(projects.root().exists() and any(projects.root().iterdir()))

        with redirect_stdout(io.StringIO()):
            self.assertEqual(migrate.main(["--source", str(legacy)]), 0)
        slug = projects.active_slug()
        folder = projects.root() / slug
        self.assertTrue(slug.endswith("-the-man-who-needed-everyone-to-envy-him"))
        for kept in ("benchmarks", "style_calibration", "archive-old", "MIGRATED.txt"):
            self.assertTrue((legacy / kept).exists(), kept)
        self.assertFalse((legacy / "script.json").exists())
        self.assertTrue((folder / "images" / "shots" / "s01_01.jpg").is_file())
        self.assertIn(slug, (legacy / "MIGRATED.txt").read_text(encoding="utf-8"))
        manifest = load_json(folder / "asset_manifest.json")
        self.assertEqual(manifest["generated_assets"][0]["local_path"],
                         f"{folder.as_posix()}/images/shots/s01_01.jpg")
        info = projects.load(folder)
        self.assertEqual((info["title"], info["published_video_id"]),
                         ("The Man Who Needed Everyone to Envy Him", "abc123XYZ"))
        self.assertEqual(info["stages"]["script"], "done")
        ledger = load_json(Path(config.PUBLICATIONS_FILE))
        self.assertEqual(ledger["publications"][0]["project"], slug)
        with redirect_stdout(io.StringIO()):                      # running again is harmless
            self.assertEqual(migrate.main(["--source", str(legacy)]), 0)


class ScriptTests(ProjectTestCase):
    def test_scripts_honour_project(self):
        from scripts import generate_shots, project as project_cli
        folder = projects.create("Two", date="2026-10-02")
        seen = {}

        def fake_list(output, force=False):
            seen["output"] = Path(output)
            seen["config"] = config.OUTPUT_DIR
            raise SystemExit(0)
        with patch("video.shot_generation.ensure_shot_list", side_effect=fake_list), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            generate_shots.main(["--project", folder.name, "--limit", "5"])
        self.assertEqual(seen["output"], folder)
        self.assertEqual(seen["config"], str(folder))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(project_cli.main(["list"]), 0)
            self.assertEqual(project_cli.main(["open", folder.name]), 0)
        self.assertIn(folder.name, out.getvalue())
        self.assertEqual(projects.active_slug(), folder.name)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(project_cli.main(["show", "nope"]), 1)

    def test_upload_record_carries_the_project(self):
        from uploader import publications
        folder = projects.create("Two", date="2026-10-02")
        write_story(folder)
        record = publications.record_upload("vid123456", folder)
        self.assertEqual(record["project"], folder.name)
        self.assertEqual(projects.load(folder)["published_video_id"], "vid123456")


class GuiProjectTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        import gui
        self.gui = gui
        self.state = patch.dict(gui._state, {"running": False, "imagegen_busy": False})
        self.state.start()
        self.client = gui.app.test_client()

    def tearDown(self):
        self.state.stop()
        super().tearDown()

    def test_list_open_new_skip_and_thumbnail(self):
        folder = projects.create("Two", date="2026-10-02")
        write_story(folder)
        (folder / "thumbnail.jpg").write_bytes(b"\xff\xd8jpg")
        listed = self.client.get("/api/projects").get_json()
        self.assertEqual([item["slug"] for item in listed["projects"]], [folder.name])
        self.assertEqual(listed["projects"][0]["thumbnail"], "thumbnail.jpg")
        opened = self.client.post("/api/projects/open", json={"slug": folder.name}).get_json()
        self.assertTrue(opened["ok"])
        self.assertEqual(config.OUTPUT_DIR, str(folder))
        self.assertEqual(self.gui._state["script"]["title"], BRIEF["video_title"])
        self.assertEqual(self.gui._state["stages"]["script"], "done")
        thumb = self.client.get(f"/projects/{folder.name}/thumbnail")
        self.assertEqual(thumb.status_code, 200)
        thumb.close()
        self.assertEqual(self.client.post("/api/projects/open", json={"slug": "nope"}).status_code, 404)
        skipped = self.client.post("/api/projects/stage/music", json={"action": "skip"}).get_json()
        self.assertEqual(next(s for s in skipped["stages"] if s["stage"] == "music")["status"], "skipped")
        created = self.client.post("/api/projects/new", json={"video_type": "normal", "target_duration": 12}).get_json()
        self.assertTrue(created["provisional"])
        self.assertEqual(config.OUTPUT_DIR, str(projects.root() / created["slug"]))

    def test_stage_run_warns_on_stale_upstream_and_never_forces_the_brief(self):
        folder = projects.create("Two", date="2026-10-02")
        write_story(folder)
        self.client.post("/api/projects/open", json={"slug": folder.name})
        with patch.object(self.gui, "_run_pipeline") as run:
            blocked = self.client.post("/api/projects/stage/video", json={"action": "run"})
            self.assertEqual(blocked.status_code, 409)
            body = blocked.get_json()
            self.assertTrue(body["needs_confirmation"])
            self.assertIn("narration", [item["stage"] for item in body["upstream"]])
            run.assert_not_called()
            ok = self.client.post("/api/projects/stage/video", json={"action": "run", "confirm": True}).get_json()
            self.assertEqual(ok["steps"], ["video"])
            upstream = self.client.post("/api/projects/stage/video",
                                        json={"action": "run", "rerun_upstream": True}).get_json()
            for _ in range(50):
                if run.call_count >= 2:
                    break
                time.sleep(0.02)
        self.assertNotIn("research", upstream["steps"])
        self.assertEqual(upstream["steps"][-1], "video")
        self.assertIn("narration", upstream["steps"])
        self.assertEqual(run.call_args_list[0].args[0], ["video"])

    def test_legacy_output_is_still_usable(self):
        legacy = self.base / "legacy"
        legacy.mkdir()
        write_story(legacy)
        with patch.object(config, "OUTPUT_DIR", str(legacy)):
            current = self.client.get("/api/projects/current").get_json()
        self.assertFalse(current["project"])
        self.assertEqual(next(s for s in current["stages"] if s["stage"] == "script")["status"], "done")
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Novo projeto", page)
        self.assertIn("Continuar projeto", page)


if __name__ == "__main__":
    unittest.main()
