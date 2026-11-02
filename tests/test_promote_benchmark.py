"""Phase 2.2.1 section 5: promote a benchmark to output/ with human edits."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents.narrative import atomic_write_json, deterministic_review, merge_editorial_review
from scripts import promote_benchmark as promote
from tests.test_narrative import bible, screenplay_with_counts, story_brief
from tests.test_review_reliability import DURATION_FALSE_POSITIVE, assessment, finding

LECTURE_LINE = "Rene Girard wrote that desire is mimetic and borrowed."


def make_benchmark(root: Path) -> Path:
    source = root / "benchmarks" / "private-gravity-test"
    script = screenplay_with_counts([80, 80, 80], [80, 80, 80])
    script["title"] = "He Waited Twenty Years"
    script["target_duration_minutes"] = 240 / 125
    for scene in script["sections"]:
        scene["narration"] = " ".join(f"s{scene['id']}w{n}" for n in range(72))
    script["sections"][0]["narration"] += " He set the table for one guest and checked the clock."
    script["sections"][1]["narration"] += " " + LECTURE_LINE
    lecture = finding("Scene 2 lectures about mimetic desire.", issue_type="lecture")
    lecture["scene_ids"] = [2]
    other = finding("Scene 3 repeats the linen motif.", issue_type="repetition_control",
                    severity="minor")
    other["scene_ids"] = [3]
    review = merge_editorial_review(deterministic_review(script, 240 / 125),
                                    assessment([lecture, other, DURATION_FALSE_POSITIVE]))
    review["source_hash"] = "benchmark-hash"
    for name, value in (("story_brief.json", story_brief()), ("character_bible.json", bible()),
                        ("story_outline.json", {"scenes": []}), ("script.json", script),
                        ("narrative_review.json", review)):
        atomic_write_json(source / name, value)
    return source


def make_v1_output(output: Path) -> None:
    atomic_write_json(output / "script.json", {"title": "The Architecture of Envy"})
    atomic_write_json(output / "research.json", {"video_title": "The Architecture of Envy"})
    (output / "images").mkdir(parents=True, exist_ok=True)
    (output / "images" / "section_01_img1.jpg").write_bytes(b"v1-image")
    (output / "narration.mp3").write_bytes(b"v1-audio")
    atomic_write_json(output / "review_approval.json", {"approved_at": "x"})


EDITS = [
    {"scene_id": "s02", "find": LECTURE_LINE,
     "replace": "He understood he had only ever wanted what another man wanted."},
    {"scene_id": "s01", "find": "for one guest", "replace": "for eight guests"},
]


class PromoteBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name, "output")
        self.source = make_benchmark(self.output)
        make_v1_output(self.output)
        self.edits = Path(self.temp.name, "edits.json")
        self.edits.write_text(json.dumps(EDITS), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = promote.main(["--source", str(self.source), "--output-dir", str(self.output),
                                 *extra])
        return code, out.getvalue() + err.getvalue()

    def archives(self):
        return sorted((self.output / "benchmarks").glob("archive-*"))

    def test_edits_applied_recorded_and_downstream_invalidated(self):
        code, text = self.run_main("--edits", str(self.edits))
        self.assertEqual(code, 0, text)
        script = json.loads((self.output / "script.json").read_text(encoding="utf-8"))
        self.assertIn("eight guests", script["sections"][0]["narration"])
        self.assertNotIn("Girard", script["sections"][1]["narration"])
        self.assertEqual([edit["author"] for edit in script["editorial_edits"]],
                         ["human_editorial"] * 2)
        review = json.loads((self.output / "narrative_review.json").read_text(encoding="utf-8"))
        self.assertEqual(len(review["editorial_edits"]), 2)
        self.assertEqual([item["issue_type"] for item in review["addressed_editorial_findings"]],
                         ["lecture"])
        self.assertTrue(review["addressed_editorial_findings"][0]["addressed_by_human_edit"])
        self.assertEqual([item["reason"] for item in review["discarded_editorial_findings"]],
                         ["deterministic_owner"])
        self.assertEqual(review["status"], "ready_for_human_review")
        lecture = [item for item in review["editorial_assessment"]["findings"]
                   if item["issue_type"] == "lecture"]
        self.assertEqual(len(lecture), 1)          # marked, not removed
        brief = json.loads((self.output / "story_brief.json").read_text(encoding="utf-8"))
        research = json.loads((self.output / "research.json").read_text(encoding="utf-8"))
        self.assertEqual(brief, research)
        for name in ("narration.mp3", "review_approval.json", "images/section_01_img1.jpg"):
            self.assertFalse((self.output / name).exists(), name)

    def test_archive_created_once_and_v1_recoverable(self):
        self.assertEqual(self.run_main()[0], 0)
        archives = self.archives()
        self.assertEqual(len(archives), 1)
        archived = json.loads((archives[0] / "script.json").read_text(encoding="utf-8"))
        self.assertEqual(archived["title"], "The Architecture of Envy")
        self.assertEqual((archives[0] / "images" / "section_01_img1.jpg").read_bytes(), b"v1-image")
        self.assertEqual((archives[0] / "narration.mp3").read_bytes(), b"v1-audio")
        self.assertFalse((archives[0] / "benchmarks").exists())
        # Promoting again archives the new content once, then never duplicates it.
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len(self.archives()), 2)

    def test_ambiguous_or_missing_find_aborts_atomically(self):
        before = {path: path.read_bytes() for path in self.output.rglob("*") if path.is_file()}
        for edits in (
            [{"scene_id": "s01", "find": "s1w1", "replace": "x"}],          # s1w1, s1w10...
            [{"scene_id": "s01", "find": "not present", "replace": "x"}],
            [EDITS[0], {"scene_id": "s09", "find": "a", "replace": "b"}],
        ):
            with self.subTest(edits=edits):
                self.edits.write_text(json.dumps(edits), encoding="utf-8")
                code, text = self.run_main("--edits", str(self.edits))
                self.assertEqual(code, 1)
                self.assertIn("no change made", text)
        after = {path: path.read_bytes() for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.archives(), [])

    def test_failed_archive_refuses_promotion(self):
        with patch.object(promote.shutil, "copy2", side_effect=OSError("disk full")):
            code, text = self.run_main("--edits", str(self.edits))
        self.assertEqual(code, 1)
        self.assertIn("could not archive", text)
        script = json.loads((self.output / "script.json").read_text(encoding="utf-8"))
        self.assertEqual(script["title"], "The Architecture of Envy")

    def test_rerun_editorial_requires_confirm_live_and_is_mocked(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            promote.main(["--source", str(self.source), "--rerun-editorial"])
        with patch("agents.scriptwriter._editorial_review", return_value=assessment([])) as call:
            code, text = self.run_main("--edits", str(self.edits), "--rerun-editorial",
                                       "--confirm-live")
        self.assertEqual(code, 0, text)
        call.assert_called_once()
        review = json.loads((self.output / "narrative_review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["status"], "ready_for_human_review")
        self.assertNotIn("addressed_editorial_findings", review)

    def test_real_edits_file_matches_the_benchmark_exactly_once(self):
        edits_file = Path("docs/prompts/edits/envy-2026-10-21.json")
        if not edits_file.exists():
            self.skipTest("edits file not present in this checkout")
        edits = json.loads(edits_file.read_text(encoding="utf-8"))
        self.assertEqual([edit["scene_id"] for edit in edits], ["s01", "s08", "s07"])
        benchmark = Path("output/benchmarks/private-gravity-20261021T001448Z/script.json")
        if not benchmark.exists():
            self.skipTest("benchmark output not present in this checkout")
        script = json.loads(benchmark.read_text(encoding="utf-8"))
        _, applied = promote.apply_edits(script, edits)
        self.assertEqual(len(applied), 3)


if __name__ == "__main__":
    unittest.main()
