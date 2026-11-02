"""Phase 3.0-A: UTF-8 everywhere, mojibake guard and repair."""
import ast
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents.narrative import (
    MojibakeError,
    assert_no_mojibake,
    has_mojibake,
    repair_mojibake,
)
from scripts import repair_text_encoding

ROOT = Path(__file__).resolve().parent.parent
CLEAN = "I don’t know, his father’s dinner—eight “guests” at the café."


def windows_misdecode(text: str) -> str:
    """What open() without encoding produces on Windows (cp1252) for UTF-8 bytes."""
    return "".join(chr(b) if b in (0x81, 0x8D, 0x8F, 0x90, 0x9D) else bytes([b]).decode("cp1252")
                   for b in text.encode("utf-8"))


def _text_mode(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else next(
        (kw.value for kw in call.keywords if kw.arg == "mode"), None)
    if mode is None:
        return True
    return not (isinstance(mode, ast.Constant) and "b" in str(mode.value))


def unencoded_text_io(root: Path) -> list[str]:
    """open()/io.open()/read_text()/write_text() in text mode without encoding=."""
    problems = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".venv", "venv", "__pycache__", "node_modules"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
            if isinstance(func, ast.Name) and func.id == "open":
                flagged = _text_mode(node) and not has_encoding
            elif (isinstance(func, ast.Attribute) and func.attr == "open"
                  and isinstance(func.value, ast.Name) and func.value.id in {"io", "codecs"}):
                flagged = _text_mode(node) and not has_encoding
            elif isinstance(func, ast.Attribute) and func.attr in {"read_text", "write_text"}:
                flagged = not has_encoding
            else:
                flagged = False
            if flagged:
                problems.append(f"{path.relative_to(root)}:{node.lineno}")
    return problems


class SourceEncodingTests(unittest.TestCase):
    def test_no_text_io_without_explicit_encoding(self):
        problems = unencoded_text_io(ROOT)
        self.assertEqual(problems, [], "text I/O without encoding=: " + ", ".join(problems))

    def test_scanner_catches_the_known_bad_patterns(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "bad.py").write_text(
                "open('a')\nopen('a', 'w')\nPath('a').read_text()\nopen('a', 'rb')\n"
                "open('a', encoding='utf-8')\n", encoding="utf-8")
            self.assertEqual(unencoded_text_io(Path(temp)), ["bad.py:1", "bad.py:2", "bad.py:3"])


class ConfigPatchTests(unittest.TestCase):
    SOURCE = ('# ─────────────────────────────────────────\r\n'
              '#  Voice — “Andrew”\r\n'
              'VOICE_ID    = "en-US-AndrewNeural"\r\n'
              'VOICE_RATE  = "-5%"     # slow down\r\n'
              'MUSIC_VOLUME = 0.12\r\n'
              'CHANNEL_DESCRIPTION = """\r\nOld text\r\n"""\r\n')

    def test_config_patch_keeps_utf8_box_characters_and_crlf(self):
        import gui
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "config.py")
            path.write_bytes(self.SOURCE.encode("utf-8"))
            gui.patch_config_file(str(path), {"voice_rate": "+0%", "music_volume": 0.2,
                                              "channel_desc": "New line one\nLine two"})
            raw = path.read_bytes()
        text = raw.decode("utf-8")
        self.assertIn("# ─────────────", text)
        self.assertIn("“Andrew”", text)
        self.assertIn('VOICE_RATE  = "+0%"\r\n', text)
        self.assertIn("MUSIC_VOLUME = 0.2\r\n", text)
        self.assertIn('CHANNEL_DESCRIPTION = """\r\nNew line one\r\nLine two\r\n"""', text)
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))

    def test_lf_config_stays_lf(self):
        import gui
        result = gui.patch_config_source(self.SOURCE.replace("\r\n", "\n"), {"voice_id": "x"})
        self.assertNotIn("\r", result)
        self.assertIn('VOICE_ID    = "x"\n', result)

    def test_gui_reads_artifacts_as_utf8(self):
        source = (ROOT / "gui.py").read_text(encoding="utf-8")
        self.assertIn('research.json", encoding="utf-8"', source)
        self.assertIn('script.json", encoding="utf-8"', source)


class MojibakeTests(unittest.TestCase):
    def test_repair_round_trips_windows_misdecoding(self):
        broken = windows_misdecode(CLEAN)
        self.assertIn("â€™", broken)
        self.assertTrue(has_mojibake(broken))
        self.assertEqual(repair_mojibake(broken), CLEAN)

    def test_clean_text_is_untouched(self):
        for text in (CLEAN, "Ångström, naïve, “quoted” — fine.", "plain ascii"):
            self.assertFalse(has_mojibake(text))
            self.assertEqual(repair_mojibake(text), text)

    def test_guard_names_the_scene(self):
        script = {"sections": [{"id": 1, "narration": CLEAN},
                               {"id": 2, "narration": windows_misdecode(CLEAN)}]}
        with self.assertRaises(MojibakeError) as caught:
            assert_no_mojibake(script, "speech synthesis")
        self.assertIn("scene 2 narration", str(caught.exception))
        self.assertNotIn("scene 1", str(caught.exception))
        assert_no_mojibake({"sections": [{"id": 1, "narration": CLEAN}]})

    def test_narration_refuses_mojibake_before_any_tts(self):
        from audio.tts import edge
        from video import narrator
        script = {"sections": [{"id": 3, "narration": windows_misdecode(CLEAN)}]}
        with patch.object(edge, "_synthesise") as synth, tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(MojibakeError):
                narrator.generate_narration(script, temp)
        synth.assert_not_called()

    def test_repair_script_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "script.json")
            broken = {"title": "ok", "sections": [{"id": 1, "narration": windows_misdecode(CLEAN)}]}
            path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                repair_text_encoding.main(["--dir", temp])
            self.assertIn("would change", output.getvalue())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), broken)
            with redirect_stdout(io.StringIO()):
                repair_text_encoding.main(["--dir", temp, "--apply"])
            repaired = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["sections"][0]["narration"], CLEAN)
            self.assertTrue(Path(temp, "script.json.bak").exists())


if __name__ == "__main__":
    unittest.main()
