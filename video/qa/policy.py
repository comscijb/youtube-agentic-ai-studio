"""Which QA issues may trigger automatic work (Phase 3.5.1-B).

``IMAGEGEN_QA_MODE``:

* ``advisory`` (default) — QA runs and stores every issue, but only the
  reliable deterministic checks act on their own: borders/comic panels, blank
  or corrupt images, and text read by OCR (when Tesseract is installed). Every
  other issue — anything a VLM said, people counts, duplicates — becomes a
  *hint* for the human review in the GUI;
* ``auto`` — an issue type also acts on its own when its measured accuracy on
  the confirmed fixtures (latest ``data/qa_probe_report.json``) is at least
  ``AUTO_MIN_ACCURACY``; the others stay hints;
* ``off`` — no QA at all.

``duplicate_person`` never acts on its own: in the illustration style every
face shares the same drawing, so "same face twice" is a low-confidence hint.

A demoted issue keeps ``raw_severity: "blocking"`` and gets ``hint: true``, so
stored results can be re-evaluated when the mode or the measurements change.
"""
from __future__ import annotations

import json
from pathlib import Path

import config
from video.qa import base

MODES = ("advisory", "auto", "off")
AUTO_MIN_ACCURACY = 0.85
REPORT = Path(__file__).resolve().parent.parent.parent / "data" / "qa_probe_report.json"
RELIABLE = {"border", "blank", "corrupt"}
NEVER_AUTO = {"duplicate_person"}
METRIC = {"missing_marker": "missing_glasses", "unstated_injury": "unstated_mark", "extra_person": "extra_person",
          "text": "text"}


def mode(value: str | None = None) -> str:
    chosen = str(value or getattr(config, "IMAGEGEN_QA_MODE", "advisory")).strip().lower()
    return chosen if chosen in MODES else "advisory"


def measured_accuracy(report_path=REPORT, backend: str = "local") -> dict[str, float]:
    """Per-metric accuracy of the latest probe run for ``backend`` ("local" or "gemini"); {} if none."""
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    local_model = getattr(config, "IMAGEGEN_QA_VLM_MODEL", "")
    for entry in report.get("models", []):
        is_gemini = entry.get("backend") == "gemini"
        if (backend == "gemini") != is_gemini:
            continue
        if backend == "local" and entry.get("mode") != "focused" and report.get("mode") != "focused":
            continue
        if backend == "local" and local_model and entry.get("model") not in (local_model, None):
            continue
        return {key: float(value) for key, value in ((entry.get("metrics") or {}).get("accuracy") or {}).items()
                if isinstance(value, (int, float))}
    return {}


def reliable(item: dict) -> bool:
    return item["type"] in RELIABLE or (item["type"] == "text" and item.get("source") == "ocr")


def automatic(item: dict, chosen: str, accuracy: dict | None = None) -> bool:
    """May this (blocking) issue trigger an edit or a regeneration without a human?"""
    if reliable(item):
        return True
    if chosen != "auto" or item["type"] in NEVER_AUTO:
        return False
    metric = METRIC.get(item["type"])
    return bool(metric) and (accuracy or {}).get(metric, 0.0) >= AUTO_MIN_ACCURACY


def apply(issues: list[dict], chosen: str | None = None, accuracy: dict | None = None) -> list[dict]:
    """The issues with every non-automatic blocking one turned into an advisory hint (idempotent:
    a stored hint is re-promoted when the mode or the measurements now allow it)."""
    chosen = mode(chosen)
    if accuracy is None and chosen == "auto":
        accuracy = measured_accuracy()
    result = []
    for item in issues:
        item = dict(item)
        raw = item.get("raw_severity") or item["severity"]
        if raw == "blocking" and not automatic(item, chosen, accuracy):
            item.update(severity="advisory", raw_severity="blocking", hint=True)
            if item["type"] in NEVER_AUTO and "low confidence" not in item.get("note", ""):
                item["note"] = (item.get("note", "") + " (low confidence in this style)").strip()[:200]
        elif raw == "blocking":
            item["severity"] = "blocking"
            item.pop("hint", None)
        result.append(item)
    return result


def hints(result: dict) -> list[dict]:
    """Issues for the human review: demoted hints and plain advisories (warnings excluded)."""
    return [item for item in result.get("issues", []) if item["severity"] == "advisory"]


class Rules:
    """The current mode and measurements, for re-evaluating stored results (``fixes.reapply_policy``)."""

    def __init__(self, chosen: str | None = None, accuracy: dict | None = None):
        self.mode = mode(chosen)
        self.accuracy = accuracy if accuracy is not None else (measured_accuracy() if self.mode == "auto" else {})

    def policy(self, issues: list[dict]) -> list[dict]:
        return apply(issues, self.mode, self.accuracy)


def summary_line(chosen: str | None = None) -> str:
    chosen = mode(chosen)
    if chosen == "auto":
        accuracy = measured_accuracy()
        allowed = sorted(kind for kind, metric in METRIC.items()
                         if kind not in NEVER_AUTO and accuracy.get(metric, 0.0) >= AUTO_MIN_ACCURACY)
        return "QA mode auto: automatic fixes for " + ", ".join(sorted(RELIABLE) + ["OCR text"] + allowed)
    if chosen == "off":
        return "QA mode off: no checks"
    return "QA mode advisory: only borders, blank/corrupt images and OCR text are fixed automatically"

