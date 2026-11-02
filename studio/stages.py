"""The production stages, their dependencies and the lane each one uses (Phase 3.8-B).

One graph drives the status computation (``studio.projects``), the GUI stage list
(``/api/stages``) and the runner (``run``), which starts every stage whose
dependencies are done, at most ``LANE_LIMITS`` jobs per lane: Narration (Edge TTS)
and the visual bible (Gemini) run together, reference candidates start as soon as
the visual bible exists, music mixing (CPU) runs next to the shots (GPU), and only
one job ever uses the GPU.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

LANES = ("network", "gpu", "cpu", "manual")
LANE_LIMITS = {"network": 2, "gpu": 1, "cpu": 1, "manual": 1}


@dataclass(frozen=True)
class Stage:
    id: str
    label: str
    depends_on: tuple = ()
    lane: str = "cpu"
    optional: bool = False
    produces: tuple = ()
    steps: tuple = ()                 # GUI pipeline steps that (re)build it; () = done elsewhere (approval)
    help: str = ""


STAGES = (
    Stage("story_brief", "Premissa / Story Brief", (), "network", False,
          ("story_brief.json", "research.json"), ("research",)),
    Stage("script", "Roteiro (outline + cenas + revisão)", ("story_brief",), "network", False,
          ("character_bible.json", "story_outline.json", "script.json", "narrative_review.json"), ("script",)),
    Stage("narration", "Narração", ("script",), "network", False,
          ("narration.mp3", "narration.srt", "scene_timeline.json"), ("narration",)),
    Stage("music", "Música", ("narration",), "cpu", True, ("narration_mixed.mp3",), ("music",)),
    Stage("visual_bible", "Visual bible", ("script",), "network", False, ("visual_bible.json",), ("visual_bible",)),
    Stage("references", "Referências (aprovação)", ("visual_bible",), "gpu", False,
          ("character_refs/", "location_refs/"), (), "approve one portrait per character in Review & Edit"),
    Stage("shots", "Planos (ComfyUI)", ("narration", "visual_bible", "references"), "gpu", False,
          ("shot_list.json", "images/shots/"), ("shots",), "shot list planning, rendering and QA"),
    Stage("shot_review", "Revisão dos planos (visualizador)", ("shots",), "manual", True, (), (),
          "mark each shot OK in the shot viewer"),
    Stage("video", "Vídeo", ("narration", "shots"), "cpu", False, ("final_video.mp4",), ("video",)),
    Stage("shorts", "Shorts", ("narration", "shots"), "cpu", True, ("shorts/",), ("shorts",)),
    Stage("publish", "Publicação", ("video",), "network", True, (), (), "approval gate in Review & Edit"),
)
STAGE_IDS = tuple(stage.id for stage in STAGES)
BY_ID = {stage.id: stage for stage in STAGES}
# Legacy GUI step names → the step that replaced them.
STEP_ALIASES = {"images": "shots"}


def get(stage_id: str) -> Stage:
    return BY_ID[stage_id]


def stage_for_step(step: str) -> str | None:
    step = STEP_ALIASES.get(step, step)
    return next((stage.id for stage in STAGES if step in stage.steps), None)


def downstream(stage_id: str) -> list[str]:
    """Every stage that depends on ``stage_id``, directly or not, in graph order."""
    found = {stage_id}
    for stage in STAGES:
        if set(stage.depends_on) & found:
            found.add(stage.id)
    return [stage for stage in STAGE_IDS if stage in found and stage != stage_id]


def as_json() -> list[dict]:
    """``/api/stages``: the graph in display order."""
    return [{"id": stage.id, "label": stage.label, "depends_on": list(stage.depends_on), "lane": stage.lane,
             "optional": stage.optional, "produces": list(stage.produces), "steps": list(stage.steps),
             "runnable": bool(stage.steps), "help": stage.help, "position": number}
            for number, stage in enumerate(STAGES, 1)]


class Blocked(Exception):
    """A stage cannot go on until the user acts (e.g. approve references); its dependents are skipped."""


@dataclass
class RunResult:
    status: dict = field(default_factory=dict)      # stage id -> done | blocked | error | skipped | stopped
    errors: dict = field(default_factory=dict)      # stage id -> exception

    def first_error(self) -> BaseException | None:
        return next((self.errors[stage] for stage in STAGE_IDS
                     if stage in self.errors and self.status.get(stage) == "error"), None)


def run(selected, jobs: dict[str, Callable[[], object]], *, stop: threading.Event | None = None,
        on_event: Callable[[str, str, str], None] | None = None, limits: dict | None = None) -> RunResult:
    """Run the ``selected`` stages (ids), each as soon as its selected dependencies are done.

    ``jobs[stage]()`` does the work in its own thread; a stage whose dependency failed, was blocked
    or skipped is skipped; ``stop`` starts nothing new (running jobs finish). ``on_event(stage,
    status, lane)`` reports running / done / blocked / error / skipped.
    """
    selected = [stage for stage in STAGE_IDS if stage in set(selected)]
    limits = {**LANE_LIMITS, **(limits or {})}
    result = RunResult()
    changed = threading.Condition()
    running: dict[str, threading.Thread] = {}
    pending = list(selected)

    def report(stage: str, status: str) -> None:
        if on_event:
            on_event(stage, status, BY_ID[stage].lane)

    def work(stage: str) -> None:
        try:
            jobs[stage]()
            status = "done"
        except Blocked as exc:
            status = "blocked"
            result.errors[stage] = exc
        except BaseException as exc:                 # noqa: BLE001 — reported, dependents skipped
            status = "stopped" if isinstance(exc, InterruptedError) else "error"
            result.errors[stage] = exc
        with changed:
            result.status[stage] = status
            running.pop(stage, None)
            changed.notify_all()
        report(stage, status)

    with changed:
        while pending or running:
            for stage in list(pending):
                deps = [dep for dep in BY_ID[stage].depends_on if dep in selected]
                if any(result.status.get(dep) not in (None, "done") for dep in deps) or \
                        (stop is not None and stop.is_set()):
                    pending.remove(stage)
                    result.status[stage] = "skipped" if not (stop is not None and stop.is_set()) else "stopped"
                    report(stage, result.status[stage])
                    continue
                if any(result.status.get(dep) != "done" for dep in deps):
                    continue
                lane = BY_ID[stage].lane
                if sum(1 for other in running if BY_ID[other].lane == lane) >= limits.get(lane, 1):
                    continue
                pending.remove(stage)
                thread = threading.Thread(target=work, args=(stage,), name=f"stage-{stage}", daemon=True)
                running[stage] = thread
                report(stage, "running")
                thread.start()
            if pending or running:
                changed.wait(timeout=0.5)
    return result
