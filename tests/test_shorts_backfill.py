"""Phase 4.3-H: the Shorts backfill restores only what the Shorts need from a (mocked) archive and links the episode."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from scripts import shorts_backfill
from studio import archive
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_short_formats import BRIEF, OUTLINE, SCRIPT, WPM, script_for, verdicts_ok
from video import short_formats

SLUG = "2026-11-03-everyone-heard-what-he-thought"


def make_archive(root: Path, projects_root: Path):
    target = root / SLUG
    shot_list = {"shots": [{"shot_id": "s01", "section_id": 2, "image_path": "images\\shots\\s01.jpg"},
                           {"shot_id": "s02", "section_id": 3, "image_path": "images\\shots\\s02.jpg"},
                           {"shot_id": "s03", "section_id": 3, "reuse_of": "s01", "image_path": ""}]}
    files = {
        "text/script.json": json.dumps(SCRIPT), "text/story_brief.json": json.dumps(BRIEF),
        "text/story_outline.json": json.dumps(OUTLINE), "text/shot_list.json": json.dumps(shot_list),
        "other/narration_words.json": json.dumps({"words": []}),
        "other/thumbnails/thumbnails.json": json.dumps({"selected": 2, "variants": []}),
        "other/thumbnails/background_2.jpg": "jpg", "other/thumbnails/background_1.jpg": "jpg",
        "images/shots/s01.jpg": "a", "images/shots/s02.jpg": "b", "images/shots/history/s01_old.jpg": "old",
        "images/shots/depth/s01.png": "d", "audio/narration.mp3": "mp3", "video/final_video.mp4": "mp4",
        "shorts/short_01.mp4": "old short", "images/character_refs/nell.jpg": "ref",
    }
    originals = {"text/": "", "other/thumbnails/": "thumbnails/", "other/": "", "images/": "images/", "audio/": "",
                 "video/": "", "shorts/": "shorts/"}
    entries = []
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        prefix = next(key for key in originals if relative.startswith(key))
        original = originals[prefix] + relative[len(prefix):]
        entries.append({"original": f"projects\\{SLUG}\\" + original.replace("/", "\\"), "archive": relative,
                        "sha256": archive.sha256(path), "action": "move"})
    atomic_write_json(target / archive.MANIFEST, {"slug": SLUG, "source": f"projects\\{SLUG}", "files": entries})
    stub = projects_root / SLUG
    stub.mkdir(parents=True)
    atomic_write_json(stub / "project.json", {"slug": SLUG, "status": "archived", "archive_path": str(target)})
    (stub / "ARCHIVED.txt").write_text("archived", encoding="utf-8")


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.projects = base / "projects"
        self.patches = [patch.object(config, "ARCHIVE_ROOT", str(base / "archive"), create=True),
                        patch.object(config, "PROJECTS_DIR", str(self.projects)),
                        patch.object(short_formats, "shorts_wpm", return_value=WPM)]
        for item in self.patches:
            item.start()
        make_archive(base / "archive", self.projects)
        atomic_write_json(config.PUBLICATIONS_FILE, {"publications": [
            {"video_id": "EPISODE1", "project": SLUG, "title": "Everyone Heard What He Thought", "state": "published"},
            {"video_id": "SHORT1", "project": SLUG, "parent_video_id": "EPISODE1", "title": "old short"}]})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_restores_only_the_needed_files(self):
        report = shorts_backfill.restore(SLUG, log=lambda message: None)
        project = self.projects / SLUG
        for name in ("script.json", "story_brief.json", "story_outline.json", "shot_list.json", "narration_words.json",
                     "images/shots/s01.jpg", "images/shots/s02.jpg", "thumbnails/thumbnails.json", "thumbnails/background_2.jpg"):
            self.assertTrue((project / name).is_file(), name)
        for name in ("narration.mp3", "final_video.mp4", "shorts/short_01.mp4", "images/shots/history/s01_old.jpg",
                     "images/shots/depth/s01.png", "images/character_refs/nell.jpg", "thumbnails/background_1.jpg"):
            self.assertFalse((project / name).exists(), name)
        self.assertIn("research.json", report["missing"])          # not in this archive: reported, not invented
        self.assertTrue((project / "ARCHIVED.txt").is_file())       # the project stays archived
        self.assertTrue((Path(config.ARCHIVE_ROOT) / SLUG / "audio" / "narration.mp3").is_file())   # archive intact
        again = shorts_backfill.restore(SLUG, log=lambda message: None)
        self.assertEqual(again["restored"], [])                     # files already on the SSD are kept

    def test_dry_run_copies_nothing(self):
        report = shorts_backfill.run(SLUG, ["hook"], dry_run=True, log=lambda message: None)
        self.assertEqual(report["episode_url"], "https://www.youtube.com/watch?v=EPISODE1")
        self.assertIn("images/shots/s01.jpg", report["restore"]["restored"])
        self.assertFalse((self.projects / SLUG / "script.json").exists())

    def test_plan_links_the_episode_and_waits_for_the_gate(self):
        def generator(prompt, *args, **kwargs):
            return json.dumps({"shorts": [script_for("hook"), script_for("dilemma"), script_for("line")]})
        rendered = []
        report = shorts_backfill.run(SLUG, ["hook", "dilemma", "line"], generator=generator, simulator=verdicts_ok,
                                     render=lambda folder: rendered.append(folder) or [], log=lambda message: None)
        plan = load_json(self.projects / SLUG / "shorts" / "shorts_plan.json")
        self.assertEqual(plan["backfill"], {"episode_video_id": "EPISODE1", "episode_url": "https://www.youtube.com/watch?v=EPISODE1"})
        self.assertEqual([item["format"] for item in plan["segments"]], ["hook", "dilemma", "line"])
        self.assertEqual(rendered, [self.projects / SLUG])
        self.assertEqual(report["episode_url"], "https://www.youtube.com/watch?v=EPISODE1")

    def test_unknown_episode_or_format(self):
        with self.assertRaises(LookupError):
            shorts_backfill.run("2026-01-01-nothing", log=lambda message: None)
        with self.assertRaises(ValueError):
            shorts_backfill.run(SLUG, ["sequel"], generator=lambda *a, **k: "{}", log=lambda message: None)


if __name__ == "__main__":
    unittest.main()
