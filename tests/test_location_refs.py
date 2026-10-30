"""Location references: recurring = 2+ scenes or 3+ shots; any location on request ("Gerar referência")."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import shot_generation as gen

VB = {"characters": [], "locations": [
    {"location_id": "front_room", "descriptor": "a narrow front room", "scene_ids": ["s01", "s04"]},
    {"location_id": "cemetery", "descriptor": "a wet cemetery bench", "scene_ids": ["s08"]},
    {"location_id": "hallway", "descriptor": "a dark hallway", "scene_ids": ["s03"]},
]}
SHOTS = {"shots": [{"shot_id": f"s08_0{n}", "location": "cemetery"} for n in (1, 2, 3)]
         + [{"shot_id": "s03_01", "location": "hallway"}, {"shot_id": "s03_02", "location": "hallway"},
            {"shot_id": "s03_03", "location": "hallway", "reuse_of": "s03_01"}]}


class StubGenerator:
    def __init__(self):
        self.jobs = []

    def generate_with_retry(self, job):
        self.jobs.append(job)
        return {"seed": job.seed}


class LocationReferenceTests(unittest.TestCase):
    def test_recurring_means_two_scenes_or_three_shots(self):
        self.assertEqual([item["location_id"] for item in gen.recurring_locations(VB, SHOTS)],
                         ["front_room", "cemetery"])                 # hallway: 2 shots + a reused image
        self.assertEqual([item["location_id"] for item in gen.recurring_locations(VB)], ["front_room"])
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "shot_list.json"), SHOTS)
            self.assertEqual(len(gen.recurring_locations(VB, output_dir=temp)), 2)

    def test_candidates_for_recurring_locations_include_a_long_single_scene(self):
        stub = StubGenerator()
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "shot_list.json"), SHOTS)
            with redirect_stdout(io.StringIO()):
                record = gen.generate_reference_candidates(VB, temp, stub, characters=[], progress=lambda _: None)
        self.assertEqual(sorted(record), ["location:cemetery", "location:front_room"])
        self.assertEqual(len(stub.jobs), 4)

    def test_any_location_on_request(self):
        stub = StubGenerator()
        with tempfile.TemporaryDirectory() as temp:
            record = gen.generate_location_candidates(VB, temp, stub, ["hallway"], progress=lambda _: None)
            with self.assertRaises(ValueError):
                gen.generate_location_candidates(VB, temp, stub, ["attic"], progress=lambda _: None)
        self.assertEqual([item["number"] for item in record["location:hallway"]], [1, 2])
        self.assertTrue(record["location:hallway"][0]["path"].replace("\\", "/")
                        .startswith("location_refs/candidates/hallway_1"))

    def test_gui_lists_every_location_and_offers_the_button(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch("video.imagegen_comfyui.ComfyClient.available", return_value=False):
            atomic_write_json(Path(temp, "visual_bible.json"), VB)
            atomic_write_json(Path(temp, "shot_list.json"), SHOTS)
            client = gui.app.test_client()
            status = client.get("/api/imagegen/status").get_json()
            unknown = client.post("/api/imagegen/location/candidates", json={"id": "attic"})
            with patch.object(gui, "_imagegen_run", return_value=("started", 200)) as run:
                client.post("/api/imagegen/location/candidates", json={"id": "hallway"})
        optional = {item["id"]: item["optional"] for item in status["references"] if item["kind"] == "location"}
        self.assertEqual(optional, {"front_room": False, "cemetery": False, "hallway": True})
        self.assertEqual(unknown.status_code, 400)
        run.assert_called_once()
        self.assertIn("Gerar referência", Path("gui_templates/index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
