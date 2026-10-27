"""Phase 3.4-G: archive published projects from the SSD to the HDD (temp dirs, no real drives)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from studio import archive, projects
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import publications

FILES = {
    "final_video.mp4": b"video" * 100, "thumbnail.jpg": b"thumb", "images/shots/s01_01.jpg": b"shot",
    "character_refs/julian_vance.png": b"sheet", "location_refs/loft.png": b"loft",
    "narration.mp3": b"narration", "narration_mixed.mp3": b"mix", "narration.srt": b"1\n00:00 --> 00:01\nHi\n",
    "review_approval.json": b"{}", "scene_timeline.json": b"{}", "debug/shot-list-1.txt": b"raw",
    "notes/odd.bin": b"unknown", ".imagegen_cache/abc.png": b"cache", ".checkpoints/shot_list/s01.json": b"{}",
}
EXPECTED = {
    "video/final_video.mp4", "video/thumbnail.jpg", "images/shots/s01_01.jpg", "images/character_refs/julian_vance.png",
    "images/location_refs/loft.png", "audio/narration.mp3", "audio/narration_mixed.mp3", "captions/narration.srt",
    "text/review_approval.json", "text/script.json", "text/project.json", "manifests/scene_timeline.json",
    "manifests/asset_manifest.json", "logs/debug/shot-list-1.txt", "other/notes/odd.bin",
    "audio/music/Night Room - Ambient.mp3", "text/youtube_metadata.json", "archive_manifest.json",
}


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.archive_root = base / "D" / "Private Gravity" / "Archive"
        self.music = base / "music library"
        self.music.mkdir()
        (self.music / "Night Room - Ambient.mp3").write_bytes(b"track")
        self.patches = [patch.object(config, "PROJECTS_DIR", str(base / "projects")),
                        patch.object(config, "ARCHIVE_ROOT", str(self.archive_root)),
                        patch.object(config, "ARCHIVE_QUEUE_FILE", str(base / "archive_queue.json")),
                        patch.object(config, "PUBLICATIONS_FILE", str(base / "publications.json")),
                        patch.object(config, "MUSIC_LIBRARY_DIR", str(self.music)),
                        patch.object(config, "ARCHIVE_ON_PUBLISH", True)]
        for item in self.patches:
            item.start()
        self.project = projects.create("The Man Who Needed Everyone to Envy Him", date="2026-10-21")
        for relative, data in FILES.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        atomic_write_json(self.project / "script.json", {"title": "The Man", "description": "d", "tags": ["t"]})
        atomic_write_json(self.project / "asset_manifest.json", {"music": {"track": "Night Room - Ambient.mp3"}})
        self.ledger(publications.PUBLISHED)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def ledger(self, state):
        atomic_write_json(Path(config.PUBLICATIONS_FILE), {"schema_version": 1, "publications": [
            {"video_id": "abc123XYZ", "url": "https://www.youtube.com/watch?v=abc123XYZ", "project": self.project.name,
             "run_dir": str(self.project), "state": state, "published_at": "2026-10-27T10:00:00+00:00"}]})

    def archived(self, target=None):
        target = Path(target or self.archive_root / self.project.name)
        return {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}

    def run_archive(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return archive.archive_project(self.project, **kwargs)


class ArchiveTests(ArchiveTestCase):
    def test_dry_run_lists_actions_and_changes_nothing(self):
        work = self.run_archive(dry_run=True)
        kinds = {item["action"] for item in work["actions"]}
        self.assertEqual(kinds, {"move", "copy", "delete-cache"})
        self.assertEqual(len([item for item in work["actions"] if item["action"] == "delete-cache"]), 2)
        self.assertFalse(self.archive_root.exists())
        self.assertTrue((self.project / "final_video.mp4").is_file())

    def test_full_run_moves_copies_and_deletes_by_category(self):
        manifest = self.run_archive()
        target = self.archive_root / self.project.name
        self.assertEqual(self.archived(), EXPECTED)
        self.assertEqual((target / "video/final_video.mp4").read_bytes(), FILES["final_video.mp4"])
        self.assertTrue((self.music / "Night Room - Ambient.mp3").is_file())            # copied, not moved
        left = {path.relative_to(self.project).as_posix() for path in self.project.rglob("*") if path.is_file()}
        self.assertEqual(left, {"project.json", "ARCHIVED.txt"})                          # caches deleted too
        stub = projects.load(self.project)
        self.assertEqual((stub["status"], stub["archive_path"]), ("archived", str(target)))
        entry = next(item for item in manifest["files"] if item["archive"] == "video/final_video.mp4")
        self.assertEqual((entry["action"], entry["size"]), ("move", len(FILES["final_video.mp4"])))
        self.assertEqual(len(entry["sha256"]), 64)
        self.assertEqual({item["action"] for item in manifest["caches"]}, {"deleted-cache"})
        metadata = load_json(target / "text/youtube_metadata.json")
        self.assertEqual((metadata["video_id"], metadata["title"], metadata["tags"]), ("abc123XYZ", "The Man", ["t"]))
        self.assertTrue(projects.summary(self.project)["archived"])

    def test_hash_mismatch_aborts_without_deleting(self):
        real_copy = archive.shutil.copy2

        def corrupt(source, destination):
            real_copy(source, destination)
            if source.endswith("final_video.mp4"):
                Path(destination).write_bytes(b"corrupted")
        with patch.object(archive.shutil, "copy2", side_effect=corrupt), self.assertRaises(archive.ArchiveError):
            self.run_archive()
        for relative in FILES:
            self.assertTrue((self.project / relative).is_file(), relative)

    def test_an_interrupted_run_resumes(self):
        real_replace, calls = os.replace, []

        def crash(source, destination):
            calls.append(destination)
            if len(calls) == 4:
                raise OSError("power cut")
            return real_replace(source, destination)
        with patch.object(archive.os, "replace", side_effect=crash), self.assertRaises(OSError):
            self.run_archive()
        self.assertTrue((self.project / "final_video.mp4").is_file())                  # nothing deleted yet
        self.run_archive()
        self.assertEqual(self.archived(), EXPECTED)
        self.assertFalse(list(self.archive_root.rglob("*.part")))

    def test_space_check_and_ledger_gate(self):
        with patch.object(archive, "_free_bytes", return_value=10), self.assertRaises(archive.ArchiveError) as caught:
            self.run_archive()
        self.assertIn("not enough space", str(caught.exception))
        self.ledger(publications.PENDING)
        with self.assertRaises(archive.ArchiveError) as caught:
            self.run_archive()
        self.assertIn("not published", str(caught.exception))
        with self.assertRaises(archive.ArchiveError):
            self.run_archive(force=True)                                               # no reason
        manifest = self.run_archive(force=True, reason="test upload removed by mistake")
        self.assertEqual((manifest["forced"], manifest["reason"]), (True, "test upload removed by mistake"))

    def test_a_foreign_archive_folder_is_never_overwritten(self):
        other = self.archive_root / self.project.name
        other.mkdir(parents=True)
        (other / "something.txt").write_text("another project", encoding="utf-8")
        manifest = self.run_archive()
        self.assertTrue(manifest["target"].endswith(f"{self.project.name}__2"))
        self.assertEqual((other / "something.txt").read_text(encoding="utf-8"), "another project")

    def test_restore_round_trip_keeps_the_archive(self):
        self.run_archive()
        with redirect_stdout(io.StringIO()):
            archive.restore_project(self.project.name)
        for relative, data in FILES.items():
            if not relative.startswith("."):
                self.assertEqual((self.project / relative).read_bytes(), data, relative)
        self.assertEqual(projects.load(self.project)["status"], "restored")
        self.assertFalse((self.project / "ARCHIVED.txt").exists())
        self.assertEqual(self.archived(), EXPECTED)


class AutomaticArchiveTests(ArchiveTestCase):
    def service(self):
        item = {"id": "abc123XYZ", "status": {"privacyStatus": "public", "uploadStatus": "processed"}}
        listing = SimpleNamespace(execute=lambda: {"items": [item]})
        return SimpleNamespace(videos=lambda: SimpleNamespace(list=lambda **kwargs: listing))

    def test_sync_archives_a_newly_published_project(self):
        self.ledger(publications.PENDING)
        with patch("agents.researcher.append_banned_entry"), redirect_stdout(io.StringIO()):
            summary = publications.sync_publications(service=self.service())
        self.assertEqual(summary["archive"]["archived"], [self.project.name])
        self.assertEqual(projects.load(self.project)["status"], "archived")

    def test_unreachable_target_is_queued_and_retried(self):
        self.ledger(publications.PENDING)
        unreachable = patch.object(archive, "_free_bytes", side_effect=archive.ArchiveError("drive D: not reachable"))
        with unreachable, patch("agents.researcher.append_banned_entry"), redirect_stdout(io.StringIO()):
            summary = publications.sync_publications(service=self.service())
        self.assertEqual(summary["archive"]["queued"], [self.project.name])
        self.assertEqual([item["slug"] for item in load_json(Path(config.ARCHIVE_QUEUE_FILE))["queue"]],
                         [self.project.name])
        self.assertTrue((self.project / "final_video.mp4").is_file())
        with redirect_stdout(io.StringIO()):                    # next sync: nothing pending, the queue is retried
            publications.sync_publications(service=self.service())
        self.assertEqual(projects.load(self.project)["status"], "archived")
        self.assertEqual(load_json(Path(config.ARCHIVE_QUEUE_FILE))["queue"], [])


class InterfaceTests(ArchiveTestCase):
    def test_cli_dry_run_and_gui_endpoints(self):
        from scripts import archive_project
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(archive_project.main(["--project", self.project.name, "--dry-run"]), 0)
        self.assertIn("Nothing was changed", out.getvalue())
        with redirect_stderr(io.StringIO()):
            self.assertEqual(archive_project.main(["--project", "nope"]), 1)
        import gui
        with patch.dict(gui._state, {"running": False, "imagegen_busy": False, "archiving": False}):
            client = gui.app.test_client()
            plan = client.post("/api/projects/archive", json={"slug": self.project.name, "dry_run": True}).get_json()
            self.assertTrue(plan["ok"])
            self.assertTrue(plan["target"].endswith(self.project.name))
            listed = client.get("/api/projects").get_json()["projects"][0]
        self.assertFalse(listed["archived"])
        page = client.get("/").get_data(as_text=True)
        self.assertIn("Arquivar", page)
        self.assertIn("arquivado", page)


if __name__ == "__main__":
    unittest.main()
