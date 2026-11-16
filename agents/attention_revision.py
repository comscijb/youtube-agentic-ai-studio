"""Rewrite until it holds (Phase 4.1-E).

When the attention contract (B) or the viewer simulation (D) fails, a targeted attention revision rewrites the
failing scenes only, with instructions built from the findings and the personas' reasons (open on action or
dialogue, cut description to one concrete image, move the reveal earlier, end on a question or partial reveal,
split long sentences, keep the dialogue that turns the scene).

The guards of earlier phases still hold: brief faithfulness and no brief paste (3.9.2), the chronology guard, the
cold-open rules (3.9.7) and the author's quoted lines word for word. Shorter is allowed: a revised scene has no
minimum (the total floor is lifted for the scenes cut here, ``attention_shortened``), only its maximum.

At most 2 rounds; C and D run again after each. What still fails goes to the human review as
"não passou no portão de atenção"; uploading then needs a typed override reason (recorded in the approval).
"""
from __future__ import annotations

import json
from pathlib import Path

import config
from agents import attention, attention_map, viewer_sim
from agents.narrative import NarrativeValidationError, atomic_write_json

MAX_ROUNDS = 2
FAILED_STATUS = "não passou no portão de atenção"
MIN_SCENE_WORDS = 25
RECOMMENDATIONS = {
    "first_5_seconds": "Open on an action, a spoken line or an impossible fact; no setting, weather or appearance first.",
    "inciting_event": "Put the story's central problem or rule in motion within the first 45 seconds after the cold open; move the reveal earlier.",
    "dead_air": "Add a question, a partial reveal, an escalation or a line of dialogue that changes something in this stretch.",
    "description_run": "Cut the description to one concrete sensory image and return to action, dialogue or a question.",
    "ear_average": "Split long sentences; vary the rhythm with short ones.",
    "ear_long_share": "Split the sentences over 20 words.",
    "ear_too_long": "Split this sentence in two or three.",
    "loop_unclosed": "Pay off this open question later, or make it the deliberate final question.",
    "no_live_loop": "Open a new question before the previous one closes.",
    "scene_without_loop": "End the scene on a question, a partial reveal or a turn.",
}


def evaluate(output_dir, script: dict, *, generator=None, words_per_minute: float | None = None, simulate: bool = True) -> dict:
    """C (map), B (checks on the map) and D (simulation) for the current screenplay."""
    sections = script.get("sections", [])
    found_map = attention_map.build(output_dir, sections, generator=generator, words_per_minute=words_per_minute)
    checked = attention.check(sections, found_map, words_per_minute=words_per_minute)
    sim = viewer_sim.run(output_dir, script, generator=generator, words_per_minute=words_per_minute) if simulate else None
    passes = checked["passes"] and (sim is None or sim["gate"]["passes"])
    return {"check": checked, "sim": sim, "passes": passes, "duration": checked["duration"]}


def _section_ids(script: dict, scene_ids) -> set[int]:
    """Map outline scene ids ("s03") or numeric ids to the screenplay sections' ``id``."""
    wanted = {str(item) for item in scene_ids}
    return {section["id"] for section in script.get("sections", [])
            if str(section.get("outline_scene_id")) in wanted or str(section.get("id")) in wanted}


def failing(script: dict, evaluation: dict) -> dict[int, list[str]]:
    """{section id: [instruction, ...]} from the findings and the personas' reasons."""
    found: dict[int, list[str]] = {}
    for finding in evaluation["check"]["findings"]:
        if finding["severity"] != "major" and finding["rule"] not in ("description_run", "dead_air", "scene_without_loop", "ear_too_long"):
            continue
        for section in _section_ids(script, finding["scene_ids"]):
            found.setdefault(section, []).append(f"[{finding['at']}] {finding['claim']} → {RECOMMENDATIONS.get(finding['rule'], finding['recommendation'])}")
    sim = evaluation.get("sim")
    if sim and not sim["gate"]["passes"]:
        sentences = attention.timing(script.get("sections", []))
        voiced = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
        medians = sim["gate"]["block_medians"]
        for index, block in enumerate(sim["blocks"]):
            low = medians[index] < viewer_sim.GATE["low_block"] or (block["start"] < viewer_sim.GATE["early_seconds"] and medians[index] < 7)
            if index == sim["gate"].get("reversal_block"):
                low = low or medians[index] < viewer_sim.GATE["reversal_interest"]
            if not low:
                continue
            reasons = [row["reason"] for persona in sim["personas"] for row in persona["blocks"] if row["block"] == block["block"] and row["reason"]]
            sections = {line["section"] for line in sentences if block["start"] <= line["start"] < block["end"]}
            for position in sections:
                if position < len(voiced):
                    found.setdefault(voiced[position]["id"], []).append(
                        f"[{attention.clock(block['start'])}] simulated viewers lose interest (median {medians[index]}): "
                        + "; ".join(dict.fromkeys(reasons))[:300])
    return {key: list(dict.fromkeys(value)) for key, value in found.items()}


def contract(identity: str, required: str) -> str:
    return f"""You perform a targeted ATTENTION revision of a Private Gravity screenplay: the viewer is on a phone
and can leave at any second. Return ONLY the SELECTED SCENES supplied after this contract, in order, as
{{"scenes": [...]}} with the same keys. Fix the problems listed for each scene:
- open the scene in motion, on an action or a spoken line; never on setting, weather or appearance
- cut description to one concrete sensory image
- move the reveal earlier; let a question open early and stay alive
- end the scene on a question, a partial reveal or a turn
- short spoken sentences with varied rhythm; split any sentence over 20 words
- keep every line of dialogue that turns the scene
Write for the ear, keep the channel's literary voice: precise, restrained, never clickbait-cheap. Shorter is
allowed: never pad. Preserve every scene id, section_type, established fact, chronology, character identity,
causal outcome and cold-open device. Never paste the author's brief; never explain psychology.
Keep these author's lines word for word wherever they already appear:{required}

{identity}"""


def _required(brief: dict) -> list[dict]:
    from agents import premise_brief
    return premise_brief.required_lines((brief or {}).get("author_brief") or {})


def guards(before: dict, after: dict, brief: dict, outline: dict | None) -> list[str]:
    """What the revision may not break: brief paste, required lines, chronology, cold-open rules."""
    from agents import cold_open, premise_brief, prose_lint, scriptwriter
    problems = []
    before_text = " ".join(section.get("narration", "") for section in before["sections"])
    after_text = " ".join(section.get("narration", "") for section in after["sections"])
    for section in after["sections"]:
        try:
            scriptwriter._reject_paste(section.get("narration", ""), brief)
        except NarrativeValidationError as exc:
            problems.append(str(exc))
    for item in _required(brief):
        kind = item.get("kind", "narrated")
        if premise_brief._quote_present(kind, item["line"], before_text) and not premise_brief._quote_present(kind, item["line"], after_text):
            problems.append(f"the author's line was lost: {item['line'][:80]}")
    count = lambda findings: sum(1 for item in findings if item.get("severity") in ("major", "blocking"))
    if count(prose_lint.chronology_findings(after["sections"], outline)) > count(prose_lint.chronology_findings(before["sections"], outline)):
        problems.append("the revision broke the chronology")
    kind = after.get("narrative_type")
    if count(cold_open.findings(after["sections"], outline, narrative_type=kind)) > count(cold_open.findings(before["sections"], outline, narrative_type=kind)):
        problems.append("the revision broke the cold-open rules")
    return problems


def revise(script: dict, instructions: dict[int, list[str]], *, brief: dict, bible: dict, outline: dict | None,
           generator=None) -> dict:
    """One round over the failing scenes; returns the new script (unchanged scenes byte for byte)."""
    from agents.gemini_client import generate
    from agents.identity import identity_block
    from agents.structured import compact_json, generate_json
    from agents import premise_brief
    ids = sorted(instructions)
    selected = [section for section in script["sections"] if section["id"] in ids]
    maxima = {section["id"]: max(MIN_SCENE_WORDS, int(len(section["narration"].split()) * 1.15)) for section in selected}
    required = "".join(f"\n- {item['line']}" for item in _required(brief)) or " none."

    def validate(value):
        scenes = value.get("scenes") if isinstance(value, dict) else None
        if not isinstance(scenes, list) or [scene.get("id") for scene in scenes] != ids:
            raise NarrativeValidationError(f"attention revision must return scenes {ids} in order")
        for scene in scenes:
            words = len(str(scene.get("narration", "")).split())
            if words < MIN_SCENE_WORDS or words > maxima[scene["id"]]:
                raise NarrativeValidationError(f"revised scene {scene['id']} has {words} words (allowed {MIN_SCENE_WORDS}-{maxima[scene['id']]})")
        return scenes
    context = (f"SELECTED SCENES: {compact_json(selected)}\nPROBLEMS PER SCENE: {compact_json({str(key): value for key, value in instructions.items()})}\n"
               f"STORY BRIEF: {compact_json(premise_brief.without_beats(brief or {}))}\nCHARACTER BIBLE: {compact_json(bible or {})}")
    scenes = generate_json(contract(identity_block(script.get("narrative_type")), required), "attention revision", validate,
                           generator=generator or generate, context=context, stage=f"attention_revision {'-'.join(map(str, ids))}",
                           temperature_key="expansion")
    by_id = {scene["id"]: scene for scene in scenes}
    updated = []
    for original in script["sections"]:
        if original["id"] not in by_id:
            updated.append(original)
            continue
        merged = dict(original, narration=str(by_id[original["id"]]["narration"]).strip())
        updated.append(merged)
    candidate = dict(script, sections=updated)
    problems = guards(script, candidate, brief or {}, outline)
    if problems:
        raise NarrativeValidationError("attention revision rejected: " + "; ".join(problems))
    shorter = {section["id"] for section, before in zip(updated, script["sections"])
               if len(section["narration"].split()) < len(before["narration"].split())}
    candidate["attention_shortened"] = sorted(set(script.get("attention_shortened") or []) | shorter)
    return candidate


def run(output_dir, script: dict, *, brief: dict, bible: dict, outline: dict | None, generator=None,
        words_per_minute: float | None = None, max_rounds: int | None = None) -> tuple[dict, dict]:
    """Evaluate, then revise the failing scenes up to ``max_rounds`` times; returns (script, attention record)."""
    rounds_allowed = MAX_ROUNDS if max_rounds is None else min(MAX_ROUNDS, max_rounds)
    evaluation = evaluate(output_dir, script, generator=generator, words_per_minute=words_per_minute)
    before = evaluation["duration"]
    rounds = []
    while not evaluation["passes"] and len(rounds) < rounds_allowed:
        todo = failing(script, evaluation)
        if not todo:
            break
        entry = {"round": len(rounds) + 1, "scene_ids": sorted(todo), "majors_before": evaluation["check"]["major"],
                 "gate_before": (evaluation.get("sim") or {}).get("gate", {}).get("failures", [])}
        try:
            revised = revise(script, todo, brief=brief, bible=bible, outline=outline, generator=generator)
        except NarrativeValidationError as exc:
            entry.update(success=False, error=str(exc)[:400])
            rounds.append(entry)
            continue
        candidate = evaluate(output_dir, revised, generator=generator, words_per_minute=words_per_minute)
        entry.update(success=True, majors_after=candidate["check"]["major"],
                     gate_after=(candidate.get("sim") or {}).get("gate", {}).get("failures", []))
        better = candidate["check"]["major"] <= evaluation["check"]["major"]
        if better:
            script, evaluation = revised, candidate
        else:
            entry["kept_previous"] = True
        rounds.append(entry)
    record = {"status": "passed" if evaluation["passes"] else "failed",
              "label": "passou no portão de atenção" if evaluation["passes"] else FAILED_STATUS,
              "findings": evaluation["check"]["findings"], "major": evaluation["check"]["major"],
              "sim_gate": (evaluation.get("sim") or {}).get("gate"), "rounds": rounds,
              "duration_before": before, "duration_after": evaluation["duration"]}
    atomic_write_json(Path(output_dir) / "attention_review.json", record)
    return script, record


def target_minutes(narrative_type: str | None) -> float | None:
    """Phase 4.1-F: the episode length by type when a brief sets none (A 9, B 9, C 7 minutes)."""
    from agents import cold_open
    return (getattr(config, "ATTENTION_TARGET_MINUTES", {}) or {}).get(cold_open.letter(narrative_type))
