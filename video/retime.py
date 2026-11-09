"""Re-timing after a new narration, without regenerating anything (Phase 3.9.4-G.5).

A new narration changes every word time. Each shot keeps its narration span (the words of its
``narration_excerpt``, stored as ``start_word``/``end_word``) and gets new start and end times from the new
timings. Images, history, edits and approvals are untouched. Shorts keep the same sentences. Only the
Video and Shorts stages go stale: the shot list's source hash is brought up to date, so Planos stays done.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from agents.narrative import atomic_write_json, load_json
from video.captions import _norm

PREVIOUS_WORDS = ".narration_words.prev.json"


class RetimeError(RuntimeError):
    """The shots cannot be mapped onto the narration (the script changed): re-plan them."""


def _tokens(text: str) -> list[str]:
    return [key for key in (_norm(token) for token in str(text).split()) if key]


def word_spans(shots: list[dict], words: list[dict]) -> list[tuple[int, int]]:
    """(first, last) word index of each shot of one scene, in order, from its narration excerpt."""
    keys = [_norm(word.get("text", "")) for word in words]
    spans, cursor = [], 0
    for shot in shots:
        tokens = _tokens(shot.get("narration_excerpt", ""))
        if not tokens:
            raise RetimeError(f"{shot.get('shot_id')} has no narration excerpt")
        window = keys[cursor:cursor + len(tokens) + 12]
        matcher = SequenceMatcher(None, window, tokens, autojunk=False)
        blocks = [block for block in matcher.get_matching_blocks() if block.size]
        if sum(block.size for block in blocks) < 0.6 * len(tokens):
            raise RetimeError(f"{shot.get('shot_id')}: its excerpt is not in the narration any more")
        first = cursor + blocks[0].a
        last = cursor + blocks[-1].a + blocks[-1].size - 1
        spans.append((first, last))
        cursor = last + 1
    return spans


def retime_shots(shot_list: dict, words: list[dict], durations: list[float], section_ids: list) -> dict:
    """New start/end for every shot from the new word timings; returns the same object. A scene's first shot
    starts with the scene and its last one ends with it, so the pauses stay covered."""
    shots = shot_list.get("shots", [])
    scene_start, cursor = {}, 0.0
    for section_id, duration in zip(section_ids, durations):
        scene_start[section_id] = (cursor, cursor + duration)
        cursor += duration
    by_section: dict = {}
    for shot in shots:
        by_section.setdefault(shot.get("section_id"), []).append(shot)
    updates = []
    for section_id, items in by_section.items():
        if section_id not in scene_start:
            raise RetimeError(f"scene {section_id} is not in the narration")
        scene_words = [(index, word) for index, word in enumerate(words) if word.get("scene_id") == section_id]
        spans = word_spans(items, [word for _, word in scene_words])
        begin, finish = scene_start[section_id]
        for position, (shot, (first, last)) in enumerate(zip(items, spans)):
            start = begin if position == 0 else float(scene_words[first][1]["start"])
            next_start = float(scene_words[spans[position + 1][0]][1]["start"]) if position + 1 < len(items) else finish
            updates.append((shot, round(start, 3), round(next_start, 3), scene_words[first][0], scene_words[last][0]))
    for shot, start, end, first, last in updates:     # nothing changes before every scene mapped
        shot.update(start=start, end=end, start_word=first, end_word=last)
    return shot_list


def resnap_shorts(plan: dict, previous: list[dict], words: list[dict]) -> dict:
    """Each Short keeps the same words: its old times name them in the old timings, the new timings say when
    they are now. A plan whose words changed count is left to be rebuilt."""
    if len(previous) != len(words):
        raise RetimeError("the narration's words changed: rebuild the Shorts plan")
    for segment in plan.get("segments", []):
        start, end = float(segment["start"]), float(segment["end"])
        inside = [index for index, word in enumerate(previous)
                  if float(word["start"]) >= start - 0.05 and float(word["end"]) <= end + 0.05]
        if not inside:
            continue
        segment.update(start=round(float(words[inside[0]]["start"]), 3), end=round(float(words[inside[-1]]["end"]), 3))
    return plan


def retime_project(output_dir, progress=print) -> dict:
    """After a narration: shots and Shorts follow the new timings. Returns what changed."""
    from agents import shot_list as shots_module
    from agents import visual_bible as vbible
    from video import shorts
    output = Path(output_dir)
    words = (load_json(output / shorts.WORDS_FILE) or {}).get("words") or []
    timeline = load_json(output / "scene_timeline.json") or {}
    shot_list = load_json(output / shots_module.FILE_NAME)
    report = {"shots": 0, "shorts": 0}
    if shot_list and shot_list.get("shots") and words and timeline.get("scenes"):
        scenes = timeline["scenes"]
        retime_shots(shot_list, words, [float(scene["duration"]) for scene in scenes], [scene.get("scene_id", scene.get("id")) for scene in scenes])
        script, vb = load_json(output / "script.json"), load_json(output / vbible.FILE_NAME)
        fresh = shots_module.expected_source_hash(script, vb, output) if script and vb else None
        if fresh:
            shot_list["source_hash"] = fresh
        atomic_write_json(output / shots_module.FILE_NAME, shot_list)
        report["shots"] = len(shot_list["shots"])
        progress(f"   → {report['shots']} planos re-sincronizados com a nova narração (nenhuma imagem refeita)")
    previous = (load_json(output / PREVIOUS_WORDS) or {}).get("words") or []
    plan = shorts.load(output)
    if plan and plan.get("segments") and previous:
        resnap_shorts(plan, previous, words)
        plan["retimed"] = True
        atomic_write_json(output / shorts.FOLDER / shorts.PLAN_FILE, plan)
        report["shorts"] = len(plan["segments"])
        progress(f"   → {report['shorts']} Short(s) re-sincronizado(s) nas mesmas frases; renderize-os de novo")
    return report
