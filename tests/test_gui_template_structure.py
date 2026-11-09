"""Phase 3.9.1-A: the GUI template's structure — balanced tags, one level of tabs, unique ids.

A stray ``<div class="field-label">Voice<br>          <div>`` in the wizard (93074a3, phase 3.8-C)
left two divs open, so Review & Edit and Settings were parsed inside the "Novo projeto" tab and
showed nothing. The JS syntax check could not see it; this test does (standard library only).
"""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "gui_templates" / "index.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs, line, parent):
        self.tag, self.attrs, self.line, self.parent = tag, dict(attrs), line, parent
        self.children = []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class TreeParser(HTMLParser):
    """Builds the element tree and records every tag that closes out of order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], 0, None)
        self.stack = [self.root]
        self.problems: list[str] = []
        self.ids: dict[str, list[int]] = {}

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        node = Node(tag, attrs, line, self.stack[-1])
        self.stack[-1].children.append(node)
        if node.attrs.get("id"):
            self.ids.setdefault(node.attrs["id"], []).append(line)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag in VOID:
            return
        if self.stack[-1].tag == tag:
            self.stack.pop()
            return
        open_tags = [node.tag for node in self.stack]
        self.problems.append(f"line {line}: </{tag}> closes <{self.stack[-1].tag}> opened at line {self.stack[-1].line}")
        if tag in open_tags:                             # recover: unwind to the matching element
            while self.stack[-1].tag != tag:
                self.stack.pop()
            self.stack.pop()

    def close(self):
        super().close()
        for node in self.stack[1:]:
            self.problems.append(f"line {node.line}: <{node.tag}> is never closed")


def parse(text: str) -> TreeParser:
    parser = TreeParser()
    parser.feed(text)
    parser.close()
    return parser


def walk(node):
    for child in node.children:
        yield child
        yield from walk(child)


def structure_problems(text: str) -> list[str]:
    parser = parse(text)
    problems = list(parser.problems)
    nodes = list(walk(parser.root))
    tabs = [node for node in nodes if "tab-content" in node.classes]
    for tab in tabs:
        parent = tab.parent
        if not (parent.tag == "main" and "main" in parent.classes):
            problems.append(f"line {tab.line}: #{tab.attrs.get('id')} is not a direct child of main.main "
                            f"(inside <{parent.tag} id={parent.attrs.get('id')!r} class={parent.attrs.get('class')!r}>)")
        ancestor = tab.parent
        while ancestor is not None:
            if "tab-content" in ancestor.classes:
                problems.append(f"line {tab.line}: #{tab.attrs.get('id')} is inside #{ancestor.attrs.get('id')}")
            ancestor = ancestor.parent
    tab_ids = {tab.attrs.get("id") for tab in tabs}
    for button in (node for node in nodes if "tab" in node.classes):
        name = button.attrs.get("data-tab")
        if not name:
            problems.append(f"line {button.line}: tab button without data-tab")
        elif f"tab-{name}" not in tab_ids:
            problems.append(f"line {button.line}: tab button {name!r} has no #tab-{name}")
    for key, lines in parser.ids.items():
        if len(lines) > 1:
            problems.append(f"id {key!r} is used {len(lines)} times (lines {lines})")
    return problems


class TemplateStructureTests(unittest.TestCase):
    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def test_template_structure(self):
        self.assertEqual(structure_problems(self.text), [])

    def test_the_93074a3_corruption_is_caught(self):
        line = '<div class="field-label">Voice<br>          <div>'
        anchor = '<div class="tab-content active" id="tab-generate">'
        broken = self.text.replace(anchor, anchor + "\n" + line, 1)
        problems = structure_problems(broken)
        self.assertTrue(any("tab-review" in item and "not a direct child" in item for item in problems), problems)

    def test_switch_tab_uses_data_tab(self):
        body = re.search(r"function switchTab\(name\) \{(.*?)\n\}", self.text, re.S).group(1)
        self.assertIn("dataset.tab", body)
        self.assertNotIn("const names", body)
        buttons = re.findall(r'class="tab(?: active)?" data-tab="(\w+)"', self.text)
        self.assertEqual(len(buttons), len(set(buttons)))
        self.assertGreaterEqual(len(buttons), 5)


if __name__ == "__main__":
    unittest.main()
