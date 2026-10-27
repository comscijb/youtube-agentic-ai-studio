"""QA result schema shared by every layer and backend (Phase 3.5-B).

Per image::

    {"people_count": 2,
     "issues": [{"type": "missing_marker", "severity": "blocking",
                 "box": [x0, y0, x1, y1] | None,      # normalized 0-1, from the detector only
                 "target": "julian_vance", "note": "...", "source": "deterministic"}],
     "verdict": "pass|edit|regenerate"}

The verdict is decided here by code, never by a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image

QA_VERSION = 2
ISSUE_TYPES = ("missing_marker", "duplicate_person", "extra_person", "unstated_injury", "text", "border",
               "style_break", "wrong_subject", "identity", "other")
ANSWERS = ("yes", "no", "unclear")


def issue(kind: str, *, severity: str = "blocking", box=None, target: str = "", note: str = "",
          source: str = "", marker: str = "") -> dict:
    if kind not in ISSUE_TYPES:
        kind = "other"
    found = {"type": kind, "severity": "advisory" if severity == "advisory" else "blocking",
             "box": [round(float(value), 4) for value in box] if box else None,
             "target": target or "", "note": note[:200], "source": source}
    if marker:
        found["marker"] = marker
    return found


def blocking(result: dict) -> list[dict]:
    return [item for item in result.get("issues", []) if item["severity"] == "blocking"]


def advisory(result: dict) -> list[dict]:
    return [item for item in result.get("issues", []) if item["severity"] == "advisory"]


def area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1]) if box else 0.0


def iou(first, second) -> float:
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = area(first) + area(second) - inter
    return inter / union if union > 0 else 0.0


def notes(result: dict) -> list[str]:
    """Short lines for the GUI shot card and the CLI."""
    lines = [f"{'⛔' if item['severity'] == 'blocking' else '•'} {item['type']}"
             + (f" ({item['target']})" if item["target"] else "") + (f": {item['note']}" if item["note"] else "")
             for item in result.get("issues", [])]
    lines += [f"⚠ {warning}" for warning in result.get("warnings", [])]
    return lines


@dataclass
class Question:
    """One focused question for the VLM: a crop and a one-word answer."""
    qid: str
    kind: str                        # gender | marker | injury | text | person | same_face
    image: Image.Image
    prompt: str
    choices: tuple = ANSWERS
    meta: dict = field(default_factory=dict)


class VLMUnavailable(RuntimeError):
    """The layer-2/3 model cannot be reached or loaded."""


class VLMBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def ask(self, questions: list[Question]) -> dict[str, str]:
        """{qid: one of question.choices}; "unclear" when the answer cannot be parsed."""
        ...


def parse_choice(text: str, choices=ANSWERS) -> str | None:
    """The answer word when it is one of ``choices``.

    qwen3vl through TextGenerate often wraps the word in markup
    (``<div class="answer" data-ans="no">no</div>``, ``# **Answer: no**``,
    ``Assistant: no``): a ``data-ans`` value wins, otherwise thinking blocks,
    tags and labels are dropped and the first of the leading words that is a
    choice is taken.
    """
    import re
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", str(text or "")).lower()
    tagged = re.search(r"data-ans\s*=\s*[\"']?([a-z]+)", cleaned)
    if tagged and tagged.group(1) in choices:
        return tagged.group(1)
    cleaned = re.sub(r"<[^>]*>?", " ", cleaned)
    cleaned = re.sub(r"\b(assistant|answer|final answer)\s*:", " ", cleaned)
    words = re.findall(r"[a-z]+", cleaned)
    for word in words[:3]:
        if word in choices:
            return word
    return None
