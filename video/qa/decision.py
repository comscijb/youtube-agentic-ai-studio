"""What to do with a shot after QA: pass, edit a region, or regenerate (Phase 3.5-B).

Decided by code from the issues, never by a model:

* **pass** — no blocking issue (advisory issues never trigger work; the GUI shows them);
* **edit** — every blocking issue is localized and small (a detector box under
  ``EDIT_MAX_AREA`` of the frame) or an instruction-only fix (``unstated_injury``,
  ``text``: no reliable box, the region-edit template works without one), and
  there are at most ``EDIT_MAX_ISSUES`` of them: missing glasses, a mark on a
  hand, an extra background figure, lettering, a duplicated person who is not
  the main subject;
* **regenerate** — a style break, border/panel, blank or corrupt image, wrong subject, a blocking issue
  whose box covers the main subject (>= ``EDIT_MAX_AREA``), any other issue
  without a box, three or more blocking issues, or ``IMAGEGEN_QA_MAX_EDITS``
  edits already failed.

Which issues may be blocking at all is decided first by ``policy`` (``IMAGEGEN_QA_MODE``).
"""
from __future__ import annotations

import config
from video.qa import base

EDIT_MAX_AREA = 0.35
EDIT_MAX_ISSUES = 2
REGENERATE_TYPES = {"style_break", "border", "blank", "corrupt", "wrong_subject"}
INSTRUCTION_EDITABLE = {"unstated_injury", "text"}


def max_edits() -> int:
    return int(getattr(config, "IMAGEGEN_QA_MAX_EDITS", 2))


def verdict(result: dict, edits_done: int = 0) -> tuple[str, str]:
    """(pass|edit|regenerate, reason)."""
    found = base.blocking(result)
    if not found:
        return "pass", "no blocking issue"
    if edits_done >= max_edits():
        return "regenerate", f"{edits_done} edit(s) already tried"
    kinds = sorted({item["type"] for item in found} & REGENERATE_TYPES)
    if kinds:
        return "regenerate", f"{', '.join(kinds)} cannot be fixed by a region edit"
    if len(found) > EDIT_MAX_ISSUES:
        return "regenerate", f"{len(found)} blocking issues (an edit fixes at most {EDIT_MAX_ISSUES})"
    for item in found:
        if item["box"] and base.area(item["box"]) >= EDIT_MAX_AREA:
            return "regenerate", f"{item['type']} covers {base.area(item['box']):.0%} of the frame (the main subject)"
        if not item["box"] and item["type"] not in INSTRUCTION_EDITABLE:
            return "regenerate", f"{item['type']} has no detector box to edit"
    return "edit", ", ".join(item["type"] for item in found)


def finalize(result: dict, edits_done: int = 0) -> dict:
    """Add status, verdict and its reason to a QA result."""
    decided, reason = verdict(result, edits_done)
    status = result.get("status")
    if status != "skipped":
        status = "pass" if decided == "pass" else "fail"
    return {**result, "status": status, "verdict": decided, "verdict_reason": reason}
