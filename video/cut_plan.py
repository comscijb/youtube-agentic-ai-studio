"""The cut plan (Phase 4.2): the shot schedule turned into what the viewer sees, before rendering.

``expand`` replaces each shot with 2-3 derived views (``video.rhythm``) cut on word boundaries, and later sections add
the key lines (B), the beat grades and transitions (C). ``cut_plan.json`` keeps a JSON copy (no image arrays) for
the GUI's rhythm report and the video attention map (F).
"""
from __future__ import annotations

import os
from pathlib import Path

from agents.narrative import atomic_write_json, load_json
from video import rhythm

FILE_NAME = "cut_plan.json"
VIEWS_CACHE = "images/shots/views.json"


def words(output_dir) -> list[dict]:
    return (load_json(Path(output_dir) / "narration_words.json") or {}).get("words", [])


def attention_events(output_dir, word_list: list[dict]) -> set[float]:
    """Word start times that open a sentence carrying a 4.1 attention event (question, reveal, dialogue turn)."""
    from agents import attention_map
    found = attention_map.load(output_dir) or {}
    wanted = {"question", "reveal", "dialogue_turn", "open_loop", "escalation", "decision"}
    starts = [float(item["start"]) for item in found.get("sentences", [])
              if {tag["tag"].split(":")[0] for tag in item.get("tags", [])} & wanted]
    if not starts or not word_list:
        return set()
    total_est = float(found.get("duration") or 1)
    total_real = float(word_list[-1]["end"]) or total_est
    scale = total_real / total_est                       # the map's estimate onto the real narration clock
    times = [float(word["start"]) for word in word_list]
    return {min(times, key=lambda time: abs(time - start * scale)) for start in starts}


def _detect(output_dir, shot_id: str, image_path: str, cache: dict) -> list[dict]:
    key = f"{shot_id}:{int(os.path.getmtime(image_path))}"
    if key in cache:
        return cache[key]
    persons = []
    try:
        from video.qa import fixes
        detector = fixes._detector()
        if detector is not None:
            persons = [{"box": item.get("box"), **({"face": {"box": item["face"]["box"]}} if item.get("face") else {})}
                       for item in detector.detect(image_path).get("persons", [])]
    except Exception:                                    # noqa: BLE001 — no detector: crops by edge density
        persons = []
    cache[key] = persons
    return persons


def expand(output_dir, sections: list[dict], durations: list[float], schedule: dict, *, upscale_probe: dict | None = None,
           detect=None) -> dict:
    """{section id: [view items]} in the creator's shot-item format, plus the report rows."""
    from PIL import Image
    output_dir = str(output_dir)
    word_list = words(output_dir)
    events = attention_events(output_dir, word_list)
    candidates = rhythm.boundaries(word_list, events)
    cache_path = Path(output_dir) / VIEWS_CACHE
    cache = load_json(cache_path) or {}
    probe = upscale_probe if upscale_probe is not None else _probe()
    expanded, rows, cursor = {}, [], 0.0
    last_id = sections[-1]["id"] if sections else None
    for section, duration in zip(sections, durations):
        items = schedule.get(section["id"]) or []
        beat = rhythm.beat_for(section.get("section_type"))
        views_out = []
        for position, item in enumerate(items):
            final = section["id"] == last_id and position == len(items) - 1
            persons = (detect or (lambda shot_id, path: _detect(output_dir, shot_id, path, cache)))(item["shot_id"], item["image"])
            upscaled = None
            if probe.get("available"):
                from video import upscale
                upscaled = upscale.ensure(output_dir, item["shot_id"], item["image"], model=probe.get("chosen"))
            with Image.open(item["image"]) as opened:
                views = rhythm.views_for(opened.convert("RGB"), persons, upscaled=bool(upscaled))
            local = [(time - cursor, preferred) for time, preferred in candidates if cursor <= time <= cursor + duration]
            for number, view in enumerate(rhythm.split(item["start"], item["end"], views, beat, local, final=final)):
                crop = view["box"] if view["kind"] != "full" else None
                views_out.append({**item, "shot_id": f"{item['shot_id']}#{number}" if number else item["shot_id"],
                                  "source_shot": item["shot_id"], "view": view["kind"], "crop": crop,
                                  "source": str(upscaled) if crop and upscaled else item["image"],
                                  "start": view["start"], "end": view["end"], "motion_variant": view.get("motion_variant", 0),
                                  "shot_type": {"detail": "close_up", "medium": "medium"}.get(view["kind"], item.get("shot_type", "medium")),
                                  "transition": item.get("transition", "cut") if number == 0 else ("cut" if beat in ("tension", "cold_open") else "crossfade"),
                                  "beat": beat, "final": final})
        if views_out:
            expanded[section["id"]] = views_out
            rows.append({"id": section["id"], "title": section.get("title", ""), "beat": beat, "section_type": section.get("section_type"),
                         "views": [{key: view[key] for key in ("start", "end", "view")} for view in views_out],
                         "final": section["id"] == last_id})
        cursor += duration
    atomic_write_json(cache_path, cache)
    extras = rhythm.extra_requests(rows)
    return {"schedule": expanded, "rows": rows, "report": rhythm.report(rows), "upscale": probe,
            "extra_images": extras, "gpu_budget": rhythm.gpu_budget(extras)}


def faces_at(plan: dict, sections: list[dict], durations: list[float], t: float, output_dir) -> list:
    """Face boxes (frame-normalised) of the view on screen at global time ``t``, from the detection cache."""
    cache = load_json(Path(output_dir) / VIEWS_CACHE) or {}
    cursor = 0.0
    for section, duration in zip(sections, durations):
        if cursor <= t < cursor + duration:
            for item in plan.get("schedule", {}).get(section["id"], []):
                if item["start"] <= t - cursor < item["end"]:
                    persons = next((value for key, value in cache.items() if key.startswith(f"{item.get('source_shot', item['shot_id'])}:")), [])
                    faces = [person["face"]["box"] for person in persons if person.get("face")]
                    crop = item.get("crop")
                    if not crop:
                        return faces
                    width, height = crop[2] - crop[0], crop[3] - crop[1]
                    return [[(face[0] - crop[0]) / width, (face[1] - crop[1]) / height, (face[2] - crop[0]) / width,
                             (face[3] - crop[1]) / height] for face in faces]
        cursor += duration
    return []


def key_lines(output_dir, plan: dict, sections: list[dict], durations: list[float], *, captions_on: bool) -> list[dict]:
    """Phase 4.2-B: the key lines with their placement (upper third unless a face is there)."""
    from agents import attention_map
    from video import key_lines as lines_module
    if not lines_module.enabled():
        return []
    found = lines_module.choose(attention_map.load(output_dir) or {}, words(output_dir), duration=sum(durations))
    for line in found:
        line.update(lines_module.placement(faces_at(plan, sections, durations, line["start"], output_dir), captions_on=captions_on))
    return found


def _probe() -> dict:
    import config
    if not getattr(config, "UPSCALE_CROPS", True):
        return {"available": False, "reason": "UPSCALE_CROPS desligado"}
    from video import upscale
    return upscale.probe()


def save(output_dir, plan: dict) -> dict:
    record = {key: plan[key] for key in plan if key != "schedule"}
    record["sections"] = {str(section_id): [{key: value for key, value in item.items() if key not in ("arr", "motion")}
                                           for item in items] for section_id, items in plan.get("schedule", {}).items()}
    atomic_write_json(Path(output_dir) / FILE_NAME, record)
    return record


def load(output_dir) -> dict | None:
    return load_json(Path(output_dir) / FILE_NAME)
