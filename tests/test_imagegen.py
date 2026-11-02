"""Phase 3.1-C: FLUX 3 Klein template derivation and the ComfyUI provider (mocked)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from tests.fake_comfy import OBJECT_INFO, FakeComfy, png_bytes
from video import imagegen_templates as templates
from video.imagegen_comfyui import (
    ComfyClient,
    ComfyUnavailable,
    GenerationFailed,
    ImageGenerator,
    ImageJob,
)

EDIT = templates.load_edit_template()
REMOVED = {"11", "18", "33", "34", "46", "47", "48", "49", "50", "51", "52"}


def client(fake):
    return ComfyClient("http://127.0.0.1:8188", session=fake, sleep=lambda _: None)


class TemplateDerivationTests(unittest.TestCase):
    def test_t2i_reconnects_empty_latent_and_drops_edit_nodes(self):
        graph = templates.derive_t2i(EDIT)
        self.assertEqual(set(graph), {"1", "2", "3", "4", "7", "8", "9", "32", "44"})
        self.assertFalse(REMOVED & set(graph))
        self.assertNotIn("17", graph)
        self.assertEqual(graph["7"]["inputs"]["latent_image"], ["44", 0])     # disconnected in export
        self.assertEqual(graph["7"]["inputs"]["negative"], ["32", 0])
        self.assertEqual(graph["32"]["class_type"], "ConditioningZeroOut")
        self.assertEqual((graph["44"]["inputs"]["width"], graph["44"]["inputs"]["height"]), (1280, 720))
        self.assertIsInstance(graph["4"]["inputs"]["text"], str)               # raw prompt, no upsampler
        for key in ("steps", "cfg", "sampler_name", "scheduler"):
            self.assertEqual(graph["7"]["inputs"][key], EDIT["7"]["inputs"][key])

    def test_ref_has_four_reference_slots_and_reference_latent(self):
        graph = templates.derive_ref(EDIT)
        self.assertEqual([graph[node]["class_type"] for node in templates.REFERENCE_NODES],
                         ["LoadImage"] * 4)
        inputs = graph["17"]["inputs"]
        self.assertEqual([inputs[f"images.image{i}"] for i in range(4)],
                         [[node, 0] for node in templates.REFERENCE_NODES])
        self.assertEqual(graph["7"]["inputs"]["latent_image"], ["17", 2])
        self.assertEqual(graph["7"]["inputs"]["positive"], ["17", 0])
        self.assertFalse({"18", "33", "49", "51"} & set(graph))

    def test_build_graph_prunes_unused_slots_and_switches_latent(self):
        graph = templates.build_graph("ref", prompt="Create a photograph of the man from the first image",
                                      seed=7, references=["a.png", "b.png"], latent_source="empty_16x9")
        self.assertEqual(graph["11"]["inputs"]["image"], "a.png")
        self.assertEqual(graph["12"]["inputs"]["image"], "b.png")
        self.assertNotIn("13", graph)
        self.assertNotIn("images.image2", graph["17"]["inputs"])
        self.assertEqual(graph["7"]["inputs"]["latent_image"], ["44", 0])
        reference = templates.build_graph("ref", prompt="p", seed=7, references=["a.png"])
        self.assertEqual(reference["7"]["inputs"]["latent_image"], ["17", 2])
        self.assertNotIn("44", reference)
        self.assertNotIn("17", templates.build_graph("ref", prompt="p", seed=7, references=[]))
        with self.assertRaises(templates.TemplateError):
            templates.build_graph("ref", prompt="p", seed=1, references=["a"] * 5)

    def test_upsampler_is_opt_in(self):
        graph = templates.build_graph("ref", prompt="raw", seed=1, references=["a.png"], upsampler=True)
        self.assertEqual(graph["4"]["inputs"]["text"], ["47", 0])
        self.assertEqual(graph["47"]["inputs"]["prompt"], "raw")
        self.assertEqual(graph["50"]["inputs"], {"images.image0": ["11", 0]})
        self.assertEqual(templates.validate_graph(graph, OBJECT_INFO), [])

    def test_saved_templates_match_derivation(self):
        t2i, ref, mapping = templates.load_templates()
        self.assertEqual(t2i, templates.derive_t2i(EDIT))
        self.assertEqual(ref, templates.derive_ref(EDIT))
        self.assertEqual(mapping, templates.default_map())

    def test_validation_against_object_info(self):
        for graph in (templates.build_graph("t2i", prompt="p", seed=1),
                      templates.build_graph("ref", prompt="p", seed=1, references=["a", "b", "c", "d"])):
            self.assertEqual(templates.validate_graph(graph, OBJECT_INFO), [])
        info = copy.deepcopy(OBJECT_INFO)
        del info["ReferenceMultiLatent"]
        info["VAELoader"]["input"]["required"]["vae_name"] = [["other.safetensors"]]
        problems = templates.validate_graph(
            templates.build_graph("ref", prompt="p", seed=1, references=["a"]), info)
        self.assertTrue(any("ReferenceMultiLatent" in problem for problem in problems))
        self.assertTrue(any("flux2-vae.safetensors" in problem for problem in problems))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ref = self.root / "julian.png"
        self.ref.write_bytes(png_bytes((1024, 1024), (90, 80, 70)))

    def tearDown(self):
        self.temp.cleanup()

    def job(self, **kwargs):
        values = {"job_id": "s01_01", "prompt": "Create a photograph of the man from the first image",
                  "seed": 42, "output_path": str(self.root / "shots" / "s01_01.jpg"), "kind": "ref",
                  "references": [str(self.ref)]}
        values.update(kwargs)
        return ImageJob(**values)

    def test_generate_uploads_by_hash_queues_and_upscales(self):
        fake = FakeComfy()
        record = ImageGenerator(self.root, client(fake)).generate(self.job())
        self.assertEqual(len(fake.uploads), 1)
        self.assertTrue(fake.uploads[0][0].startswith("pg_"))
        graph = fake.prompts[0]
        self.assertEqual(graph["11"]["inputs"]["image"], fake.uploads[0][0])
        self.assertEqual(graph["7"]["inputs"]["seed"], 42)
        with Image.open(record["local_path"]) as image:
            self.assertEqual(image.size, (1920, 1080))
        self.assertTrue(record["synthetic"])
        self.assertEqual(record["provider"], "comfyui")
        self.assertEqual(record["model"]["unet"], "flux3-image-klein-int8-convrot.safetensors")
        self.assertFalse(record["cache_hit"])

    def test_cache_hit_skips_comfyui_and_key_tracks_inputs(self):
        fake = FakeComfy()
        generator = ImageGenerator(self.root, client(fake))
        generator.generate(self.job())
        again = generator.generate(self.job(output_path=str(self.root / "copy.jpg")))
        self.assertTrue(again["cache_hit"])
        self.assertEqual(len(fake.prompts), 1)
        base = generator.cache_key(self.job(), 42)
        self.assertNotEqual(base, generator.cache_key(self.job(prompt="other"), 42))
        self.assertNotEqual(base, generator.cache_key(self.job(), 43))
        other_ref = self.root / "elena.png"
        other_ref.write_bytes(png_bytes((1024, 1024), (10, 10, 10)))
        self.assertNotEqual(base, generator.cache_key(self.job(references=[str(other_ref)]), 42))

    def test_retry_with_new_seed_then_fallback(self):
        fake = FakeComfy(fail_runs=1)
        record = ImageGenerator(self.root, client(fake)).generate_with_retry(self.job())
        self.assertNotEqual(fake.prompts[1]["7"]["inputs"]["seed"], 42)
        self.assertIn("retried_after", record)
        failing = FakeComfy(fail_runs=2)
        fallback = ImageGenerator(self.root, client(failing)).generate_with_retry(
            self.job(job_id="s02_01", prompt="different"),
            fallback=lambda job: {"provider": "pixabay", "local_path": "stock.jpg"})
        self.assertEqual(fallback["provider"], "pixabay")
        self.assertEqual(fallback["fallback_from"], "comfyui")
        self.assertEqual(len(fallback["generation_errors"]), 2)
        with self.assertRaises(GenerationFailed):
            ImageGenerator(self.root, client(FakeComfy(fail_runs=2))).generate_with_retry(
                self.job(job_id="s03", prompt="third"))

    def test_comfyui_not_running_is_reported_clearly(self):
        generator = ImageGenerator(self.root, client(FakeComfy(offline=True)))
        with self.assertRaises(ComfyUnavailable) as caught:
            generator.generate_with_retry(self.job())
        self.assertIn("Start ComfyUI", str(caught.exception))
        self.assertFalse(client(FakeComfy(offline=True)).available())

    def test_invalid_graph_asks_for_an_export(self):
        info = copy.deepcopy(OBJECT_INFO)
        del info["EmptyFlux2LatentImage"]
        fake = FakeComfy(object_info=info)
        with self.assertRaises(templates.TemplateError) as caught:
            ImageGenerator(self.root, client(fake)).generate(self.job(kind="t2i", references=[]))
        self.assertIn("Export this variant from ComfyUI", str(caught.exception))
        self.assertEqual(fake.prompts, [])

    def test_vram_and_free_memory(self):
        fake = FakeComfy()
        comfy = client(fake)
        self.assertEqual(comfy.vram_used_mb(), 5120.0)
        comfy.free_memory()
        self.assertEqual(fake.freed, 1)


if __name__ == "__main__":
    unittest.main()
