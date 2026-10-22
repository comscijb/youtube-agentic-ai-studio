"""Point config.STYLE_REFS_DIR at an empty temp folder for a test module.

Approved style anchors live in a channel-level folder; tests must not pick up
whatever the user approved there. Use from a test module:

    from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
"""
import tempfile
from unittest.mock import patch

import config

_state = {}


def setUpModule():
    _state["temp"] = tempfile.TemporaryDirectory()
    _state["patch"] = patch.object(config, "STYLE_REFS_DIR", _state["temp"].name)
    _state["patch"].start()


def tearDownModule():
    _state["patch"].stop()
    _state["temp"].cleanup()
