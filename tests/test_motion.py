"""Phase 3.9-B: parallax math and fallbacks, ambient whitelist and photosensitivity, pacing variety,
transitions, key-moment clips gated off (no depth model, no ComfyUI)."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import config
from agents import shot_list as shots_module
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import ambient, key_clips, motion

W, H = 320, 180


def gradient_depth(blob=True):
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    depth = ys / H * 0.6
    if blob:
        depth += 0.4 * np.exp(-(((xs - W * 0.6) / 40) ** 2 + ((ys - H * 0.55) / 35) ** 2))
    return (depth - depth.min()) / (depth.max() - depth.min())


class ParallaxTests(unittest.TestCase):
    def test_offsets_move_near_layers_more_and_stay_within_the_limit(self):
        start, middle, end = (motion.offsets(p, 3, 1.0, 1920) for p in (0.0, 0.5, 1.0))
        self.assertEqual(middle, [0.0, 0.0, 0.0])
        travel = [b - a for a, b in zip(start, end)]
        self.assertTrue(travel[0] < travel[1] < travel[2])                     # far → near
        self.assertAlmostEqual(travel[2], motion.PARALLAX_MAX * 1920)         # ≤ 2.5 % of the width
        gentle = motion.offsets(1.0, 2, 0.6, 1920)
        self.assertLess(abs(gentle[-1]) * 2, motion.PARALLAX_MAX * 1920)

    def test_layers_split_by_depth_and_render_a_full_frame(self):
        depth = gradient_depth()
        masks = motion.layers_from(depth, 3)
        self.assertEqual(len(masks), 3)
        self.assertTrue(np.logical_or.reduce(masks).all())                     # every pixel in one layer
        self.assertFalse((masks[0] & masks[2]).any())
        image = np.random.default_rng(1).integers(0, 255, (H, W, 3), dtype=np.uint8)
        layers = motion.build_layers(image, masks)
        frame = motion.render(layers, 0.3, 1.0, (W, H))
        self.assertEqual((frame.shape, frame.dtype), ((H, W, 3), np.uint8))

    def test_fallbacks(self):
        with patch.object(config, "MOTION_STYLE", "parallax"):
            self.assertEqual(motion.plan(gradient_depth(), "wide")["kind"], "parallax")
            self.assertEqual(motion.plan(None, "wide")["reason"], "no depth map")
            flat = np.full((H, W), 0.5, np.float32)
            self.assertEqual(motion.plan(flat, "wide")["reason"], "flat depth")
            noisy = np.random.default_rng(2).random((H, W)).astype(np.float32)
            self.assertEqual(motion.plan(noisy, "wide")["reason"], "noisy depth")
            face_on_edge = [[0.45, 0.2, 0.75, 0.9]]                            # straddles the near blob's edge
            self.assertEqual(motion.plan(gradient_depth(), "wide", face_on_edge)["reason"],
                             "a face crosses a layer boundary")
            self.assertEqual(motion.plan(gradient_depth(), "close_up")["kind"], "push")
            self.assertEqual(motion.plan(gradient_depth(), "insert")["kind"], "drift")
        with patch.object(config, "MOTION_STYLE", "kenburns"):
            self.assertEqual(motion.plan(gradient_depth(), "wide")["kind"], "kenburns")
        stripes = np.zeros((H, W), bool)
        stripes[:, ::12] = True                                                # 1-px poles: torn by a shift
        self.assertGreater(motion.thin_share(stripes), motion.THIN_SHARE)

    def test_depth_is_cached_and_redone_after_an_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp, "s01_01.jpg")
            Image.new("RGB", (W, H)).save(image)
            with patch.object(motion, "estimate", return_value=gradient_depth()) as estimate:
                motion.depth_map(temp, "s01_01", image)
                motion.depth_map(temp, "s01_01", image)
                self.assertEqual(estimate.call_count, 1)
                cached = motion.depth_path(temp, "s01_01")
                future = time.time() + 10
                os.utime(image, (future, future))                                # the image was edited
                motion.depth_map(temp, "s01_01", image)
                self.assertEqual(estimate.call_count, 2)
            self.assertTrue(cached.is_file())
            with patch.object(motion, "estimate", side_effect=motion.DepthUnavailable("none")):
                self.assertIsNone(motion.depth_map(temp, "s02_01", image))


class AmbientTests(unittest.TestCase):
    def test_whitelist_and_the_scene_must_support_it(self):
        self.assertEqual(ambient.validate("rain", "Rain ran down the window."), "rain")
        self.assertEqual(ambient.validate("steam", "He filled the kettle."), "steam")
        self.assertEqual(ambient.validate("rain", "A dry, bright afternoon."), "")
        self.assertEqual(ambient.validate("lava", "lava everywhere"), "")
        self.assertEqual(ambient.validate("", "rain"), "")

    def test_overlays_respect_the_photosensitivity_guard(self):
        from video import tape
        for kind in ambient.KINDS:
            frames = ambient.generate(kind, size=(96, 54), frames=48)
            with self.subTest(kind=kind):
                self.assertLessEqual(ambient.max_step(frames), tape.MAX_LUMA_STEP)
        candle = ambient.generate("candle", size=(96, 54), frames=48)
        means = candle.mean(axis=(1, 2, 3))
        self.assertLessEqual((means.max() - means.min()) / means.mean(), 2 * ambient.MAX_FLICKER)   # ±3 %

    def test_screen_blend_only_lightens(self):
        frame = np.full((54, 96, 3), 60, np.uint8)
        lit = ambient.apply(frame, ambient.generate("candle", size=(96, 54), frames=4), 0.0)
        self.assertTrue((lit >= frame).all())
        self.assertGreater(int(lit.max()), 60)

    def test_contract_and_shot_list_field(self):
        with patch.object(config, "AMBIENT_ENABLED", True):
            self.assertIn("ambient (optional)", shots_module._scene_contract("reference"))
        with patch.object(config, "AMBIENT_ENABLED", False):
            self.assertNotIn("ambient (optional)", shots_module._scene_contract("reference"))


class PacingTests(unittest.TestCase):
    def sentences(self):
        lengths = [1.5, 6, 7, 2, 1.2, 8, 9, 2.2, 5, 6, 1.4, 7, 8, 1.8, 6, 2, 9, 4]
        items, cursor = [], 0.0
        for index, length in enumerate(lengths):
            items.append({"start": cursor, "end": cursor + length, "text": f"Sentence {index}."})
            cursor += length + (0.6 if length < 2.5 else 0.15)
        return items, cursor

    def test_varied_cuts_are_less_metronomic_and_tile_the_scene(self):
        sentences, end = self.sentences()
        scene = {"start": 0.0, "end": end}
        fixed = shots_module.segment_scene(scene, sentences, 6.0, 9.0)
        varied = shots_module.varied_segments(scene, sentences, 3.5, 16.0)
        self.assertEqual((varied[0]["start"], varied[-1]["end"]), (0.0, round(end, 3)))
        for a, b in zip(varied, varied[1:]):
            self.assertEqual(a["end"], b["start"])
        self.assertGreater(shots_module.pacing_cv(varied), shots_module.pacing_cv(fixed))
        self.assertEqual(shots_module.pacing_cv([{"start": 0, "end": 5}, {"start": 5, "end": 10}]), 0.0)

    def test_window_follows_the_setting(self):
        with patch.object(config, "PACING_VARIETY", True):
            self.assertEqual(shots_module.pacing_window(), (3.5, 16.0))
        with patch.object(config, "PACING_VARIETY", False):
            self.assertEqual(shots_module.pacing_window(), (6.0, 9.0))

    def test_hard_cuts_inside_a_scene_and_crossfade_on_a_time_jump(self):
        from video import creator
        with patch.object(config, "SHOT_TRANSITIONS", "smart"):
            self.assertEqual(creator.transition_into("\"I think she hated me too.\""), "cut")
            self.assertEqual(creator.transition_into("Two weeks later, the kettle boiled."), "crossfade")
            self.assertEqual(creator.transition_into("The next morning he called June."), "crossfade")
        with patch.object(config, "SHOT_TRANSITIONS", "crossfade"):
            self.assertEqual(creator.transition_into("He waited."), "crossfade")


class FakeClient:
    base_url = "http://localhost:8188"
    timeout = 5

    def __init__(self, models):
        self.models = models

    def available(self):
        return True

    def object_info(self):
        return {node: {} for node in key_clips.NODES}

    @property
    def session(self):
        client = self

        class Session:
            def get(self, url, timeout):
                class Response:
                    def json(self):
                        return client.models.get(url.rsplit("/", 1)[-1], [])
                return Response()
        return Session()


class KeyMomentTests(unittest.TestCase):
    def test_gated_off_by_default_and_probe_first(self):
        self.assertEqual(config.KEY_MOMENT_CLIPS, 0)
        self.assertFalse(key_clips.enabled())
        missing = key_clips.probe(FakeClient({}))
        self.assertFalse(missing["runs"])
        self.assertIn("wan2.2_ti2v_5B", missing["reason"])
        ready = key_clips.probe(FakeClient({"diffusion_models": ["wan2.2_ti2v_5B_fp16.safetensors"],
                                            "text_encoders": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
                                            "vae": ["wan2.2_vae.safetensors"]}))
        self.assertTrue(ready["runs"])
        graph = key_clips.workflow("shot.png", "the same engraved style", ready["models"], seconds=3.5)
        self.assertEqual(graph["7"]["inputs"]["length"] % 4, 1)                 # Wan: 4k+1 frames
        self.assertIn("photorealistic", graph["6"]["inputs"]["text"])            # never a realistic look
        with patch.object(config, "KEY_MOMENT_CLIPS", 9):
            self.assertEqual(key_clips.count(), 3)


if __name__ == "__main__":
    unittest.main()
