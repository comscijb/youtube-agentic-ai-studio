"""Test helper: run a QA test in ``auto`` mode with every measured accuracy at 100 %.

Pipeline tests check the automatic edit/regenerate mechanics; since Phase 3.5.1 the
default mode is ``advisory``, where VLM signals never act on their own. Their fake
frames are solid colours, so the blank-frame check (tested on its own) is off too.
"""
from unittest.mock import patch

import config

FULL = {"missing_glasses": 1.0, "unstated_mark": 1.0, "extra_person": 1.0, "text": 1.0, "duplicate_person": 1.0,
        "people_count": 1.0}


def auto_mode(cls):
    """Class decorator; the patches start before ``setUp`` (where QA objects are often built)."""
    original = cls.setUp

    def setUp(self):
        for item in (patch.object(config, "IMAGEGEN_QA_MODE", "auto"),
                     patch("video.qa.policy.measured_accuracy", new=lambda *args, **kwargs: dict(FULL)),
                     patch("video.qa.deterministic.blank_issues", new=lambda image: [])):
            item.start()
            self.addCleanup(item.stop)
        original(self)

    cls.setUp = setUp
    return cls
