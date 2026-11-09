"""Phase 3.9.6-B: the ghost detector flags a silhouette left by a removal and passes a real repaint."""
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from video.qa import ghosts

FIXTURES = Path("tests/fixtures/removal_ghosts")
SIZE = (400, 300)


def hatching(size=SIZE, seed=1) -> np.ndarray:
    """Dense crossing ink lines with noise, like the channel's engraving style."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size[1], 0:size[0]]
    lines = 60 + 50 * (np.sin(x * 0.9 + y * 0.35) > 0.6) + 40 * (np.sin(x * 0.4 - y * 1.1) > 0.7)
    return np.clip(lines + rng.normal(0, 18, lines.shape), 0, 255).astype(np.uint8)


def figure_mask(size=SIZE) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((160, 60, 240, 260), fill=255)
    return mask


def rgb(gray: np.ndarray) -> Image.Image:
    return Image.fromarray(np.stack([gray] * 3, axis=-1))


class SyntheticTests(unittest.TestCase):
    def test_a_clean_hatched_fill_passes(self):
        base = hatching()
        inside = np.asarray(figure_mask()) > 127
        repaint = np.where(inside, hatching(seed=5), base)
        self.assertFalse(ghosts.score(rgb(repaint), figure_mask())["ghost"])

    def test_a_smooth_blob_on_hatching_is_a_ghost(self):
        base = hatching()
        inside = np.asarray(figure_mask()) > 127
        smeared = np.where(inside, np.full_like(base, int(base.mean())), base)
        found = ghosts.score(rgb(smeared), figure_mask())
        self.assertTrue(found["ghost"])
        self.assertIn("texture", found["failed"])

    def test_a_blocky_patch_is_a_ghost(self):
        base = hatching()
        small = Image.fromarray(hatching(seed=9)).resize((SIZE[0] // 16, SIZE[1] // 16), Image.BILINEAR)
        blocks = np.asarray(small.resize(SIZE, Image.NEAREST))
        inside = np.asarray(figure_mask()) > 127
        found = ghosts.score(rgb(np.where(inside, blocks, base)), figure_mask())
        self.assertTrue(found["ghost"])
        self.assertTrue({"blocks", "texture"} & set(found["failed"]))

    def test_a_retained_contour_is_a_ghost(self):
        base = hatching()
        inside = np.asarray(figure_mask()) > 127
        repaint = np.where(inside, hatching(seed=5), base)
        traced = Image.fromarray(repaint)
        ImageDraw.Draw(traced).ellipse((160, 60, 240, 260), outline=250, width=4)
        found = ghosts.score(rgb(np.asarray(traced)), figure_mask())
        self.assertTrue(found["ghost"])
        self.assertIn("contour", found["failed"])

    def test_a_tone_step_is_a_ghost(self):
        base = hatching()
        inside = np.asarray(figure_mask()) > 127
        darker = np.where(inside, np.clip(hatching(seed=5).astype(int) - 70, 0, 255).astype(np.uint8), base)
        found = ghosts.score(rgb(darker), figure_mask())
        self.assertTrue(found["ghost"])
        self.assertIn("tone", found["failed"])

    def test_the_contour_can_be_the_figure_inside_a_grown_region(self):
        base = hatching()
        grown = figure_mask().filter(ImageFilter.MaxFilter(21))
        inside = np.asarray(grown) > 127
        repaint = Image.fromarray(np.where(inside, hatching(seed=5), base))
        ImageDraw.Draw(repaint).ellipse((160, 60, 240, 260), outline=250, width=4)
        self.assertIn("contour", ghosts.score(rgb(np.asarray(repaint)), grown, figure_mask())["failed"])

    def test_a_tiny_mask_is_skipped_not_judged(self):
        tiny = Image.new("L", SIZE, 0)
        tiny.paste(255, (10, 10, 13, 13))
        self.assertTrue(ghosts.score(rgb(hatching()), tiny).get("skipped"))

    def test_blur_fill_only_touches_the_hole(self):
        base = rgb(hatching())
        filled = np.asarray(ghosts.blur_fill(base, figure_mask()))
        outside = np.asarray(figure_mask()) < 128
        self.assertTrue(np.array_equal(filled[outside], np.asarray(base)[outside]))
        self.assertLess(filled[~outside].std(), np.asarray(base)[~outside].std())


class FixtureTests(unittest.TestCase):
    """Six results from the Ferryman project (3.9.4 pipeline, LaMa + Klein at 0.45): every one shows the silhouette."""

    def test_the_six_ferryman_results_are_ghosts(self):
        files = sorted(FIXTURES.glob("*_restored.jpg"))
        self.assertEqual(len(files), 6)
        for path in files:
            mask = Image.open(FIXTURES / f"{path.name[:6]}_mask.png")
            found = ghosts.score(Image.open(path), mask)
            self.assertTrue(found["ghost"], path.name)
            self.assertIn("texture", found["failed"], path.name)

    def test_the_same_masks_over_untouched_parts_pass(self):
        for shot, shift in (("s01_04", -300), ("s07_07", -380)):
            mask = np.asarray(Image.open(FIXTURES / f"{shot}_mask.png")) > 127
            moved = np.roll(mask, shift, axis=1)
            self.assertFalse((moved & mask).any())
            found = ghosts.score(Image.open(FIXTURES / f"{shot}_crop.jpg"), Image.fromarray((moved * 255).astype(np.uint8)))
            self.assertFalse(found["ghost"], (shot, found))


if __name__ == "__main__":
    unittest.main()
