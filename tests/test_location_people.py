"""Location references with nobody in them: figure-free style, positive prompts, empty anchors only,
people detected and regenerated, new candidate batches, batch shot regeneration (ComfyUI mocked)."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents import identity, style_refs
from agents import shot_list as shots_module
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import shot_generation as gen

FIGURE_WORDS = re.compile(r"\b(?:figures?|people|persons?|faces?|presence|silhouettes?|empty)\b", re.I)
VB = {"characters": [{"character_id": "walter_hale", "descriptor": "a 68-year-old man", "presence": "on_screen"}],
      "locations": [
          {"location_id": "cemetery", "descriptor": "a wet stone bench by a grave under bare trees",
           "scene_ids": ["s08"]},
          {"location_id": "hallway", "descriptor": "a narrow terraced-house hallway with a coat rack",
           "scene_ids": ["s03"]}]}


class StubGenerator:
    def __init__(self):
        self.jobs = []

    def generate_with_retry(self, job):
        self.jobs.append(job)
        return {"seed": job.seed}


class FakeDetector:
    def __init__(self, people_by_name):
        self.people_by_name = people_by_name
        self.calls = 0

    def detect(self, path):
        self.calls += 1
        return {"persons": [{"box": [0, 0, 1, 1]}] * self.people_by_name.get(Path(path).name, 0)}


class FigureFreeStyleTests(unittest.TestCase):
    def test_suffix_without_figures_keeps_the_style_hash(self):
        free = identity.style_suffix(figures=False)
        self.assertEqual(FIGURE_WORDS.findall(free), [])
        self.assertIn("figures small within towering architecture", identity.style_suffix())
        self.assertIn("scratchboard and engraved line work", free)
        hashed = identity.style_hash()
        with patch.object(identity, "FIGURE_FREE_ATMOSPHERE", "changed"):
            self.assertEqual(identity.style_hash(), hashed)        # the hash uses the full suffix only

    def test_cemetery_and_hallway_prompts_never_mention_people(self):
        for lid in ("cemetery", "hallway"):
            with self.subTest(location=lid):
                prompt = gen.location_prompt(VB, lid)
                self.assertEqual(FIGURE_WORDS.findall(prompt), [])
                self.assertIn("a deserted, unoccupied", prompt)
                self.assertIn("perfectly still", prompt)
        self.assertEqual(FIGURE_WORDS.findall(style_refs.board_prompt(1)), [])

    def test_only_shots_with_people_get_figures(self):
        base = {"location": "cemetery", "action": "a wet bench under bare trees", "others_present": False}
        insert = shots_module.compose_shot(dict(base, shot_type="insert", characters=[]), VB, ".")
        wide = shots_module.compose_shot(dict(base, shot_type="wide", characters=["walter_hale"]), VB, ".",
                                         assume_references=True)
        self.assertNotIn("figures small", insert["prompt"])
        self.assertIn("figures small", wide["prompt"])


class EmptyAnchorTests(unittest.TestCase):
    def test_only_anchors_with_nobody_are_used_for_locations(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "STYLE_REFS_DIR", temp):
            approved = Path(temp, "approved", identity.style_hash())
            approved.mkdir(parents=True)
            for number in (1, 2):
                Image.new("RGB", (8, 8)).save(approved / f"board_{number}.png")
            Path(temp, style_refs.APPROVED_FILE).write_text(json.dumps({identity.style_hash(): [
                {"number": n, "path": f"approved/{identity.style_hash()}/board_{n}.png"} for n in (1, 2)]}),
                encoding="utf-8")
            detector = FakeDetector({"board_1.png": 2})
            with patch("video.qa.fixes._detector", return_value=detector):
                empty = style_refs.empty_paths()
                again = style_refs.empty_paths()
                job = gen._location_job(VB, "cemetery", 1, Path(temp))
            saved = json.loads(Path(temp, style_refs.APPROVED_FILE).read_text(encoding="utf-8"))
        self.assertEqual([Path(path).name for path in empty], ["board_2.png"])
        self.assertEqual(again, empty)
        self.assertEqual(detector.calls, 2)                                    # cached in approved.json
        self.assertEqual([entry["people"] for entry in saved[identity.style_hash()]], [2, 0])
        # 3.9.3 hotfix: a lone style board as the only reference is copied by Klein, so locations are t2i.
        self.assertEqual(list(job.references or []), [])
        self.assertNotIn("drawing style of the first image", job.prompt)

    def test_without_a_detector_no_anchor_is_trusted(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "STYLE_REFS_DIR", temp), \
                patch("video.qa.fixes._detector", return_value=None):
            Path(temp, style_refs.APPROVED_FILE).write_text(json.dumps({identity.style_hash(): [
                {"number": 1, "path": "board_1.png"}]}), encoding="utf-8")
            Image.new("RGB", (8, 8)).save(Path(temp, "board_1.png"))
            self.assertEqual(style_refs.empty_paths(), [])


class PeopleRejectionTests(unittest.TestCase):
    def test_cemetery_is_retried_until_empty_and_hallway_is_flagged(self):
        answers = {"cemetery_1.jpg": [2, 1, 0], "cemetery_2.jpg": [0], "hallway_1.jpg": [1, 1, 1, 1],
                   "hallway_2.jpg": [0]}
        stub = StubGenerator()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(gen, "people_in", side_effect=lambda path: answers[Path(path).name].pop(0)):
            record = gen.generate_location_candidates(VB, temp, stub, ["cemetery", "hallway"],
                                                      progress=lambda _: None)
        cemetery = {item["number"]: item for item in record["location:cemetery"]}
        hallway = {item["number"]: item for item in record["location:hallway"]}
        self.assertEqual((cemetery[1]["people"], cemetery[1]["attempts"]), (0, 3))
        self.assertEqual((hallway[1]["people"], hallway[1]["attempts"]), (1, 4))          # 3 retries, then shown
        self.assertEqual(len(stub.jobs), 3 + 1 + 4 + 1)
        seeds = [job.seed for job in stub.jobs[:3]]
        self.assertEqual(len(set(seeds)), 3)                                           # a new seed each time
        self.assertEqual(cemetery[1]["seed"], seeds[-1])


class NewBatchTests(unittest.TestCase):
    def test_a_new_batch_keeps_the_previous_one(self):
        stub = StubGenerator()
        with tempfile.TemporaryDirectory() as temp, patch.object(gen, "people_in", return_value=0):
            gen.generate_location_candidates(VB, temp, stub, ["cemetery"], progress=lambda _: None)
            record = gen.generate_new_batch(VB, temp, stub, "location", "cemetery", progress=lambda _: None)
            portraits = gen.generate_new_batch(VB, temp, stub, "character", "walter_hale", progress=lambda _: None)
            with self.assertRaises(ValueError):
                gen.generate_new_batch(VB, temp, stub, "character", "ada", progress=lambda _: None)
        items = record["location:cemetery"]
        self.assertEqual([(item["number"], item["batch"]) for item in items], [(1, 1), (2, 1), (3, 2), (4, 2)])
        self.assertEqual(len({item["seed"] for item in items}), 4)
        self.assertEqual([item["number"] for item in portraits["character:walter_hale"]], [1, 2, 3, 4])

    def test_gui_endpoints(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            atomic_write_json(Path(temp, "visual_bible.json"), VB)
            atomic_write_json(Path(temp, "shot_list.json"), {"shots": [
                {"shot_id": "s08_01", "scene_id": "s08", "image_path": "images/a.jpg"},
                {"shot_id": "s08_02", "scene_id": "s08", "image_path": "images/b.jpg"}]})
            client = gui.app.test_client()
            with patch.object(gui, "_imagegen_run", return_value=("started", 200)) as run:
                bad = client.post("/api/imagegen/references/new_batch", json={"kind": "character", "id": "ada"})
                client.post("/api/imagegen/references/new_batch", json={"kind": "location", "id": "hallway"})
                none = client.post("/api/imagegen/shots/regenerate", json={"shot_ids": []})
                unknown = client.post("/api/imagegen/shots/regenerate", json={"shot_ids": ["s99_01"]})
                client.post("/api/imagegen/shots/regenerate", json={"shot_ids": ["s08_01", "s08_02"]})
            shots = load_json(Path(temp, "shot_list.json"))["shots"]
        self.assertEqual((bad.status_code, none.status_code, unknown.status_code), (400, 400, 404))
        self.assertEqual(run.call_count, 2)
        self.assertEqual([(shot.get("seed_offset"), shot.get("image_path")) for shot in shots], [(1, None), (1, None)])
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        for text in ("Gerar novos candidatos", "Gerar de novo (lote)", "pessoa(s) detectada(s)", "Lotes anteriores"):
            self.assertIn(text, page)

    def test_batch_scope(self):
        shot = {"shot_id": "s08_02", "scene_id": "s08"}
        self.assertTrue(gen._in_scope(shot, ["s01_01", "s08_02"]))
        self.assertFalse(gen._in_scope(shot, ["s01_01"]))
        self.assertTrue(gen._in_scope(shot, None))


if __name__ == "__main__":
    unittest.main()
