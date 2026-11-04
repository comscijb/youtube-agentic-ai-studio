"""Phase 3.9.4-C,D: the viewer's brush mask and results in a real browser (no ComfyUI: the batch run is intercepted).
Skipped when Playwright or Chromium is missing."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from agents.narrative import atomic_write_json, load_json
from tests.test_gui_smoke import chromium_available

SHOT = {"shot_id": "s01_01", "scene_id": "s01", "image_path": "images/shots/s01_01.jpg", "start": 0, "end": 5, "shot_type": "wide",
        "seed": 3, "characters": [], "action": "An empty hall."}


@unittest.skipUnless(chromium_available(), "Playwright/Chromium not installed")
class ViewerToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import config
        from scripts import gui_audit
        cls.temp = tempfile.TemporaryDirectory()
        cls.server, cls.url, cls.stack = gui_audit.serve(Path(cls.temp.name))
        cls.output = Path(config.OUTPUT_DIR)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for item in reversed(cls.stack):
            item.stop()
        cls.temp.cleanup()

    def setUp(self):
        (self.output / "images/shots").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1920, 1080), (70, 70, 70)).save(self.output / SHOT["image_path"], quality=95)
        self.save([json.loads(json.dumps(SHOT))])

    def save(self, shots):
        current = load_json(self.output / "shot_list.json") or {}
        current["shots"] = shots
        atomic_write_json(self.output / "shot_list.json", current)

    def shot(self):
        return next(item for item in load_json(self.output / "shot_list.json")["shots"] if item["shot_id"] == "s01_01")

    def viewer(self, playwright, problems):
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda exc: problems.append(str(exc)))
        page.route("**/api/imagegen/queue/run", lambda route: route.fulfill(json={"ok": True}))
        page.goto(self.url)
        page.wait_for_timeout(800)
        page.evaluate("closeHome(); loadImagegen()")
        page.wait_for_function("typeof _igData !== 'undefined' && _igData && (_igData.shots || []).some(s => s.shot_id === 's01_01')")
        page.evaluate("igOpenViewer('s01_01')")
        page.wait_for_function("document.getElementById('igv-img').naturalWidth === 1920")
        page.wait_for_timeout(200)
        return browser, page

    def paint(self, page, dx=0):
        box = page.locator("#igv-frame").bounding_box()
        x, y = box["x"] + box["width"] * (.5 + dx), box["y"] + box["height"] * .5
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 40, y + 60)
        page.mouse.up()

    def test_brush_keys_and_enter_queue_a_mask_and_run(self):
        from playwright.sync_api import sync_playwright
        problems = []
        with sync_playwright() as playwright:
            browser, page = self.viewer(playwright, problems)
            page.keyboard.press("f")
            page.wait_for_selector("#igv-mask")
            start = page.inner_text("#igv-drawbar")
            page.keyboard.press("]")
            self.assertNotEqual(page.inner_text("#igv-drawbar"), start)                  # brush size on ]
            page.keyboard.press("[")
            page.mouse.move(700, 400)
            page.mouse.wheel(0, -100)
            self.assertNotEqual(page.inner_text("#igv-drawbar"), start)                  # and on the wheel
            self.assertIn("Pincel (X)", page.inner_text("#igv-drawbar"))
            page.keyboard.press("Enter")                                                 # nothing painted: nothing runs
            page.wait_for_timeout(100)
            self.assertTrue(page.is_visible("#igv-mask"))
            self.paint(page)
            painted = page.evaluate("[...document.getElementById('igv-mask').getContext('2d').getImageData(0,0,1920,1080).data].filter((v,i)=>i%4===3&&v>127).length")
            self.assertGreater(painted, 1000)
            page.keyboard.press("x")
            self.assertIn("Borracha (X)", page.inner_text("#igv-drawbar"))
            self.paint(page)
            erased = page.evaluate("[...document.getElementById('igv-mask').getContext('2d').getImageData(0,0,1920,1080).data].filter((v,i)=>i%4===3&&v>127).length")
            self.assertLess(erased, painted)
            page.keyboard.press("x")
            self.paint(page, .1)
            with page.expect_request("**/api/imagegen/queue/run"):
                page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            browser.close()
        entry = self.shot()["edit_queue"][0]
        self.assertEqual(entry["kind"], "remove_figure")
        with Image.open(self.output / entry["mask"]) as mask:
            self.assertIsNotNone(mask.convert("L").getbbox())
        self.assertEqual(problems, [])

    def test_esc_cancels_and_a_box_seeds_the_mask(self):
        from playwright.sync_api import sync_playwright
        problems = []
        with sync_playwright() as playwright:
            browser, page = self.viewer(playwright, problems)
            page.keyboard.press("f")
            page.wait_for_selector("#igv-mask")
            page.keyboard.press("b")
            frame = page.locator("#igv-frame").bounding_box()
            page.mouse.move(frame["x"] + frame["width"] * .3, frame["y"] + frame["height"] * .3)
            page.mouse.down()
            page.mouse.move(frame["x"] + frame["width"] * .4, frame["y"] + frame["height"] * .5)
            with page.expect_response("**/mask_preview"):
                page.mouse.up()
            page.wait_for_function("document.getElementById('igv-mask').getContext('2d').getImageData(1920*.35|0,1080*.4|0,1,1).data[3] > 200")
            page.keyboard.press("Escape")
            self.assertFalse(page.is_visible("#igv-mask"))
            page.wait_for_timeout(100)
            browser.close()
        self.assertNotIn("edit_queue", self.shot())
        self.assertEqual(problems, [])

    def test_three_results_side_by_side_and_use_this(self):
        from playwright.sync_api import sync_playwright
        folder = self.output / "images/shots/removal"
        folder.mkdir(parents=True, exist_ok=True)
        candidates = []
        for index, colour in enumerate(((90, 0, 0), (0, 90, 0), (0, 0, 90))):
            path = folder / f"c{index}.jpg"
            Image.new("RGB", (1920, 1080), colour).save(path, quality=95)
            candidates.append({"path": f"images/shots/removal/c{index}.jpg", "profile": "base", "engine": "opencv", "clean": index != 2,
                               "people_inside": [1, 0 if index != 2 else 1], "box": [.1, .1, .2, .2], "crop": [0, 0, 100, 100],
                               "mask": "images/shots/removal/m.png"})
        Image.new("L", (1920, 1080), 0).save(folder / "m.png")
        self.save([{**SHOT, "removal": {"candidates": candidates, "at": "t"}}])
        problems = []
        with sync_playwright() as playwright:
            browser, page = self.viewer(playwright, problems)
            page.wait_for_selector("#igv-results img")
            self.assertEqual(page.locator("#igv-results img").count(), 3)
            tops = [page.locator("#igv-results img").nth(n).bounding_box()["y"] for n in range(3)]
            self.assertEqual(len(set(tops)), 1)                                          # side by side
            self.assertEqual(page.locator("#igv-results button", has_text="Usar este").count(), 3)
            page.locator("#igv-results img").nth(1).click()
            page.wait_for_function("document.getElementById('igv-img').src.includes('c1.jpg')")
            with page.expect_response("**/removal/choose"):
                page.locator("#igv-results button", has_text="Usar este").nth(1).click()
            page.wait_for_function("document.getElementById('igv-results').style.display === 'none'")
            browser.close()
        saved = self.shot()
        self.assertNotIn("removal", saved)
        self.assertEqual(saved["qa"]["actions"][-1]["candidate"], 2)
        with Image.open(self.output / SHOT["image_path"]) as image:
            self.assertGreater(image.convert("RGB").getpixel((5, 5))[1], 70)
        self.assertEqual(problems, [])

    def test_import_with_a_choice_of_crop(self):
        from playwright.sync_api import sync_playwright
        wide = Path(self.temp.name) / "edited.png"
        Image.new("RGB", (1024, 1024), (10, 200, 10)).save(wide)
        problems = []
        with sync_playwright() as playwright:
            browser, page = self.viewer(playwright, problems)
            page.wait_for_selector("text=Importar imagem editada")
            with page.expect_file_chooser() as chooser:
                page.click("text=Importar imagem editada")
            with page.expect_response("**/import"):
                chooser.value.set_files(str(wide))
            page.wait_for_selector("#igv-importbox")
            before = page.locator("#igv-importbox").bounding_box()
            page.mouse.move(before["x"] + 20, before["y"] + 20)
            page.mouse.down()
            page.mouse.move(before["x"] + 20, before["y"] + 60)
            page.mouse.up()
            self.assertGreater(page.locator("#igv-importbox").bounding_box()["y"], before["y"])
            with page.expect_response("**/import"):
                page.click("text=Usar este recorte")
            page.wait_for_function("document.getElementById('igv-head').innerText.includes('editada fora')")
            browser.close()
        saved = self.shot()
        self.assertEqual(saved["source"], "external")
        self.assertEqual(saved["qa"]["actions"][-1]["fit"]["fit"], "crop")
        self.assertTrue((self.output / saved["qa"]["actions"][-1]["original"]).is_file())
        with Image.open(self.output / SHOT["image_path"]) as image:
            self.assertEqual(image.size, (1920, 1080))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
