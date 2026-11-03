"""Screenshot audit of the GUI against the fixture project (Phase 3.9.1-D).

Run:  python -m scripts.gui_audit --out docs/reports/gui-3.9.1/after
      (needs: pip install playwright && python -m playwright install chromium)

Copies tests/fixtures/gui_project into a temporary projects folder, serves the GUI on a free local port
with Gemini disabled (no live calls), and captures the home screen, every tab, the Vozes panel, the
shot viewer and the blank "Novo projeto" draft at 1440x900 and 1280x720. Console errors and page
errors are written to <out>/console.json.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "gui_project"
SLUG = "gui-fixture"
VIEWPORTS = [(1440, 900), (1280, 720)]
TABS = ["project", "generate", "review", "settings", "performance"]


class RestoredState:
    """Puts the GUI's ``_state`` back exactly (nested dicts included) when the audit server stops."""

    def __init__(self, state: dict):
        self.state = state

    def start(self):
        self.saved = copy.deepcopy(self.state)

    def stop(self):
        self.state.clear()
        self.state.update(self.saved)


def serve(temp: Path):
    """Start the GUI on a free port against a copy of the fixture project; returns (server, url, stack)."""
    import logging

    import config
    from werkzeug.serving import make_server
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    projects_dir = temp / "projects"
    shutil.copytree(FIXTURE, projects_dir / SLUG)
    stack = [patch.object(config, "PROJECTS_DIR", str(projects_dir)),
             patch.object(config, "GUI_STATE_FILE", str(temp / "gui_state.json"), create=True),
             patch.object(config, "PUBLICATIONS_FILE", str(temp / "publications.json")),
             patch.object(config, "EPISODE_PROFILES_FILE", str(temp / "profiles.json"), create=True),
             patch.object(config, "GEMINI_API_KEY", "")]
    import gui
    stack += [patch.object(config, "OUTPUT_DIR", config.OUTPUT_DIR), patch.object(config, "IMAGES_DIR", config.IMAGES_DIR),
              RestoredState(gui._state)]
    for item in stack:
        item.start()
    (temp / "gui_state.json").write_text(json.dumps({"active": SLUG}), encoding="utf-8")
    gui.startup()
    server = make_server("127.0.0.1", 0, gui.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/", stack


def watch(page, problems: list, label: str):
    page.on("pageerror", lambda exc: problems.append({"where": label, "kind": "pageerror", "text": str(exc)}))
    page.on("console", lambda msg: msg.type == "error" and problems.append(
        {"where": label, "kind": "console.error", "text": msg.text}))


def capture(url: str, out: Path) -> list[dict]:
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    problems: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in VIEWPORTS:
            size = f"{width}x{height}"
            page = browser.new_page(viewport={"width": width, "height": height})
            watch(page, problems, size)
            page.goto(url)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out / f"{size}_00_home.png"))
            page.evaluate("closeHome()")
            for number, tab in enumerate(TABS, 1):
                page.click(f".tab[data-tab='{tab}']")
                page.wait_for_timeout(1500)
                page.screenshot(path=str(out / f"{size}_{number:02d}_{tab}.png"))
            page.click(".tab[data-tab='review']")
            page.wait_for_timeout(800)
            page.evaluate("document.getElementById('ig-shots') && document.getElementById('ig-shots').scrollIntoView()")
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / f"{size}_10_review_shots.png"))
            if page.evaluate("typeof igOpenViewer === 'function' && (_igData.shots || []).length > 0"):
                page.evaluate("igOpenViewer(_igData.shots[0].shot_id)")
                page.wait_for_timeout(800)
                page.screenshot(path=str(out / f"{size}_11_shot_viewer.png"))
                page.evaluate("igCloseViewer()")
            page.evaluate("openVoices()")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / f"{size}_12_settings_vozes.png"))
            page.close()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        watch(page, problems, "draft")
        page.goto(url)
        page.wait_for_timeout(1200)
        page.evaluate("homeNewProject()")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(out / "1440x900_20_draft_wizard.png"))
        page.click(".tab[data-tab='project']")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / "1440x900_21_draft_project.png"))
        page.evaluate("showHome(); homeShowProjects()")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(out / "1440x900_22_home_projects.png"))
        browser.close()
    (out / "console.json").write_text(json.dumps(problems, indent=2, ensure_ascii=False), encoding="utf-8")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GUI screenshot audit against the fixture project.")
    parser.add_argument("--out", default=str(ROOT / "docs" / "reports" / "gui-3.9.1" / "after"))
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as temp:
        server, url, stack = serve(Path(temp))
        try:
            problems = capture(url, Path(args.out))
        finally:
            server.shutdown()
            for item in reversed(stack):
                item.stop()
    print(f"Screenshots: {args.out} ({len(problems)} console/page error(s), see console.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
