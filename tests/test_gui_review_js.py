"""Phase 3.5.2: the shot-review page script — syntax check and the pure review helpers, run in Node.

Skipped when Node.js is not installed. The helpers decide what the grid counts as a warning
("unclear" answers never are), how repeated hints collapse, the card badge and the filters.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "gui_templates" / "index.html"
NODE = shutil.which("node")


def scripts() -> str:
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", PAGE.read_text(encoding="utf-8")))


def helpers() -> str:
    """The pure part of the review block (no DOM at load time)."""
    source = scripts()
    start = source.index("// ── Shot review (3.5.2)")
    end = source.index("function igSetFilter(")
    return source[start:end]


def run_node(code: str) -> str:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp, "check.js")
        path.write_text(code, encoding="utf-8")
        done = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise AssertionError(done.stderr)
    return done.stdout


@unittest.skipUnless(NODE, "Node.js not installed")
class ReviewScriptTests(unittest.TestCase):
    def test_page_scripts_parse(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "page.js")
            path.write_text(scripts(), encoding="utf-8")
            done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_unclear_answers_never_count_and_repeats_collapse(self):
        hint = {"type": "extra_person", "severity": "advisory", "hint": True, "box": [0.1, 0.1, 0.2, 0.5]}
        shots = [
            {"shot_id": "a", "image_path": "a.jpg", "qa": {"issues": [
                hint, dict(hint, box=[0.6, 0.1, 0.7, 0.5]),
                {"type": "text", "severity": "advisory", "note": "visible writing or a watermark (unclear)"},
                {"type": "missing_marker", "severity": "advisory", "unclear": True}]}},
            {"shot_id": "b", "image_path": "b.jpg", "qa": {"issues": [
                {"type": "unstated_injury", "severity": "advisory", "note": "blood (unclear)"}]}},
            {"shot_id": "c", "image_path": "c.jpg", "qa": {"reviewed": True, "issues": [hint]}},
            {"shot_id": "d", "image_path": "d.jpg", "edit_queue": [{"kind": "regenerate"}], "qa": {"issues": []}},
            {"shot_id": "e", "image_path": "e.jpg", "qa": {"history": [{"version": 1}], "issues": []}},
        ]
        code = helpers() + f"""
_igData = {{shots: {json.dumps(shots)}, current_shot: null}};
const out = {{}};
for (const shot of _igData.shots) {{
  out[shot.shot_id] = {{warnings: igWarnings(shot).length, badge: igBadge(shot)[0],
                        groups: igCollapse(igWarnings(shot)).map(g => [g.issue.type, g.count])}};
}}
out.filters = {{}};
for (const [value] of IG_FILTERS) out.filters[value] = _igData.shots.filter(s => igMatches(s, value)).map(s => s.shot_id);
_igData.current_shot = 'd';
out.processing = igBadge(_igData.shots[3])[0];
console.log(JSON.stringify(out));
"""
        out = json.loads(run_node(code))
        self.assertEqual(out["a"], {"warnings": 2, "badge": "2 avisos", "groups": [["extra_person", 2]]})
        self.assertEqual((out["b"]["warnings"], out["b"]["badge"]), (0, "OK"))          # unclear only
        self.assertEqual(out["c"]["badge"], "revisado")
        self.assertEqual(out["d"]["badge"], "na fila")
        self.assertEqual(out["e"]["badge"], "editado")
        self.assertEqual(out["processing"], "processando")
        self.assertEqual(out["filters"], {"all": ["a", "b", "c", "d", "e"], "warnings": ["a"],
                                          "unreviewed": ["a", "b", "d", "e"], "edited": ["e"], "ok": ["b", "c", "e"]})


if __name__ == "__main__":
    unittest.main()
