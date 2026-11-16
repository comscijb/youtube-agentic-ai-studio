"""The attention contract for every screenplay (Phase 4.1-B).

A viewer on a phone decides in seconds. Every check here speaks in **seconds of video**: the timing comes from the
channel voice's calibrated words per minute plus the narration pauses (``NARRATION_PAUSES``).

Deterministic checks (findings with timestamps):

* **first 5 seconds** (about the first 12 spoken words): a concrete strangeness, conflict or threat — an action, a
  line of dialogue, an impossible fact; never setting, weather or appearance;
* **inciting event by 0:45** after the cold open: the central problem or rule in motion;
* **dead air**: no stretch over 25 s without an attention event (question, open loop, reveal, escalation, decision,
  a dialogue line that turns something);
* **description runs**: three or more sentences in a row with no action, dialogue or question;
* **ear-friendly sentences**: average ≤ 12 words, ≤ 15 % over 20, none over 30;
* **open loops**: every loop closes or is the final question; one is alive at every moment after the hook; each
  scene opens a loop or gives a partial reveal.

The events come from the semantic attention map (``agents.attention_map``, one Gemini call per screenplay); without a
map a lexical fallback stands in (quotes, questions, conflict words), so the checks still run offline.
Major: the first 5 seconds, the inciting event, a description run in the first 45 s after the cold open, a gap
over 40 s, no live loop for more than 60 s.
"""
from __future__ import annotations

import re

import config

FIRST_WORDS = 12
INCITING_SECONDS = 45.0
GAP_SECONDS = 25.0
GAP_MAJOR_SECONDS = 40.0
LOOP_MAJOR_SECONDS = 60.0
HOOK_SECONDS = 30.0
DESCRIPTION_RUN = 3
AVERAGE_WORDS = 12.0
LONG_WORDS = 20
LONG_SHARE = 0.15
MAX_WORDS = 30
EVENT_TAGS = ("question", "open_loop", "close_loop", "reveal", "escalation", "decision", "dialogue_turn")
INCITING_TAGS = ("open_loop", "reveal", "escalation", "decision", "dialogue_turn")
CONFLICT = re.compile(
    r"\b(?:kill\w*|die[ds]?|dead|death|murder\w*|lie[ds]?|lying|lied|steal\w*|stole\w*|sold|sell\w*|signed|sealed|"
    r"scream\w*|blood\w*|gun|knife|threat\w*|impossible|never|nobody|no one|everyone|vanish\w*|disappear\w*|"
    r"forbid\w*|secret\w*|ruin\w*|betray\w*|refus\w*|hunt\w*|fear\w*|terrif\w*|wrong|stop(?:ped|s)?|last)\b", re.I)
DESCRIPTIVE = re.compile(
    r"\b(?:dawn|dusk|morning|evening|night|fog|mist|rain|snow|frost\w*|sky|sun\w*|light|shadow\w*|wind|air|"
    r"sat|stood|lay|hung|stretch\w*|filter\w*|drift\w*|loom\w*|glow\w*|gleam\w*|quiet|silent|old|drafty|wooden|"
    r"was|were)\b", re.I)
ACTION = re.compile(
    r"\b(?:grab\w*|slam\w*|run|ran|push\w*|pull\w*|press\w*|tear\w*|tore|throw\w*|threw|hit|strike\w*|struck|"
    r"shout\w*|scream\w*|whisper\w*|demand\w*|signed|sealed|stole|sold|bought|broke|break\w*|turn(?:s|ed)?|"
    r"snap\w*|step(?:s|ped)?|reach\w*|freez\w*|stop(?:s|ped)?|open\w*|shut|lock\w*|burn\w*|declar\w*)\b", re.I)
QUOTE = re.compile(r"[\"“”]|(?:^|\s)'[A-Z]")
_SPLIT = re.compile(r"(?<=[.!?…])[\"'”’)]*\s+(?=[\"'“‘(]?[A-Z0-9])")


def clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def wpm() -> float:
    try:
        from agents import voice_calibration
        return float(voice_calibration.planning_wpm())
    except Exception:                                    # noqa: BLE001 — the configured rate is the fallback
        return float(getattr(config, "NARRATION_WPM", 150))


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SPLIT.split(str(text or "").strip()) if part.strip()]


def _voiced(section: dict) -> bool:
    return bool(str(section.get("narration", "")).strip())


def timing(sections: list[dict], words_per_minute: float | None = None) -> list[dict]:
    """Every spoken sentence with its start and end in seconds of video."""
    rate = float(words_per_minute or wpm())
    pauses = {"sentence": 0.35, "paragraph": 0.7, "scene": 1.5, **dict(getattr(config, "NARRATION_PAUSES", {}) or {})}
    voiced = [section for section in sections if _voiced(section)]
    found, clock_at = [], 0.0
    for section_index, section in enumerate(voiced):
        paragraphs = [part for part in re.split(r"\n\s*\n", str(section["narration"]).strip()) if part.strip()]
        for paragraph_index, paragraph in enumerate(paragraphs):
            lines = split_sentences(paragraph)
            for line_index, line in enumerate(lines):
                count = len(re.findall(r"[A-Za-z0-9'’-]+", line))
                start = clock_at
                end = start + count / rate * 60.0
                last_line = line_index == len(lines) - 1
                pause = pauses["sentence"]
                if last_line and paragraph_index == len(paragraphs) - 1:
                    pause = pauses["scene"]
                elif last_line:
                    pause = pauses["paragraph"]
                found.append({"index": len(found), "section": section_index, "scene_id": section.get("outline_scene_id") or section.get("id"),
                              "section_type": section.get("section_type", ""), "title": section.get("title", ""),
                              "text": line, "words": count, "start": round(start, 2), "end": round(end, 2)})
                clock_at = end + pause
    return found


def duration(sentences: list[dict]) -> float:
    return round(sentences[-1]["end"], 1) if sentences else 0.0


def cold_open_end(sentences: list[dict]) -> float:
    """When the cold open ends (0 without one)."""
    teaser = [item for item in sentences if item["section_type"] == "cold_open"]
    return teaser[-1]["end"] if teaser else 0.0


# ── Events: the map's tags, or the lexical fallback ──────────────────────────

def tags_for(sentences: list[dict], attention_map: dict | None) -> list[list[str]]:
    """Each sentence's tags: from the attention map when present, else deterministic guesses."""
    mapped = {int(item["sentence"]): [tag["tag"] for tag in item.get("tags", [])]
              for item in (attention_map or {}).get("sentences", [])}
    found = []
    for item in sentences:
        if attention_map is not None:
            found.append(mapped.get(item["index"], []))
            continue
        guess = []
        if "?" in item["text"]:
            guess.append("question")
        if QUOTE.search(item["text"]):
            guess.append("dialogue_turn")
        if CONFLICT.search(item["text"]) and ACTION.search(item["text"]):
            guess.append("escalation")
        if not guess and DESCRIPTIVE.search(item["text"]) and not ACTION.search(item["text"]):
            guess.append("description")
        found.append(guess)
    return found


def _kinds(tags: list[str]) -> set[str]:
    return {tag.split(":")[0] for tag in tags}


def _finding(rule: str, severity: str, start: float, end: float, sentences: list[dict], claim: str, recommendation: str) -> dict:
    scenes = list(dict.fromkeys(item["scene_id"] for item in sentences if item["scene_id"] is not None))
    return {"rule": rule, "severity": severity, "issue_type": "attention", "start": round(start, 1), "end": round(end, 1),
            "at": clock(start), "scene_ids": scenes, "claim": claim,
            "evidence": [item["text"][:160] for item in sentences[:2]], "recommendation": recommendation}


# ── The rules ───────────────────────────────────────────────────────────────

def first_seconds(sentences: list[dict], tags: list[list[str]]) -> list[dict]:
    opening, count = [], 0
    for item in sentences:
        opening.append(item)
        count += item["words"]
        if count >= FIRST_WORDS:
            break
    if not opening:
        return []
    words = " ".join(item["text"] for item in opening).split()[:FIRST_WORDS]
    text = " ".join(words)
    kinds = set().union(*(_kinds(tags[item["index"]]) for item in opening))
    gripping = kinds & {"question", "open_loop", "reveal", "escalation", "decision", "dialogue_turn"} or QUOTE.search(text) \
        or (CONFLICT.search(text) and ACTION.search(text)) or "?" in text
    if gripping:
        return []
    return [_finding("first_5_seconds", "major", 0, opening[-1]["end"], opening,
                     f"Os primeiros 5 s (\"{text}…\") não trazem estranheza, conflito ou ameaça concreta.",
                     "Abra com uma ação, uma fala ou um fato impossível; nada de cenário, clima ou aparência.")]


def inciting_event(sentences: list[dict], tags: list[list[str]]) -> list[dict]:
    start = cold_open_end(sentences)
    window = [item for item in sentences if start <= item["start"] <= start + INCITING_SECONDS and item["section_type"] != "cold_open"]
    if not window:
        return []
    if any(_kinds(tags[item["index"]]) & set(INCITING_TAGS) for item in window):
        return []
    return [_finding("inciting_event", "major", start, window[-1]["end"], window,
                     f"Nenhum evento que ponha o problema central em movimento entre {clock(start)} e {clock(start + INCITING_SECONDS)}.",
                     "Coloque o problema ou a regra da história em movimento nos primeiros 45 s depois do cold open.")]


def dead_air(sentences: list[dict], tags: list[list[str]]) -> list[dict]:
    found, last, gap_lines = [], 0.0, []
    for item in sentences:
        if _kinds(tags[item["index"]]) & set(EVENT_TAGS):
            if item["start"] - last > GAP_SECONDS:
                found.append((last, item["start"], gap_lines))
            last, gap_lines = item["end"], []
        else:
            gap_lines.append(item)
    if sentences and sentences[-1]["end"] - last > GAP_SECONDS:
        found.append((last, sentences[-1]["end"], gap_lines))
    return [_finding("dead_air", "major" if end - start > GAP_MAJOR_SECONDS else "minor", start, end, lines,
                     f"{end - start:.0f} s sem evento de atenção ({clock(start)}–{clock(end)}).",
                     "Acrescente uma pergunta, uma revelação parcial, uma escalada ou uma fala que mude algo.")
            for start, end, lines in found if lines]


def description_runs(sentences: list[dict], tags: list[list[str]]) -> list[dict]:
    found, run = [], []
    opening_end = cold_open_end(sentences) + INCITING_SECONDS

    def close():
        if len(run) >= DESCRIPTION_RUN:
            # In the opening window (the first 45 s after the cold open) it is exposition where the hook should be.
            early = run[0]["start"] < opening_end
            found.append(_finding("description_run", "major" if early else "minor", run[0]["start"], run[-1]["end"], run,
                                  f"{len(run)} frases seguidas só de descrição ({clock(run[0]['start'])})"
                                  + (", na abertura" if early else "") + ".",
                                  "Corte para uma imagem concreta e volte à ação, à fala ou a uma pergunta."))
    for item in sentences:
        kinds = _kinds(tags[item["index"]])
        if (not kinds or kinds <= {"description", "reflection"}) and not QUOTE.search(item["text"]) and "?" not in item["text"]:
            run.append(item)
        else:
            close()
            run = []
    close()
    return found


def ear_sentences(sentences: list[dict]) -> list[dict]:
    if not sentences:
        return []
    found = []
    average = sum(item["words"] for item in sentences) / len(sentences)
    long = [item for item in sentences if item["words"] > LONG_WORDS]
    if average > AVERAGE_WORDS:
        found.append(_finding("ear_average", "minor", 0, duration(sentences), sentences[:1],
                              f"Frases com {average:.1f} palavras em média (máximo {AVERAGE_WORDS:.0f}).",
                              "Divida as frases longas; varie o ritmo com frases curtas."))
    if len(long) / len(sentences) > LONG_SHARE:
        found.append(_finding("ear_long_share", "minor", long[0]["start"], long[-1]["end"], long,
                              f"{len(long)} de {len(sentences)} frases têm mais de {LONG_WORDS} palavras ({len(long) / len(sentences):.0%}; máximo 15 %).",
                              "Quebre as frases longas em duas."))
    for item in sentences:
        if item["words"] > MAX_WORDS:
            found.append(_finding("ear_too_long", "minor", item["start"], item["end"], [item],
                                  f"Frase de {item['words']} palavras aos {clock(item['start'])} (máximo {MAX_WORDS}).",
                                  "Divida esta frase."))
    return found


def loops(sentences: list[dict], tags: list[list[str]], attention_map: dict | None) -> list[dict]:
    """Open loops close (or are the final question), one is alive after the hook, each scene opens or reveals."""
    if attention_map is None:
        return []                                       # loops need the semantic map
    found, opened, closed = [], {}, {}
    for item in sentences:
        for tag in tags[item["index"]]:
            kind, _, loop = tag.partition(":")
            if kind == "open_loop" and loop and loop not in opened:
                opened[loop] = item
            elif kind == "close_loop" and loop:
                closed[loop] = item
    last_section = max((item["section"] for item in sentences), default=0)
    for loop, item in opened.items():
        if loop not in closed and item["section"] != last_section:
            found.append(_finding("loop_unclosed", "minor", item["start"], item["end"], [item],
                                  f"A pergunta \"{loop}\" aberta aos {clock(item['start'])} nunca se fecha.",
                                  "Feche este fio mais adiante, ou deixe-o como a pergunta final de propósito."))
    end = duration(sentences)
    alive = sorted((item["start"], closed[loop]["end"] if loop in closed else end) for loop, item in opened.items())
    cursor = min(HOOK_SECONDS, end)
    for start, stop in alive + [(end, end)]:
        if start > cursor and start - cursor > 0.5:
            gap = start - cursor
            lines = [item for item in sentences if cursor <= item["start"] < start]
            if lines:
                found.append(_finding("no_live_loop", "major" if gap > LOOP_MAJOR_SECONDS else "minor", cursor, start, lines,
                                      f"{gap:.0f} s sem nenhuma pergunta aberta ({clock(cursor)}–{clock(start)}).",
                                      "Abra uma pergunta antes de fechar a anterior."))
        cursor = max(cursor, stop)
    for section in sorted({item["section"] for item in sentences}):
        lines = [item for item in sentences if item["section"] == section]
        if lines[0]["section_type"] == "cold_open":
            continue
        kinds = set().union(*(_kinds(tags[item["index"]]) for item in lines))
        if not kinds & {"open_loop", "reveal"}:
            found.append(_finding("scene_without_loop", "minor", lines[0]["start"], lines[-1]["end"], lines[:1],
                                  f"A cena \"{lines[0]['title']}\" não abre pergunta nem traz revelação parcial.",
                                  "Termine a cena numa pergunta, numa revelação ou numa virada."))
    return found


def check(sections: list[dict], attention_map: dict | None = None, *, words_per_minute: float | None = None) -> dict:
    """All the attention findings for a screenplay, with its estimated duration."""
    sentences = timing(sections, words_per_minute)
    tags = tags_for(sentences, attention_map)
    findings = (first_seconds(sentences, tags) + inciting_event(sentences, tags) + dead_air(sentences, tags)
                + description_runs(sentences, tags) + ear_sentences(sentences) + loops(sentences, tags, attention_map))
    findings.sort(key=lambda item: (item["start"], item["rule"]))
    return {"findings": findings, "duration": duration(sentences), "sentences": len(sentences),
            "major": sum(1 for item in findings if item["severity"] == "major"),
            "passes": not any(item["severity"] == "major" for item in findings),
            "source": "map" if attention_map is not None else "lexical"}


# ── Phase 4.1-F: the outline's attention plan ───────────────────────────────

BEATS = ("opens_with", "inside", "ends_on")
_LOOP_ID = re.compile(r"^[a-z0-9_]{2,40}$")


def outline_issues(outline: dict) -> list[str]:
    """The 4.1-B rules on the plan, before any scene is written: attention beats per scene, an opening in motion,
    a loop plan where a question is always open after the first scene and every loop opens before it closes."""
    scenes = [scene for scene in outline.get("scenes", []) if scene.get("section_type") != "cold_open"]
    if not scenes:
        return []
    issues = []
    for scene in scenes:
        beats = scene.get("attention_beats")
        if not isinstance(beats, dict) or any(not str(beats.get(key, "")).strip() for key in BEATS):
            issues.append(f"scene {scene.get('scene_id')} needs attention_beats with opens_with, inside and ends_on")
    first = (scenes[0].get("attention_beats") or {}).get("opens_with", "") if isinstance(scenes[0].get("attention_beats"), dict) else ""
    if first and not (QUOTE.search(first) or "?" in first or ACTION.search(first) or CONFLICT.search(first)) and DESCRIPTIVE.search(first):
        issues.append(f"scene {scenes[0].get('scene_id')} opens on description (\"{first[:60]}\"): open in motion")
    order = {scene.get("scene_id"): index for index, scene in enumerate(scenes)}
    plan = outline.get("loop_plan")
    if not isinstance(plan, list) or not plan:
        return issues + ["loop_plan must list the story's open questions"]
    spans = []
    for loop in plan:
        if not isinstance(loop, dict) or not _LOOP_ID.match(str(loop.get("id", ""))):
            issues.append(f"loop_plan entry needs a snake_case id: {str(loop)[:60]}")
            continue
        opens, closes = loop.get("opens"), loop.get("closes")
        if opens not in order:
            issues.append(f"loop {loop['id']} opens in an unknown scene {opens}")
            continue
        if closes is not None and closes not in order:
            issues.append(f"loop {loop['id']} closes in an unknown scene {closes}")
            continue
        if closes is not None and order[closes] < order[opens]:
            issues.append(f"loop {loop['id']} closes before it opens")
            continue
        spans.append((order[opens], len(scenes) - 1 if closes is None else order[closes]))
    for index in range(1, len(scenes)):
        if not any(start <= index <= end for start, end in spans):
            issues.append(f"no question is open during scene {scenes[index].get('scene_id')}")
    return issues
