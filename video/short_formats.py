"""Shorts written as Shorts (Phase 4.3-A/B): five formats, one script call, deterministic checks, a swipe gate.

The old Shorts were cuts of the episode narration (45-75 s, the calm episode delivery). Here every Short has its own
script, derived from the episode and true to it:

========== ======== =====================================================================================
format      seconds  what it is / how it ends
========== ======== =====================================================================================
hook        20-35    the most dramatic moment (usually the cold open), tighter; ends on the unanswered question
dilemma     20-35    the rule or choice in two lines, then "What would you do?" ("Who was right?" for type A)
line        12-25    one quotable line, big on screen, built to loop: the last words flow back into the first
countdown   30-45    rising tension toward the reversal; stops one beat before the payoff
pov         15-30    "POV: ..." second person, present tense, immersive; a sting and the CTA
========== ======== =====================================================================================

* ``allocate`` — ``SHORTS_PER_EPISODE`` (default 4, at most 6) scripts across the enabled formats of
  ``SHORTS_FORMATS`` ({format: weight}, 0 = off), largest weights first, in the format order.
* ``build`` — one Gemini call (contract-first JSON) proposes every script; ``validate`` checks the structure and that
  the scene ids and evidence quotes exist in the screenplay; ``checks`` runs the deterministic rules (hook first,
  short sentences, duration in range, CTA, no twist, title); ``simulate`` asks the "swiper" persona to read each
  script in 3-second blocks; ``gate`` decides. A script that fails gets one targeted rewrite, then goes to human
  review with its reasons (``status`` "review").
* The plan lives in ``shorts/shorts_plan.json`` with ``"mode": "formats"``; the old cut-down plan ("cuts") stays
  available as "Recortes do episódio" (``SHORTS_MODE``).
"""
from __future__ import annotations

import re
from pathlib import Path

import config
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json, stable_hash, title_passes, validate_title

FORMATS = {
    "hook": {"label": "Gancho", "seconds": (20, 35), "spoken_cta": True,
             "brief": "The episode's most dramatic moment (usually the cold open), rewritten tighter. Ends on the "
                      "unanswered question (the last sentence is a question)."},
    "dilemma": {"label": "Dilema", "seconds": (20, 35), "spoken_cta": True,
                "brief": "States the rule or the choice in two lines, then asks the viewer the question (field "
                         "\"question\"). The CTA invites a comment."},
    "line": {"label": "A Frase", "seconds": (12, 25), "spoken_cta": False,
             "brief": "One quotable line or moment, shown as big text, built to LOOP: the last words flow back "
                      "into the first words, so a replay sounds like one sentence. No spoken CTA (it would break "
                      "the loop); the CTA is on screen only."},
    "countdown": {"label": "Contagem para a virada", "seconds": (30, 45), "spoken_cta": True,
                  "brief": "Rising tension toward the reversal. STOPS ONE BEAT BEFORE THE PAYOFF: the last sentence "
                           "is the instant before, never the turn itself."},
    "pov": {"label": "POV", "seconds": (15, 30), "spoken_cta": True,
            "brief": "Second-person framing of the premise (on_screen_hook starts with \"POV:\"), present tense, "
                     "immersive. Never claims the viewer did something real; it is the story's situation. Ends on "
                     "a sting."},
}
ORDER = tuple(FORMATS)
DEFAULT_WEIGHTS = {name: 1.0 for name in ORDER}
MAX_PER_EPISODE = 6
WORDS = (40, 110)
HOOK_WORDS = 10
SCREEN_HOOK_WORDS = 5
CTA_WORDS = 8
TITLE_MAX = 50                    # before " #Shorts"
SENTENCE_AVG = 12
SENTENCE_MAX = 18
BLOCK_SECONDS = 3.0
DEFAULT_CTA_SCREEN = "Full story on the channel ↑"
VERSION = 1
# Scene-setting openers: the first spoken words must be the hook, never the stage.
OPENERS = re.compile(r"^(?:in a (?:town|village|city|world|small|quiet)|in the (?:town|village|city|year|days)|once\b|"
                     r"there (?:was|were|once|lived)|long ago|years ago|it was (?:a|the|an)\b|this is the story|"
                     r"meet\b|let me tell you|imagine a (?:town|village|world|city|man|woman)|on a (?:quiet|cold|dark|rainy)|"
                     r"every (?:morning|day|night) in)", re.I)
COMMENT_WORDS = ("comment", "comments", "tell me", "below", "you decide", "your answer", "would you")
LOOP_SECONDS = 0.5


def enabled_weights() -> dict[str, float]:
    configured = dict(getattr(config, "SHORTS_FORMATS", None) or DEFAULT_WEIGHTS)
    return {name: float(configured.get(name, 0) or 0) for name in ORDER if float(configured.get(name, 0) or 0) > 0}


def per_episode() -> int:
    return max(1, min(MAX_PER_EPISODE, int(getattr(config, "SHORTS_PER_EPISODE", 4))))


def mode() -> str:
    return "cuts" if str(getattr(config, "SHORTS_MODE", "formats")).lower() == "cuts" else "formats"


def allocate(count: int | None = None, weights: dict | None = None) -> list[str]:
    """Which format each script gets: highest-averages over the weights (each enabled format once before any
    repeats when there is room), returned in the format order."""
    weights = {name: float(value) for name, value in (weights if weights is not None else enabled_weights()).items()
               if name in FORMATS and float(value) > 0}
    count = max(0, min(MAX_PER_EPISODE, count if count is not None else per_episode()))
    if not weights or not count:
        return []
    given = {name: 0 for name in weights}
    for _ in range(count):
        name = max(weights, key=lambda key: (weights[key] / (given[key] + 1), -ORDER.index(key)))
        given[name] += 1
    return [name for name in ORDER for _ in range(given.get(name, 0))]


# ── Timing ──────────────────────────────────────────────────────────────────

def shorts_wpm() -> float:
    """Words per minute of the Shorts voice: the episode's measured rate scaled by the speed ratio."""
    from agents import attention
    episode_speed = float(getattr(config, "TTS_LOCAL_SPEED", 0.88) or 0.88)
    return attention.wpm() * float(getattr(config, "SHORTS_TTS_SPEED", 0.95)) / episode_speed


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9'’-]+", str(text or ""))


def spoken_text(item: dict) -> str:
    """What the voice says: the script, plus the CTA for the formats that speak it."""
    script = str(item.get("script", "")).strip()
    if FORMATS.get(item.get("format"), {}).get("spoken_cta") and str(item.get("cta_text", "")).strip():
        return f"{script} {str(item['cta_text']).strip()}"
    return script


def estimate_seconds(item: dict, wpm: float | None = None) -> float:
    """Spoken words at the Shorts rate, the short sentence pauses, the 0.3 s opening accent and the end card."""
    from agents.attention import split_sentences
    text = spoken_text(item)
    pause = float((getattr(config, "SHORTS_PAUSES", None) or {}).get("sentence", 0.15))
    sentences = split_sentences(text)
    speech = len(_words(text)) / float(wpm or shorts_wpm()) * 60.0
    return round(0.3 + speech + pause * max(0, len(sentences) - 1) + (0.0 if item.get("format") == "line" else 1.5), 1)


# ── Contract and structure ──────────────────────────────────────────────────

def contract(formats: list[str], narrative_type: str = "") -> str:
    lines = "\n".join(f"- {name} ({FORMATS[name]['seconds'][0]}-{FORMATS[name]['seconds'][1]} s spoken): {FORMATS[name]['brief']}"
                      for name in dict.fromkeys(formats))
    question = "Who was right?" if narrative_type == "realistic_psychological_fiction" else "What would you do?"
    return f"""You write YouTube Shorts for Private Gravity (cinematic psychological fiction, one narrator voice).
Each Short is its OWN script derived from the episode below, not a cut of its narration. Write {len(formats)} scripts,
in this order of formats: {formats}.
FORMATS:
{lines}
RULES FOR EVERY SCRIPT:
- TRUE TO THE EPISODE: no event, line or outcome that does not happen in it. Cite it: "source_scene_ids" (ids from
  SCENES) and "evidence_quotes" (1-3 exact phrases copied from those scenes' narration).
- NEVER reveal the final twist or the ending (the WITHHELD and ENDING notes). Only scenes before the reversal may be
  used; the cold-open scene is allowed.
- The FIRST spoken words ARE the hook: the script starts with "hook_line" (max {HOOK_WORDS} words). Never open on
  scene-setting ("In a town...", "Once...", "There was...").
- Spoken English for the ear: sentences of {SENTENCE_AVG} words or fewer on average, none over {SENTENCE_MAX}.
- "script": {WORDS[0]}-{WORDS[1]} words. Present tense where it fits.
- "on_screen_hook": max {SCREEN_HOOK_WORDS} words shown in the first 1.5 s (different words from the title).
- "cta_text": max {CTA_WORDS} words; for spoken formats the narrator says it last ("The full story is on the
  channel."); for dilemma it invites a comment. Never promise a "Part 2".
- "title": a curiosity hook naming the concept (thought experiment, parable, a person and a situation), max
  {TITLE_MAX} characters, no hashtags (#Shorts is added), no colon subtitle, never the theme word.
- "description_line": one line, no spoilers.
- dilemma only: "question" (the dilemma question; for this type: "{question}").
Return ONLY JSON {{"shorts": [{{"format", "hook_line", "script", "on_screen_hook", "cta_text", "title",
"description_line", "question", "source_scene_ids": [..], "evidence_quotes": [..]}}]}}."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", str(text or "").lower().replace("’", "'"))).strip()


def validate(value, formats: list[str], sections: list[dict]) -> list[dict]:
    """The structure: every field, the formats in order, scene ids and evidence quotes that exist."""
    items = value.get("shorts") if isinstance(value, dict) else None
    if not isinstance(items, list) or len(items) != len(formats):
        raise NarrativeValidationError(f"shorts needs exactly {len(formats)} scripts ({formats})")
    by_id = {str(section.get("id")): section for section in sections}
    problems, clean = [], []
    for number, (item, wanted) in enumerate(zip(items, formats), 1):
        label = f"short {number} ({wanted})"
        if not isinstance(item, dict):
            problems.append(f"{label}: not an object")
            continue
        if item.get("format") != wanted:
            problems.append(f"{label}: format must be {wanted!r}")
        for key in ("hook_line", "script", "on_screen_hook", "cta_text", "title", "description_line"):
            if not str(item.get(key, "")).strip():
                problems.append(f"{label}: {key} is empty")
        if wanted == "dilemma" and "?" not in str(item.get("question", "")):
            problems.append(f"{label}: question must be a question")
        ids = [str(scene) for scene in item.get("source_scene_ids") or []]
        unknown = [scene for scene in ids if scene not in by_id]
        if not ids or unknown:
            problems.append(f"{label}: source_scene_ids {unknown or '[]'} are not scene ids")
        quotes = [str(quote).strip() for quote in item.get("evidence_quotes") or [] if str(quote).strip()]
        narration = " ".join(_norm(by_id[scene].get("narration", "")) for scene in ids if scene in by_id)
        missing = [quote for quote in quotes if _norm(quote) not in narration]
        if not quotes or missing:
            problems.append(f"{label}: evidence_quotes must be copied from the cited scenes ({missing[:2] or 'none given'})")
        clean.append({"format": wanted, **{key: str(item.get(key, "")).strip() for key in
                                           ("hook_line", "script", "on_screen_hook", "cta_text", "title", "description_line", "question")},
                      "source_scene_ids": ids, "evidence_quotes": quotes})
    if problems:
        raise NarrativeValidationError("shorts scripts: " + "; ".join(problems))
    return clean


# ── Deterministic checks ────────────────────────────────────────────────────

def story_guard(script: dict, outline: dict | None = None) -> dict:
    """What a Short must not reveal: the scenes from the reversal on (ids), the cold open's withheld words and the
    ending's words."""
    from agents import cold_open
    from video.shorts import TWIST_TYPES
    sections = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
    twist_index = next((index for index, section in enumerate(sections)
                        if str(section.get("section_type", "")).lower() in TWIST_TYPES), len(sections))
    teaser = cold_open.plan(outline)
    own = cold_open.content_words(" ".join(str((teaser or {}).get(key, "")) for key in ("title", "summary", "setting")))
    ending = sections[-1].get("narration", "") if sections and twist_index < len(sections) else ""
    return {"forbidden_scenes": [str(section.get("id")) for section in sections[twist_index:]],
            "cold_open_id": str(sections[0].get("id")) if sections and sections[0].get("section_type") == "cold_open" else "",
            "withheld": sorted(cold_open.content_words((teaser or {}).get("withheld", "")) - own),
            "ending": sorted(cold_open.content_words(ending)),
            "twist_sentences": [line for section in sections[twist_index:] for line in _sentences(section.get("narration", ""))]}


def _sentences(text: str) -> list[str]:
    from agents.attention import split_sentences
    return split_sentences(text)


def _check(code: str, ok: bool, message: str) -> dict:
    return {"code": code, "ok": bool(ok), "message": "" if ok else message}


def reveals(item: dict, guard: dict) -> list[str]:
    """Why a script gives away the twist or the ending (empty: it does not)."""
    from agents import cold_open
    found = []
    used = [scene for scene in item.get("source_scene_ids", []) if scene in guard.get("forbidden_scenes", [])
            and scene != guard.get("cold_open_id")]
    if used:
        found.append(f"uses scenes from the reversal on ({', '.join(used)})")
    words = cold_open.content_words(item.get("script", "") + " " + item.get("on_screen_hook", "") + " " + item.get("title", ""))
    withheld = set(guard.get("withheld") or [])
    named = sorted(words & withheld)
    if withheld and (len(named) >= 2 or len(named) / len(withheld) >= 0.5):
        found.append(f"names what the cold open withholds ({', '.join(named)})")
    ending = set(guard.get("ending") or [])
    shared = sorted(words & ending)
    if ending and len(shared) >= max(5, len(ending) // 3):
        found.append(f"shares many words with the ending ({', '.join(shared[:6])})")
    for line in guard.get("twist_sentences") or []:
        if len(_words(line)) >= 5 and _norm(line) in _norm(item.get("script", "")):
            found.append(f"repeats a twist sentence ({line[:60]})")
            break
    return found


def checks(item: dict, *, guard: dict, brief: dict | None = None, narrative_type: str = "", wpm: float | None = None) -> list[dict]:
    """Every deterministic rule as {code, ok, message}."""
    from agents import topic
    script = str(item.get("script", "")).strip()
    sentences = _sentences(script)
    lengths = [len(_words(line)) for line in sentences] or [0]
    hook = _norm(item.get("hook_line", ""))
    first = _norm(script)
    low, high = FORMATS[item["format"]]["seconds"]
    seconds = estimate_seconds(item, wpm)
    title = short_title(item.get("title", ""))
    bare = re.sub(r"\s*#shorts\b", "", title, flags=re.I).strip()
    screen = set(_norm(item.get("on_screen_hook", "")).split()) - {"the", "a", "an", "of", "to", "pov", "his", "her"}
    title_words = set(_norm(bare).split())
    found = [
        _check("hook_first", bool(hook) and first.startswith(hook) and not OPENERS.match(script),
               "the first spoken words must be the hook line, never scene-setting"),
        _check("hook_length", 0 < len(hook.split()) <= HOOK_WORDS, f"hook_line must be 1-{HOOK_WORDS} words"),
        _check("screen_hook", 0 < len(_norm(item.get("on_screen_hook", "")).split()) <= SCREEN_HOOK_WORDS,
               f"on_screen_hook must be 1-{SCREEN_HOOK_WORDS} words"),
        _check("words", WORDS[0] <= len(_words(script)) <= WORDS[1], f"script must be {WORDS[0]}-{WORDS[1]} words ({len(_words(script))})"),
        _check("sentences", sum(lengths) / len(lengths) <= SENTENCE_AVG and max(lengths) <= SENTENCE_MAX,
               f"sentences must average ≤ {SENTENCE_AVG} words, none over {SENTENCE_MAX} (avg {sum(lengths) / len(lengths):.1f}, max {max(lengths)})"),
        _check("duration", low <= seconds <= high + 2, f"{seconds:.0f} s estimated, the format needs {low}-{high} s"),
        _check("cta", 0 < len(_words(item.get("cta_text", ""))) <= CTA_WORDS and "part 2" not in _norm(item.get("cta_text", "")),
               f"the CTA must be 1-{CTA_WORDS} words and never a 'Part 2'"),
    ]
    spoiled = reveals(item, guard)
    found.append(_check("no_twist", not spoiled, "; ".join(spoiled)))
    title_ok = bool(bare) and len(bare) <= TITLE_MAX and title_passes(validate_title(title, brief or {}))
    found.append(_check("title", title_ok, f"the title must pass the title policy and be ≤ {TITLE_MAX} characters"))
    found.append(_check("title_topic", bool(topic.found_concepts(bare + " " + item.get("description_line", ""), narrative_type,
                                                                 (brief or {}).get("topic_words"))),
                        "the title or description line must name a searchable concept"))
    found.append(_check("title_vs_hook", not screen or len(screen & title_words) / len(screen) < 0.6,
                        "the title repeats the on-screen hook"))
    ending = sentences[-1] if sentences else ""
    if item["format"] == "hook":
        found.append(_check("ending", ending.endswith("?"), "the Hook ends on the unanswered question"))
    elif item["format"] == "countdown":
        found.append(_check("ending", not ending.endswith("!"), "the Countdown stops one beat before the payoff"))
    elif item["format"] == "dilemma":
        found.append(_check("question", "?" in item.get("question", "") and any(word in _norm(item.get("cta_text", "") + " " + item.get("question", ""))
                                                                              for word in COMMENT_WORDS),
                            "the Dilemma asks its question and invites a comment"))
    elif item["format"] == "pov":
        found.append(_check("pov", _norm(item.get("on_screen_hook", "")).startswith("pov") and
                            re.search(r"\byou(?:r|'re)?\b", script, re.I) is not None,
                            "the POV hook starts with 'POV:' and the script speaks to 'you'"))
    return found


def failed(found: list[dict]) -> list[str]:
    return [item["message"] for item in found if not item["ok"]]


def short_title(title: str) -> str:
    from video.shorts import short_title as with_tag
    return with_tag(title)


# ── Swipe simulation ────────────────────────────────────────────────────────

def blocks(item: dict, wpm: float | None = None) -> list[str]:
    """The spoken text cut into 3-second blocks (by words at the Shorts rate)."""
    words = str(spoken_text(item)).split()
    per_block = max(1, round(float(wpm or shorts_wpm()) / 60.0 * BLOCK_SECONDS))
    return [" ".join(words[index:index + per_block]) for index in range(0, len(words), per_block)]


def sim_contract() -> str:
    return """You are "the swiper": 19-30, thumb on the screen, scrolling Shorts late at night. You have seen
every trick and you swipe the instant nothing pulls you in. For each SHORT, read its blocks one at a time (each
block is 3 seconds of voice; ON_SCREEN is the text shown in the first 1.5 s) and answer, for EVERY block, whether you
swipe away now and why (one short reason). For format "line" also say whether the loop (last words flowing back
into the first) feels natural. Judge honestly; most Shorts lose people early.
Return ONLY JSON {"verdicts": [{"id": "...", "blocks": [{"swipe": true|false, "reason": "..."}],
"loop_natural": true|false|null}]}."""


def validate_sim(value, items: list[dict], block_map: dict) -> dict:
    verdicts = value.get("verdicts") if isinstance(value, dict) else None
    if not isinstance(verdicts, list):
        raise NarrativeValidationError("swipe simulation needs a verdicts array")
    by_id = {str(verdict.get("id")): verdict for verdict in verdicts if isinstance(verdict, dict)}
    found = {}
    for item in items:
        verdict = by_id.get(item["id"])
        wanted = len(block_map[item["id"]])
        rows = (verdict or {}).get("blocks")
        if not isinstance(rows, list) or len(rows) != wanted:
            raise NarrativeValidationError(f"{item['id']}: needs {wanted} block verdicts")
        found[item["id"]] = {"blocks": [{"swipe": bool(row.get("swipe")), "reason": str(row.get("reason", "")).strip()[:160]}
                                        for row in rows],
                             "loop_natural": verdict.get("loop_natural") if item["format"] == "line" else None}
    return found


def gate(item: dict, verdict: dict | None) -> list[str]:
    """No swipe in the first 3 s; at most one swipe block before the end; a natural loop for The Line."""
    if not verdict:
        return ["no swipe simulation"]
    rows = verdict.get("blocks") or []
    reasons = []
    if rows and rows[0]["swipe"]:
        reasons.append(f"swipe in the first 3 s: {rows[0]['reason']}")
    early = [row for row in rows[:-1] if row["swipe"]]
    if len(early) > 1:
        reasons.append(f"{len(early)} swipe blocks before the end: " + "; ".join(row["reason"] for row in early[:2]))
    if item["format"] == "line" and verdict.get("loop_natural") is not True:
        reasons.append("the loop does not feel natural")
    return reasons


def simulate(items: list[dict], *, generator=None, run_dir=None, wpm: float | None = None) -> dict:
    """{id: verdict} from one call for every script."""
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    if not items:
        return {}
    block_map = {item["id"]: blocks(item, wpm) for item in items}
    listing = [{"id": item["id"], "format": item["format"], "ON_SCREEN": item.get("on_screen_hook", ""),
                "blocks": block_map[item["id"]]} for item in items]
    return generate_json(sim_contract(), "shorts swipe simulation", lambda value: validate_sim(value, items, block_map),
                         generator=generator or generate, context=f"SHORTS: {compact_json(listing)}", stage="shorts_swipe",
                         temperature_key="editorial_review", run_dir=str(run_dir) if run_dir else None)


# ── The plan ────────────────────────────────────────────────────────────────

FOLDER = "shorts"
PLAN_FILE = "shorts_plan.json"


def _context(script: dict, brief: dict, guard: dict) -> str:
    from agents.structured import compact_json
    scenes = [{"id": section.get("id"), "type": section.get("section_type", ""), "narration": section.get("narration", "")}
              for section in script.get("sections", []) if str(section.get("narration", "")).strip()
              and str(section.get("id")) not in guard["forbidden_scenes"]]
    story = {key: brief.get(key, "") for key in ("logline", "premise", "psychological_theme", "narrative_type", "topic_words")}
    notes = {"WITHHELD": " ".join(guard["withheld"]), "ENDING (never reveal)": "the last scene and the reversal are not shown"}
    return f"STORY: {compact_json(story)}\nNOTES: {compact_json(notes)}\nSCENES: {compact_json(scenes)}"


def _finish(item: dict, number: int, *, guard: dict, brief: dict, narrative_type: str, wpm: float) -> dict:
    found = checks(item, guard=guard, brief=brief, narrative_type=narrative_type, wpm=wpm)
    return {**item, "id": item.get("id") or f"short_{number:02d}", "title": short_title(item["title"]),
            "description": item.get("description_line", ""), "estimate_seconds": estimate_seconds(item, wpm),
            "checks": found, "include": item.get("include", True)}


def evaluate(items: list[dict], verdicts: dict) -> list[dict]:
    for item in items:
        item["swipe"] = verdicts.get(item["id"], item.get("swipe"))
        item["reasons"] = failed(item["checks"]) + gate(item, item.get("swipe"))
        item["status"] = "ready" if not item["reasons"] else "review"
    return items


def rewrite_contract(formats: list[str], narrative_type: str) -> str:
    return contract(formats, narrative_type) + """
These scripts FAILED review. Rewrite each one to fix every REASON listed with it, keeping its format and staying
true to the episode."""


def build(output_dir, *, script: dict | None = None, brief: dict | None = None, outline: dict | None = None,
          generator=None, simulator=None, force: bool = False, formats: list[str] | None = None) -> dict:
    """One call for every script, the checks, the swipe gate, one targeted rewrite; saved as the Shorts plan."""
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    output = Path(output_dir)
    script = script or load_json(output / "script.json") or {}
    brief = brief or load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    outline = outline or load_json(output / "story_outline.json") or {}
    sections = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
    if not sections:
        raise FileNotFoundError("no screenplay: the Shorts scripts are derived from script.json")
    kind = script.get("narrative_type") or brief.get("narrative_type", "")
    formats = formats or allocate()
    wpm = shorts_wpm()
    guard = story_guard(script, outline)
    source = stable_hash({"narration": [section.get("narration") for section in sections], "formats": formats,
                          "version": VERSION})
    path = output / FOLDER / PLAN_FILE
    saved = load_json(path) or {}
    if saved.get("mode") == "formats" and saved.get("source_hash") == source and not force:
        return saved
    generator = generator or generate
    context = _context(script, brief, guard)
    raw = generate_json(contract(formats, kind), "shorts scripts", lambda value: validate(value, formats, sections),
                        generator=generator, context=context, stage="shorts_scripts", temperature_key="packaging",
                        run_dir=str(output))
    items = [_finish(item, number, guard=guard, brief=brief, narrative_type=kind, wpm=wpm) for number, item in enumerate(raw, 1)]
    simulator = simulator or (lambda chosen: simulate(chosen, generator=generator, run_dir=output, wpm=wpm))
    evaluate(items, simulator(items))
    failing = [item for item in items if item["status"] == "review"]
    if failing:                                           # one targeted rewrite round
        wanted = [item["format"] for item in failing]
        notes = [{"format": item["format"], "script": item["script"], "hook_line": item["hook_line"], "title": item["title"],
                  "REASONS": item["reasons"]} for item in failing]
        try:
            again = generate_json(rewrite_contract(wanted, kind), "shorts rewrite", lambda value: validate(value, wanted, sections),
                                  generator=generator, context=context + f"\nFAILED: {compact_json(notes)}",
                                  stage="shorts_rewrite", temperature_key="packaging", run_dir=str(output))
        except NarrativeValidationError as exc:
            again = []
            for item in failing:
                item["reasons"].append(f"rewrite failed: {str(exc)[:160]}")
        rewritten = []
        for old, new in zip(failing, again):
            fresh = _finish({**new, "id": old["id"]}, 0, guard=guard, brief=brief, narrative_type=kind, wpm=wpm)
            fresh["versions"] = [{key: old[key] for key in ("hook_line", "script", "title", "reasons")}]
            fresh["rewritten"] = True
            rewritten.append(fresh)
        if rewritten:
            evaluate(rewritten, simulator(rewritten))
            by_id = {item["id"]: item for item in rewritten}
            items = [by_id.get(item["id"], item) for item in items]
    plan = {"mode": "formats", "version": VERSION, "source_hash": source, "formats": formats, "wpm": round(wpm, 1),
            "guard": {key: guard[key] for key in ("forbidden_scenes", "withheld")}, "segments": items, "edited": False}
    atomic_write_json(path, plan)
    return plan


def regenerate(output_dir, short_id: str, *, generator=None, simulator=None) -> dict:
    """"Gerar outra versão": a new script of the same format for one Short (the old one kept in its versions)."""
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    output = Path(output_dir)
    plan = load_json(output / FOLDER / PLAN_FILE) or {}
    old = next((item for item in plan.get("segments", []) if item.get("id") == short_id), None)
    if old is None or plan.get("mode") != "formats":
        raise LookupError(f"{short_id} is not a format Short")
    script = load_json(output / "script.json") or {}
    brief = load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    sections = [section for section in script.get("sections", []) if str(section.get("narration", "")).strip()]
    kind = script.get("narrative_type") or brief.get("narrative_type", "")
    guard = story_guard(script, load_json(output / "story_outline.json") or {})
    wpm = shorts_wpm()
    generator = generator or generate
    others = [item["script"] for item in plan["segments"] if item["id"] != short_id]
    raw = generate_json(contract([old["format"]], kind) + "\nWrite a NEW version, different from PREVIOUS and from the OTHER Shorts.",
                        f"shorts version {short_id}", lambda value: validate(value, [old["format"]], sections),
                        generator=generator, context=_context(script, brief, guard) + f"\nPREVIOUS: {compact_json(old['script'])}"
                        f"\nOTHER: {compact_json(others)}", stage="shorts_version", temperature_key="packaging", run_dir=str(output))
    fresh = _finish({**raw[0], "id": short_id}, 0, guard=guard, brief=brief, narrative_type=kind, wpm=wpm)
    fresh["versions"] = (old.get("versions") or []) + [{key: old.get(key) for key in ("hook_line", "script", "title", "reasons")}]
    simulator = simulator or (lambda chosen: simulate(chosen, generator=generator, run_dir=output, wpm=wpm))
    evaluate([fresh], simulator([fresh]))
    plan["segments"] = [fresh if item["id"] == short_id else item for item in plan["segments"]]
    atomic_write_json(output / FOLDER / PLAN_FILE, plan)
    return fresh


EDITABLE = ("hook_line", "script", "on_screen_hook", "cta_text", "title", "description_line", "question", "include")


def save(output_dir, edits: list[dict]) -> dict:
    """The user's text edits, re-checked by code; the swipe verdict of an edited script is marked stale."""
    output = Path(output_dir)
    plan = load_json(output / FOLDER / PLAN_FILE) or {}
    if plan.get("mode") != "formats":
        raise LookupError("no format Shorts plan")
    script = load_json(output / "script.json") or {}
    brief = load_json(output / "story_brief.json") or load_json(output / "research.json") or {}
    kind = script.get("narrative_type") or brief.get("narrative_type", "")
    guard = story_guard(script, load_json(output / "story_outline.json") or {})
    wpm = shorts_wpm()
    changes = {str(edit.get("id")): edit for edit in edits or [] if isinstance(edit, dict)}
    updated = []
    for item in plan.get("segments", []):
        edit = changes.get(item["id"])
        if not edit:
            updated.append(item)
            continue
        text_changed = any(key in edit and str(edit[key]).strip() != str(item.get(key, "")).strip()
                           for key in EDITABLE if key != "include")
        merged = {**item, **{key: (bool(edit[key]) if key == "include" else str(edit[key]).strip())
                             for key in EDITABLE if key in edit}}
        merged = _finish(merged, 0, guard=guard, brief=brief, narrative_type=kind, wpm=wpm)
        if text_changed:
            merged["swipe"] = {**(item.get("swipe") or {}), "stale": True}
            merged["edited"] = True
        stale = (merged.get("swipe") or {}).get("stale")
        merged["reasons"] = failed(merged["checks"]) + (["the text changed: simulate again"] if stale
                                                        else gate(merged, merged.get("swipe")))
        merged["status"] = "ready" if not merged["reasons"] else "review"
        updated.append(merged)
    plan.update(segments=updated, edited=True)
    atomic_write_json(output / FOLDER / PLAN_FILE, plan)
    return plan


def resimulate(output_dir, ids: list[str] | None = None, *, generator=None, simulator=None) -> dict:
    output = Path(output_dir)
    plan = load_json(output / FOLDER / PLAN_FILE) or {}
    chosen = [item for item in plan.get("segments", []) if not ids or item["id"] in ids]
    simulator = simulator or (lambda items: simulate(items, generator=generator, run_dir=output))
    verdicts = simulator(chosen)
    for item in chosen:
        item["swipe"] = verdicts.get(item["id"])
        item["reasons"] = failed(item["checks"]) + gate(item, item["swipe"])
        item["status"] = "ready" if not item["reasons"] else "review"
    atomic_write_json(output / FOLDER / PLAN_FILE, plan)
    return plan
