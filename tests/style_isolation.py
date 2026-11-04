"""Point config.STYLE_REFS_DIR, PROJECTS_DIR and FILM_ASSETS_DIR at empty temp folders for a test module.

Approved style anchors live in a channel-level folder and episodes in
projects/; tests must not pick up whatever the user approved or created
there. Use from a test module:

    from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
"""
import os
import tempfile
from unittest.mock import patch

import config
from video import motion

_state = {}


def setUpModule():
    _state["temp"] = tempfile.TemporaryDirectory()
    _state["patches"] = [
        patch.object(config, "STYLE_REFS_DIR", _state["temp"].name),
        patch.object(config, "PROJECTS_DIR", os.path.join(_state["temp"].name, "projects")),
        patch.object(config, "OUTPUT_DIR", config.OUTPUT_DIR),     # restored if a script activates one
        patch.object(config, "IMAGES_DIR", config.IMAGES_DIR),
        patch.object(config, "IMAGEGEN_VISUAL_QA", False),       # QA tests enable it with a mocked Gemini
        patch.object(config, "FILM_ASSETS_DIR", os.path.join(_state["temp"].name, "film"), create=True),
        patch.object(config, "AMBIENT_ASSETS_DIR", os.path.join(_state["temp"].name, "ambient"), create=True),
        # Phase 3.9: title scoring and the variation guard read the ledger and the episode profiles.
        patch.object(config, "PUBLICATIONS_FILE", os.path.join(_state["temp"].name, "publications.json")),
        # No live Gemini in tests: an unmocked call fails at once ("no credentials") instead of reaching
        # the API (found in 3.9: the GUI pipeline tests reached the thumbnail concepts call).
        patch.object(config, "GEMINI_API_KEY", ""),
        patch.object(config, "EPISODE_PROFILES_FILE", os.path.join(_state["temp"].name, "profiles.json"), create=True),
        patch.object(config, "MEASURE_NEW_VOICE", False, create=True),      # no TTS before a mocked script
        patch.object(config, "GUI_STATE_FILE", os.path.join(_state["temp"].name, "gui_state.json"), create=True),
        # No real depth model either: with transformers installed it would download and run on the fixtures.
        patch.object(motion, "estimate", side_effect=motion.DepthUnavailable("no depth model in tests")),
    ]
    for item in _state["patches"]:
        item.start()


def tearDownModule():
    for item in reversed(_state["patches"]):
        item.stop()
    _state["temp"].cleanup()
