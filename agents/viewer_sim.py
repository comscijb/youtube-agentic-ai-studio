"""The skeptical-viewer simulation (Phase 4.1-D): the screenplay's gate before any image is drawn.

Three personas (``VIEWER_PERSONAS``, configurable) read the screenplay in 15-second blocks with timestamps and
return, per block, ``{interest 1-10, would_leave, reason, quote}`` plus the first second they would leave. One call
for the three (``VIEWER_SIM_MODE = "single"``) or one short call each (``"per_persona"``).

The gate: no majority leaving in the first 30 s; median block interest ≥ 7; never two consecutive blocks with a
median below 5; the reversal's block ≥ 8. ``viewer_sim.json`` keeps the answers; the attention map draws the curves
as a *simulation*, never as a forecast: ``correlation`` compares them with the real retention once it exists.
"""
from __future__ import annotations

import math
import re
import statistics
from pathlib import Path

import config
from agents import attention
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash

FILE_NAME = "viewer_sim.json"
BLOCK_SECONDS = 15.0
VERSION = 1
DEFAULT_PERSONAS = [
    {"id": "phone_night", "description": "25-34, scrolling on a phone at night, likes dark stories, impatient, will swipe "
                                         "the moment nothing is happening"},
    {"id": "ideas", "description": "35-50, watches philosophy and psychology channels, patient with ideas, hates padding "
                                   "and repetition"},
    {"id": "from_short", "description": "18-24, arrived from a Short, wants the hook paid off fast"},
]
GATE = {"early_seconds": 30.0, "median_interest": 7.0, "low_block": 5.0, "reversal_interest": 8.0}


def personas() -> list[dict]:
    return list(getattr(config, "VIEWER_PERSONAS", None) or DEFAULT_PERSONAS)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", str(text or "").lower().replace("’", "'"))).strip()


def blocks(sections: list[dict], words_per_minute: float | None = None) -> list[dict]:
    """The screenplay as 15-second blocks of narration: [{"block", "start", "end", "text", "sections"}]."""
    sentences = attention.timing(sections, words_per_minute)
    total = attention.duration(sentences)
    found, start, number = [], 0.0, 0
    while start < total:
        lines = [item for item in sentences if start <= item["start"] < start + BLOCK_SECONDS]
        if lines:
            found.append({"block": number, "start": start, "end": min(total, start + BLOCK_SECONDS),
                          "text": " ".join(item["text"] for item in lines),
                          "sections": sorted({item["section"] for item in lines}),
                          "section_types": sorted({item["section_type"] for item in lines})})
            number += 1
        start += BLOCK_SECONDS
    return found


def contract(chosen: list[dict]) -> str:
    listing = "\n".join(f"- {item['id']}: {item['description']}" for item in chosen)
    return f"""You simulate real YouTube viewers watching a narrated illustrated story. Each VIEWER below reads the
screenplay in the 15-second BLOCKS given (with timestamps), in order, as if hearing it for the first time, and is
honest: a slow block loses them even if the writing is beautiful. Never be kind for its own sake.
VIEWERS:
{listing}
For every viewer and every block return interest 1-10, would_leave (true if they would swipe away during this
block), a short reason and a quote (the exact words of the block that caused it). Once a viewer leaves, keep scoring
the later blocks as if they had stayed. Also give first_leave_second: the first second they would leave (null if
they would stay to the end).
Return ONLY JSON {{"personas": [{{"id": "...", "first_leave_second": number|null, "blocks": [{{"block": 0,
"interest": 1-10, "would_leave": true|false, "reason": "...", "quote": "..."}}, ...]}}]}}."""


def validate(value: dict, block_list: list[dict], chosen: list[dict]) -> dict:
    items = value.get("personas") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise NarrativeValidationError("viewer simulation needs a personas array")
    wanted = [item["id"] for item in chosen]
    by_id = {item.get("id"): item for item in items if isinstance(item, dict)}
    missing = [name for name in wanted if name not in by_id]
    if missing:
        raise NarrativeValidationError(f"viewer simulation is missing personas: {', '.join(missing)}")
    texts = {item["block"]: item for item in block_list}
    total = block_list[-1]["end"] if block_list else 0
    found = []
    for name in wanted:
        persona = by_id[name]
        rows = {row.get("block"): row for row in persona.get("blocks") or [] if isinstance(row, dict)}
        if sorted(key for key in rows if key in texts) != sorted(texts):
            raise NarrativeValidationError(f"persona {name} must score every block exactly once")
        clean = []
        for number in sorted(texts):
            row = rows[number]
            try:
                interest = int(round(float(row.get("interest"))))
            except (TypeError, ValueError):
                raise NarrativeValidationError(f"persona {name} block {number}: interest must be 1-10") from None
            if not 1 <= interest <= 10:
                raise NarrativeValidationError(f"persona {name} block {number}: interest must be 1-10")
            quote = str(row.get("quote", "")).strip()
            if quote and _norm(quote) not in _norm(texts[number]["text"]):
                quote = ""                              # an invented quote is not evidence
            clean.append({"block": number, "start": texts[number]["start"], "interest": interest,
                          "would_leave": bool(row.get("would_leave")), "reason": str(row.get("reason", ""))[:200], "quote": quote})
        leave = persona.get("first_leave_second")
        leaves = [row["start"] for row in clean if row["would_leave"]]
        try:
            leave = float(leave) if leave is not None else None
        except (TypeError, ValueError):
            leave = None
        if leave is None and leaves:
            leave = leaves[0]
        if leave is not None:
            leave = max(0.0, min(total, leave))
        found.append({"id": name, "first_leave_second": leave, "blocks": clean})
    return {"personas": found}


def gate(result: dict, block_list: list[dict], reversal_start: float | None = None) -> dict:
    """The thresholds; returns {passes, failures, block_medians, reversal_block}."""
    chosen = result.get("personas", [])
    medians = []
    for item in block_list:
        scores = [row["interest"] for persona in chosen for row in persona["blocks"] if row["block"] == item["block"]]
        medians.append(statistics.median(scores) if scores else 0)
    failures = []
    early = sum(1 for persona in chosen if persona["first_leave_second"] is not None
                and persona["first_leave_second"] < GATE["early_seconds"])
    if chosen and early > len(chosen) / 2:
        failures.append(f"{early} de {len(chosen)} espectadores simulados saem nos primeiros 30 s")
    overall = statistics.median(medians) if medians else 0
    if overall < GATE["median_interest"]:
        failures.append(f"interesse mediano {overall:.1f} (mínimo {GATE['median_interest']:.0f})")
    for index in range(len(medians) - 1):
        if medians[index] < GATE["low_block"] and medians[index + 1] < GATE["low_block"]:
            failures.append(f"dois blocos seguidos abaixo de 5 a partir de {attention.clock(block_list[index]['start'])}")
            break
    reversal_block = None
    if reversal_start is not None:
        reversal_block = next((item["block"] for item in reversed(block_list) if item["start"] <= reversal_start), None)
        if reversal_block is not None and medians[reversal_block] < GATE["reversal_interest"]:
            failures.append(f"o bloco da virada ({attention.clock(block_list[reversal_block]['start'])}) tem interesse "
                            f"{medians[reversal_block]:.1f} (mínimo {GATE['reversal_interest']:.0f})")
    return {"passes": not failures, "failures": failures, "block_medians": medians, "median_interest": overall,
            "reversal_block": reversal_block, "early_leavers": early}


def predicted_retention(result: dict, block_list: list[dict]) -> list[dict]:
    """Share of the simulated viewers still watching at each block's start (a simulation, not a forecast)."""
    chosen = result.get("personas", [])
    return [{"start": item["start"], "share": round(sum(1 for persona in chosen if persona["first_leave_second"] is None
                                                         or persona["first_leave_second"] > item["start"]) / max(1, len(chosen)), 3)}
            for item in block_list]


def reversal_start(sections: list[dict], words_per_minute: float | None = None) -> float | None:
    sentences = attention.timing(sections, words_per_minute)
    return next((item["start"] for item in sentences if item["section_type"] == "reversal"), None)


def simulate(sections: list[dict], *, generator=None, words_per_minute: float | None = None, run_dir=None) -> dict:
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    block_list = blocks(sections, words_per_minute)
    chosen = personas()
    mode = str(getattr(config, "VIEWER_SIM_MODE", "single"))
    groups = [chosen] if mode != "per_persona" else [[item] for item in chosen]
    merged = []
    context = "BLOCKS: " + compact_json([{"block": item["block"], "time": f"{attention.clock(item['start'])}-{attention.clock(item['end'])}",
                                          "text": item["text"]} for item in block_list])
    for group in groups:
        value = generate_json(contract(group), "viewer simulation", lambda found, group=group: validate(found, block_list, group),
                              generator=generator or generate, context=context, stage="viewer_sim",
                              temperature_key="editorial_review", run_dir=str(run_dir) if run_dir else None)
        merged += value["personas"]
    result = {"personas": merged}
    verdict = gate(result, block_list, reversal_start(sections, words_per_minute))
    return {"blocks": [{key: item[key] for key in ("block", "start", "end", "text")} for item in block_list],
            "personas": merged, "gate": verdict, "predicted_retention": predicted_retention(result, block_list),
            "label": "simulação", "duration": block_list[-1]["end"] if block_list else 0}


def run(output_dir, script: dict, *, generator=None, words_per_minute: float | None = None, force: bool = False) -> dict:
    """viewer_sim.json for the screenplay (cached on the narration and the personas)."""
    sections = script.get("sections", [])
    key = stable_hash({"narration": [section.get("narration", "") for section in sections], "personas": personas(),
                       "version": VERSION, "wpm": round(float(words_per_minute or attention.wpm()), 1)})
    path = Path(output_dir) / FILE_NAME
    saved = load_json(path) or {}
    if saved.get("key") == key and not force:
        return saved
    record = {"key": key, **simulate(sections, generator=generator, words_per_minute=words_per_minute, run_dir=output_dir)}
    atomic_write_json(path, record)
    return record


def load(output_dir) -> dict | None:
    return load_json(Path(output_dir) / FILE_NAME)


def correlation(simulated: list[dict], real_curve: list[dict], duration: float) -> float | None:
    """Pearson correlation between the simulated share still watching and the real audience watch ratio, sampled at
    the simulated blocks (Phase 4.1-G calibration). None when there is too little to compare."""
    if not simulated or not real_curve or not duration:
        return None
    xs, ys = [], []
    for point in simulated:
        ratio = point["start"] / duration
        nearest = min(real_curve, key=lambda item: abs(item["ratio"] - ratio))
        xs.append(point["share"])
        ys.append(nearest["watch"])
    if len(xs) < 3:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    spread_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if not spread_x or not spread_y:
        return None
    return round(sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / (spread_x * spread_y), 3)
