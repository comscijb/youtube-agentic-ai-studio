"""Thumbnail–video coherence: no deceptive imagery (Phase 3.9-E). Warnings in the approval gate.

* The selected thumbnail's **focal element** must appear in the video: the characters its
  composition shows are on screen in some shot (shot list ``characters``), and an object or place it
  relies on appears in a shot description or the visual bible.
* The thumbnail **text** must reflect something in the story: one low-temperature Gemini check
  against the screenplay ("does the story contain this situation?"), cached by text + script.
* Each Short's **hook text** must come from its own segment: a string/stem match against the
  segment's words (cheap, no Gemini).
* Never realistic visuals: thumbnail prompts keep the style suffix (see tests).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.narrative import atomic_write_json, load_json, stable_hash

STOP = {"the", "a", "an", "of", "his", "her", "their", "its", "on", "in", "at", "with", "and", "to", "from",
        "face", "faces", "eyes", "hand", "hands", "turned", "away", "two", "one", "single", "small", "big"}
TEXT_CHECK_FILE = "thumbnail_text_check.json"


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]+", str(text or "").lower()) if len(word) > 2 and word not in STOP}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)][:6]
    return word[:6]


def overlap(needle: str, haystack: str) -> float:
    """Share of ``needle``'s content words (by stem) that occur in ``haystack``."""
    words = _words(needle)
    if not words:
        return 1.0
    stems = {_stem(word) for word in _words(haystack)}
    return sum(1 for word in words if _stem(word) in stems) / len(words)


def selected_concept(run_dir) -> dict | None:
    index = load_json(Path(run_dir) / "thumbnails" / "thumbnails.json") or {}
    selected = next((item for item in index.get("variants", []) if item.get("number") == index.get("selected")), None)
    return (selected or {}).get("concept")


def focal_findings(run_dir) -> list[str]:
    """Warnings when the selected thumbnail relies on something the video never shows."""
    run = Path(run_dir)
    concept = selected_concept(run)
    if not concept:
        return []
    from agents import visual_bible as vbible
    shots = (load_json(run / "shot_list.json") or {}).get("shots", [])
    vb = load_json(run / vbible.FILE_NAME) or {}
    on_screen = {cid for shot in shots for cid in shot.get("characters") or []}
    findings = []
    people = [item["character_id"] for item in vbible.on_screen(vb)][:1]
    if concept.get("composition") == "two_person" and concept.get("other_character"):
        people.append(concept["other_character"])
    for cid in people:
        if shots and cid not in on_screen:
            findings.append(f"A miniatura mostra {cid}, que não aparece em nenhum plano do vídeo.")
    if concept.get("composition") in ("telling_object", "small_vs_presence"):
        corpus = " ".join([str(shot.get("action", "")) + " " + str(shot.get("prompt", "")) for shot in shots]
                          + [str(item.get("descriptor", "")) for item in vb.get("locations", [])]
                          + [str(item.get("visual_proxy", "")) for item in vb.get("characters", [])])
        if overlap(concept.get("focal_element", ""), corpus) < 0.5:
            findings.append(f"O foco da miniatura (“{concept.get('focal_element')}”) não aparece em nenhum plano.")
    return findings


def text_prompt(text: str, script: dict) -> str:
    story = " ".join(str(section.get("narration", "")) for section in script.get("sections", []))[:12000]
    return (f"Does the story below contain the situation this thumbnail text refers to? Thumbnail text: "
            f"\"{text}\". Return ONLY JSON {{\"contains\": true|false, \"evidence\": \"a short quote or why not\"}}."
            f"\nSTORY: {story}")


def text_findings(run_dir, *, generator=None) -> list[str]:
    """One cached Gemini check (temperature from 'title_scoring', low) that the text is in the story."""
    run = Path(run_dir)
    concept = selected_concept(run)
    script = load_json(run / "script.json") or {}
    if not concept or not concept.get("text") or not script:
        return []
    key = stable_hash({"text": concept["text"], "script": [section.get("narration") for section in script.get("sections", [])]})
    cached = load_json(run / TEXT_CHECK_FILE) or {}
    if cached.get("key") != key:
        if generator is None:
            return []
        from agents.narrative import extract_json
        try:
            answer = extract_json(generator(text_prompt(concept["text"], script), stage="thumbnail_text_check",
                                            temperature=0.1), "thumbnail text check")
        except Exception:                                # noqa: BLE001 — a check that could not run warns nothing
            return []
        cached = {"key": key, "contains": bool(answer.get("contains")), "evidence": str(answer.get("evidence", ""))[:300]}
        atomic_write_json(run / TEXT_CHECK_FILE, cached)
    if cached.get("contains"):
        return []
    return [f"O texto da miniatura (“{concept['text']}”) não corresponde a nada da história: {cached.get('evidence', '')}"]


def short_findings(run_dir) -> list[str]:
    """A Short's hook text must come from the segment's own words."""
    from video import shorts
    run = Path(run_dir)
    plan = shorts.load(run) or {}
    words = (load_json(run / shorts.WORDS_FILE) or {}).get("words", [])
    findings = []
    for segment in plan.get("segments", []):
        if not segment.get("include", True) or not segment.get("hook_text"):
            continue
        text = " ".join(str(word.get("text", "")) for word in words
                        if segment["start"] - 0.05 <= float(word.get("start", 0)) < segment["end"])
        if text and overlap(segment["hook_text"], text) < 0.5:
            findings.append(f"{segment['id']}: o texto de gancho (“{segment['hook_text']}”) não vem da fala do trecho.")
    return findings


def findings(run_dir, *, generator=None) -> list[str]:
    return focal_findings(run_dir) + text_findings(run_dir, generator=generator) + short_findings(run_dir)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
