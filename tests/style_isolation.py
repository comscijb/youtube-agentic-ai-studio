"""Point config.STYLE_REFS_DIR and PROJECTS_DIR at empty temp folders for a test module.

Approved style anchors live in a channel-level folder and episodes in
projects/; tests must not pick up whatever the user approved or created
there. Use from a test module:

    from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
"""
import os
import tempfile
from unittest.mock import patch

import config

_state = {}


def setUpModule():
    _state["temp"] = tempfile.TemporaryDirectory()
    _state["patches"] = [
        patch.object(config, "STYLE_REFS_DIR", _state["temp"].name),
        patch.object(config, "PROJECTS_DIR", os.path.join(_state["temp"].name, "projects")),
        patch.object(config, "OUTPUT_DIR", config.OUTPUT_DIR),     # restored if a script activates one
        patch.object(config, "IMAGES_DIR", config.IMAGES_DIR),
        patch.object(config, "IMAGEGEN_VISUAL_QA", False),       # QA tests enable it with a mocked Gemini
    ]
    for item in _state["patches"]:
        item.start()


def tearDownModule():
    for item in reversed(_state["patches"]):
        item.stop()
    _state["temp"].cleanup()
