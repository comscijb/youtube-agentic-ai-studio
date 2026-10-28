"""Human review of the shots in the GUI: one-click edits, queued and run in one batch (Phase 3.5.1-C).

QA only gives hints (``policy``); the user decides. Each shot card offers:

* **Remover figura** — a box (drawn, or a suggested detector box) → "remove the
  figure, continue the background";
* **Adicionar marcador** — a character and one of its signature markers (e.g.
  Julian's glasses), optional box → region edit with the approved portrait;
* **Remover marcas** — blood, wounds or red marks, with or without a box;
* **Instrução livre** — free text, optional box;
* **Gerar de novo** — a new seed;
* **Ignorar aviso** / **Marcar OK** — dismiss one hint, or all of them.

The first four become synthetic QA issues so ``edits.compose`` writes the
instruction exactly as for automatic fixes. Requests wait in the shot's
``edit_queue`` (shot_list.json) and ``run_queue`` runs every one of them in a
row, Klein loaded once. Every version stays in the history; ``fixes.restore_version``
undoes any edit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from agents import visual_bible as vbible
from video.qa import base, edits, fixes

KINDS = ("remove_figure", "add_marker", "remove_marks", "custom", "regenerate")
LABELS = {"remove_figure": "Remover figura", "add_marker": "Adicionar marcador", "remove_marks": "Remover marcas",
          "custom": "Instrução livre", "regenerate": "Gerar de novo"}


def clean_box(box) -> list[float] | None:
    if box in (None, "", []):
        return None
    try:
        box = [min(max(float(value), 0.0), 1.0) for value in box]
    except (TypeError, ValueError):
        raise ValueError("box must be [x0, y0, x1, y1] between 0 and 1") from None
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("box must be [x0, y0, x1, y1] between 0 and 1")
    return [round(value, 4) for value in box]


def make_request(kind: str, vb: dict, *, box=None, character: str = "", marker: str = "", text: str = "") -> dict:
    """A validated queue entry; ValueError with a message for the user otherwise."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    box = clean_box(box)
    request = {"kind": kind, "label": LABELS[kind]}
    if box:
        request["box"] = box
    if kind == "remove_figure" and not box:
        raise ValueError("Draw a box around the figure (or pick a suggested one)")
    if kind == "add_marker":
        markers = vbible.signature_markers(vb, character) if vbible.character(vb, character) else []
        if not markers:
            raise ValueError("Pick a character with signature markers")
        if marker not in markers:
            raise ValueError(f"Pick one of {character}'s markers: {', '.join(markers)}")
        request.update(character=character, marker=marker)
    if kind == "custom":
        text = str(text or "").strip()
        if not text:
            raise ValueError("Type the edit instruction")
        request["text"] = text[:500]
    return request


def plan_for(request: dict, vb: dict, output_dir) -> dict:
    """The ``edits.compose`` plan of one (non-regenerate) request."""
    box = request.get("box")
    if request["kind"] == "custom":
        return edits.compose([], vb, output_dir, custom=request["text"], custom_box=box)
    if request["kind"] == "remove_figure":
        found = base.issue("extra_person", box=box, source="human")
    elif request["kind"] == "add_marker":
        found = base.issue("missing_marker", box=box, target=request["character"], marker=request["marker"],
                           source="human")
    else:
        found = base.issue("unstated_injury", box=box, source="human")
    plan = edits.compose([found], vb, output_dir)
    plan["kinds"] = [request["kind"]]
    return plan


def enqueue(shot: dict, request: dict) -> list[dict]:
    shot.setdefault("edit_queue", []).append(request)
    return shot["edit_queue"]


def dequeue(shot: dict, index: int) -> list[dict]:
    queue = shot.get("edit_queue") or []
    if not 0 <= index < len(queue):
        raise IndexError("no such queued edit")
    queue.pop(index)
    if not queue:
        shot.pop("edit_queue", None)
    return queue


def queued(shot_list: dict) -> list[dict]:
    return [shot for shot in shot_list.get("shots", []) if shot.get("edit_queue")]


def mark(shot: dict, action: str, issue_index: int | None = None) -> dict:
    """"ignore" one hint (by index) or "ok" — the whole shot is reviewed, every hint dismissed."""
    qa = fixes.state(shot)
    if action == "ignore":
        if issue_index is None or not 0 <= issue_index < len(qa["issues"]):
            raise IndexError("no such hint")
        qa["issues"][issue_index]["ignored"] = True
        if all(item.get("ignored") or not item.get("hint") for item in qa["issues"]):
            qa["hints_dismissed"] = True
    elif action == "ok":
        qa.update(hints_dismissed=True, reviewed=True)
    else:
        raise ValueError("action must be ok or ignore")
    return qa


def run_queue(generator, output, vb: dict, shot_list: dict, *, job_factory: Callable[[dict], Callable],
              save: Callable[[], None], progress: Callable[[str], None] = print) -> dict:
    """Every queued request of every shot, in order, Klein loaded throughout. A failed edit keeps
    the original image (recorded on the shot); the queue entry is consumed either way."""
    output = Path(output)
    summary = {"edits": 0, "regenerations": 0, "failed": [], "shots": []}
    for shot in queued(shot_list):
        image = output / shot["image_path"]
        factory = job_factory(shot)
        while shot.get("edit_queue"):
            request = shot["edit_queue"][0]
            if request["kind"] == "regenerate":
                action = fixes.apply(generator, output, shot, vb, image_path=image, job_factory=factory,
                                     force="regenerate", progress=progress)
            else:
                action = fixes.edit_with_plan(generator, output, shot, plan_for(request, vb, output),
                                              image_path=image, base_job=factory(0), origin="human",
                                              progress=progress)
            action["request"] = request["label"]
            dequeue(shot, 0)
            if action["action"] == "edit":
                summary["edits"] += 1
            elif action["action"] == "regenerate":
                summary["regenerations"] += 1
            else:
                summary["failed"].append(shot["shot_id"])
            save()
        fixes.state(shot)["reviewed"] = False                 # the new image needs a look
        summary["shots"].append(shot["shot_id"])
    return summary
