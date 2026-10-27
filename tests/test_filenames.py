"""File names built from IDs must be valid on Windows, checked on every OS (the Windows rules, not the
local file system, decide): an ``OSError: [Errno 22]`` from a ``|`` in a qid only showed up on Windows."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studio.filenames import safe_filename, windows_problems
from tests.test_qa_focused import VlmComfy, frame
from video.imagegen_comfyui import ComfyClient
from video.qa import base, fixes
from video.qa.comfy_vlm import ComfyVLM

HOSTILE = ["0|gender_0", "1|marker:0:black horn-rimmed glasses", "what?", "a*b", 'say "hi"', "<tag>", "dir/x",
           "back\\slash", "tab\there", "bell\x07", "trailing. ", "dots...", "CON", "nul.txt", "Com1", "lpt9.jpg",
           "", "   ", "..", "x" * 400, "s01_01", "julian_vance"]


class SafeFilenameTests(unittest.TestCase):
    def test_every_result_is_a_valid_windows_name(self):
        for name in HOSTILE:
            with self.subTest(name=name):
                result = safe_filename(name)
                self.assertEqual(windows_problems(result), [])
                self.assertEqual(windows_problems(result + ".png"), [])
                self.assertLessEqual(len(result), 120)

    def test_ordinary_ids_are_unchanged(self):
        for name in ("s01_01", "julian_vance", "2026-10-21-the-man", "s04_03_v2"):
            self.assertEqual(safe_filename(name), name)

    def test_the_validator_catches_what_windows_rejects(self):
        for bad in ("a|b.png", "q:1.png", "x?.png", "*.png", "CON.png", "name.", "name ", "", "a\x01b"):
            self.assertTrue(windows_problems(bad), bad)
        self.assertEqual(windows_problems("q0000.png"), [])


class GeneratedNamesTests(unittest.TestCase):
    def test_vlm_crops_never_take_their_name_from_the_qid(self):
        qids = ["0|gender_0", "0|marker:0:black horn-rimmed glasses", "1|what?*", "1|text"]
        questions = [base.Question(qid, "text", frame(), "Is there writing? Answer with one word: yes, no or unclear.")
                     for qid in qids]
        fake = VlmComfy(["yes", "no", '<div class="answer" data-ans="no">no</div>', "unclear"])
        vlm = ComfyVLM(ComfyClient(session=fake, sleep=lambda _: None), model="qwen3vl_4b_int8_convrot.safetensors")
        saved = []
        upload = vlm.client.upload_image
        with patch.object(vlm.client, "upload_image", side_effect=lambda path: saved.append(Path(path).name)
                          or upload(path)):
            answers = vlm.ask(questions)
        self.assertEqual(answers, dict(zip(qids, ["yes", "no", "no", "unclear"])))
        self.assertEqual(saved, ["q0000.png", "q0001.png", "q0002.png", "q0003.png"])
        for name in saved:
            self.assertEqual(windows_problems(name), [])

    def test_history_versions_of_a_hostile_shot_id_are_valid_names(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            image = output / "current.jpg"
            Image.new("RGB", (8, 8)).save(image)
            item = {"shot_id": "s01|01:a?*"}
            entry = fixes.save_version(output, item, image)
            self.assertTrue((output / entry["path"]).is_file())
        self.assertEqual(windows_problems(Path(entry["path"]).name), [])


if __name__ == "__main__":
    unittest.main()
