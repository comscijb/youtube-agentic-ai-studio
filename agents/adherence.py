"""Images that follow the narration (Phase 3.9.9): per-shot locations (A) and the prompt-vs-narration check (C).

**A.** The shot planner used to give every shot its scene's location: in "She Filmed Every Moment", the drive home of
scene 4 was planned in ``supermarket_aisle``, so Klein got a supermarket as the location reference while the text said
"in the car", and Leo ended up outside the car. Here each shot's location follows its OWN narration excerpt (plus the
previous shot for continuity): location keywords in the excerpt must match the chosen location; when they do not, the
shot moves to a location that matches, created from a template when the visual bible has none (``car_interior``, an
enclosed space with a "who sits where" note). A location without an approved reference goes text-only, which beats a
wrong reference.

**C.** Before rendering, each composed prompt is compared with its excerpt and the staging sheet (deterministic pre-checks
plus one low-temperature Gemini call per scene); fixes go into the shot spec automatically, with a log.
"""
from __future__ import annotations

import json
import re

# category -> (words that place a moment there, template for a location the visual bible lacks)
CATEGORIES = {
    "car": (("car", "cars", "driving", "drive home", "drove", "steering wheel", "passenger seat", "passenger window", "driver's seat",
             "rearview", "rear-view", "windshield", "windscreen", "dashboard", "seatbelt", "seat belt", "back seat", "backseat"),
            {"location_id": "car_interior", "name": "Car interior", "interior": True, "enclosed": True,
             "descriptor": "the interior of a family car seen from inside: two front seats, the steering wheel and dashboard, "
                           "side windows and the windshield",
             "seating": "the driver in the front left seat with hands on the wheel, a passenger in the front right seat beside them"}),
    "kitchen": (("kitchen", "stove", "oven", "fridge", "refrigerator", "kitchen island", "kitchen counter", "sink"),
                {"location_id": "kitchen", "name": "Kitchen", "interior": True, "descriptor": "a home kitchen with counters, cabinets and an island"}),
    "supermarket": (("supermarket", "aisle", "cereal", "checkout", "shopping cart", "trolley", "grocery", "groceries", "store shelves"),
                    {"location_id": "supermarket_aisle", "name": "Supermarket aisle", "interior": True,
                     "descriptor": "a supermarket aisle between tall shelves of packaged goods under fluorescent light"}),
    "hallway": (("hallway", "corridor", "staircase", "stairs", "front door"),
                {"location_id": "hallway", "name": "Hallway", "interior": True, "descriptor": "a narrow house hallway with doors and a staircase"}),
    "bedroom": (("bedroom", "bed", "pillow", "duvet"),
                {"location_id": "bedroom", "name": "Bedroom", "interior": True, "descriptor": "a small bedroom with a bed and a window"}),
    "office": (("office", "editing desk", "monitors", "monitor", "laptop", "desk"),
               {"location_id": "home_office", "name": "Home office", "interior": True, "descriptor": "a home office with a desk and two monitors"}),
    "outside": (("outside", "street", "sidewalk", "pavement", "front yard", "garden", "driveway", "porch", "lawn", "lane"),
                {"location_id": "street", "name": "Street", "interior": False, "descriptor": "a quiet suburban street with houses and trees"}),
}
INSIDE_CONTRADICTIONS = {"car": re.compile(r"\b(?:framed from outside|seen from outside|outside the car|standing (?:beside|next to) the car|"
                                           r"on the (?:street|sidewalk|road) outside)\b", re.I)}
SEATED = re.compile(r"\b(?:sits?|sitting|seated|in the (?:driver's|passenger) seat|behind the wheel)\b", re.I)
STANDING = re.compile(r"\b(?:stands?|standing|walks?|walking|strides?)\b", re.I)


STRONG = {"car": ("in the car", "drive home", "steering wheel", "passenger seat", "driver's seat", "rearview", "rear-view",
                  "passenger window", "windshield", "dashboard", "behind the wheel")}
PLACE_PREPOSITION = r"(?:in|into|inside|back in|back into|across|through) the"
MEMORY = re.compile(r"\b(?:had|remembered|remembers|used to|years ago|when (?:he|she|they) (?:was|were)|back then|once|every|the first)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", str(text or "")) if part.strip()]


def strong(text: str, wanted: str) -> list[str]:
    """Evidence that the moment happens there NOW: a strong phrase (vehicles) or "in the <place>", in a sentence that
    is not a memory. "the first day of kindergarten, every tantrum in the hallway" and "she had left her phone on the
    kitchen counter" are memories and only ask for a review."""
    words = CATEGORIES[wanted][0]
    found = []
    for sentence in _sentences(text):
        if MEMORY.search(sentence):
            continue
        lowered = sentence.lower()
        found += [phrase for phrase in STRONG.get(wanted, ()) if phrase in lowered]
        found += [word for word in words if re.search(rf"\b{PLACE_PREPOSITION} {re.escape(word)}\b", lowered)]
    return list(dict.fromkeys(found))


def _hits(text: str, words) -> int:
    lowered = str(text or "").lower()
    return sum(1 for word in words if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered))


def category(text: str) -> str | None:
    """The place the text puts the moment in, or None (most keyword hits; ties keep table order)."""
    scored = [(_hits(text, words), name) for name, (words, _) in CATEGORIES.items()]
    best = max(scored, key=lambda item: item[0])
    return best[1] if best[0] else None


def location_text(location: dict) -> str:
    return " ".join(str(location.get(key, "")) for key in ("location_id", "name", "descriptor", "category")).replace("_", " ")


def matches(location: dict | None, wanted: str) -> bool:
    return bool(location) and (location.get("category") == wanted or _hits(location_text(location), CATEGORIES[wanted][0]) > 0)


def mismatch(shot: dict, vb: dict) -> dict | None:
    """{category, location, evidence} when the shot's own excerpt places it somewhere its location is not."""
    wanted = category(shot.get("narration_excerpt", ""))
    location = next((item for item in vb.get("locations", []) if item.get("location_id") == shot.get("location")), None)
    if wanted is None or matches(location, wanted):
        return None
    words = [word for word in CATEGORIES[wanted][0] if _hits(shot.get("narration_excerpt", ""), [word])]
    evidence = strong(shot.get("narration_excerpt", ""), wanted)
    return {"category": wanted, "location": shot.get("location"), "evidence": evidence or words, "strong": bool(evidence)}


def contradictions(shot: dict, vb: dict, staging_phrase: str = "") -> list[str]:
    """Deterministic: the drawn action against where the excerpt and the location put the moment."""
    found = []
    location = next((item for item in vb.get("locations", []) if item.get("location_id") == shot.get("location")), {})
    place = "car" if location.get("enclosed") and matches(location, "car") else category(shot.get("narration_excerpt", ""))
    action = str(shot.get("action", ""))
    if place in INSIDE_CONTRADICTIONS and INSIDE_CONTRADICTIONS[place].search(action):
        found.append(f"inside/outside: the narration is inside the {place}, the action says \"{INSIDE_CONTRADICTIONS[place].search(action).group(0)}\"")
    seated = SEATED.search(shot.get("narration_excerpt", "") + " " + staging_phrase)
    if seated and STANDING.search(action) and not SEATED.search(action):
        found.append(f"seated/standing: the narration or staging has \"{seated.group(0)}\", the action has \"{STANDING.search(action).group(0)}\"")
    return found


def find_or_create(vb: dict, wanted: str, scene_id: str) -> tuple[dict, bool]:
    """A visual-bible location of that category (the scene's own first), else a new one from the template."""
    candidates = [item for item in vb.get("locations", []) if matches(item, wanted)]
    candidates.sort(key=lambda item: scene_id not in (item.get("scene_ids") or []))
    if candidates:
        chosen = candidates[0]
        created = False
    else:
        template = dict(CATEGORIES[wanted][1])
        used = {item.get("location_id") for item in vb.get("locations", [])}
        base, number = template["location_id"], 2
        while template["location_id"] in used:
            template["location_id"] = f"{base}_{number}"
            number += 1
        chosen = {**template, "category": wanted, "scene_ids": [], "beat_level": True}
        vb.setdefault("locations", []).append(chosen)
        created = True
    if scene_id and scene_id not in chosen.setdefault("scene_ids", []):
        chosen["scene_ids"].append(scene_id)
    return chosen, created


def relocate(shots: list[dict], vb: dict, *, log=print) -> list[dict]:
    """Each shot's location from its own excerpt. Strong evidence moves the shot; weak evidence (a single place word, a
    memory) only marks it for review. A shot with no place words follows the previous shot of the same scene when that
    one was moved into an enclosed space (the drive home goes on while they talk). Returns the changes."""
    changes, carried = [], {}
    for shot in shots:
        scene = shot.get("scene_id", "")
        found = mismatch(shot, vb)
        if found and found["strong"]:
            chosen, created = find_or_create(vb, found["category"], scene)
            change = {"shot_id": shot["shot_id"], "from": shot.get("location"), "to": chosen["location_id"],
                      "evidence": found["evidence"], "created": created}
            if chosen.get("enclosed"):
                carried[scene] = chosen["location_id"]
            else:
                carried.pop(scene, None)
        elif found:
            shot["location_review"] = f"a narração fala de {found['category']} ({', '.join(found['evidence'])}); local {found['location']}"
            continue
        elif category(shot.get("narration_excerpt", "")) is None and scene in carried and shot.get("location") != carried[scene]:
            change = {"shot_id": shot["shot_id"], "from": shot.get("location"), "to": carried[scene], "evidence": ["continuity"], "created": False}
        else:
            if category(shot.get("narration_excerpt", "")):
                carried.pop(scene, None)
            continue
        shot["location_relocated_from"] = shot.get("location")
        shot["location"] = change["to"]
        shot["setting_condition"] = ""                     # it described the old place ("a quiet retail aisle...")
        changes.append(change)
        log(f"   -> {shot['shot_id']}: location {change['from']} -> {change['to']} ({', '.join(change['evidence'])})"
            + (" [new location, text-only until a reference is approved]" if change["created"] else ""))
    return changes


def report(shots: list[dict], vb: dict) -> dict:
    """What ``relocate`` would change, what needs a review and the contradictions left after it (scripts.check_adherence);
    the inputs are not changed."""
    from copy import deepcopy
    moved_shots, moved_vb = deepcopy(shots), deepcopy(vb)
    changes = relocate(moved_shots, moved_vb, log=lambda _: None)
    return {"relocations": changes,
            "review": [{"shot_id": shot["shot_id"], "note": shot["location_review"]} for shot in moved_shots if shot.get("location_review")],
            "contradictions": [{"shot_id": shot["shot_id"], "problems": problems} for shot in moved_shots
                               for problems in [contradictions(shot, moved_vb)] if problems],
            "new_locations": [item["location_id"] for item in moved_vb.get("locations", []) if item.get("beat_level")]}


# ── C. Prompt vs narration ──────────────────────────────────────────────────

UNNAMED = re.compile(r"\b(?:stranger|strangers|shopper|woman|man|people|crowd|guests?|customers?|someone|passers?-?by|"
                     r"waiter|waitress|clerk|neighbou?r)\b", re.I)
PROBLEM_KINDS = ("presence", "location", "position", "prop", "action", "contradiction")


def check_contract() -> str:
    return ("You check an illustrated film's shot prompts against the narration BEFORE any image is drawn. For each SHOT "
            "below compare its PROMPT with its NARRATION excerpt and its STAGING (who is where, seated or standing, inside or "
            "outside). Report only what contradicts the narration or leaves it out in a way that changes the picture: who is "
            "present or absent, the location, positions and relations (inside the car, behind the table), key props and "
            "actions, outside vs inside, standing vs seated. Return ONLY JSON {\"shots\": [{\"shot_id\": \"...\", \"ok\": true|false, "
            f"\"problems\": [{{\"kind\": one of {list(PROBLEM_KINDS)}, \"evidence_quote\": \"words from the narration\", "
            "\"fix\": \"what the prompt must say\"}], \"suggested_prompt_patch\": \"one short sentence to add to the prompt, or empty\", "
            "\"certain\": true|false}]}.")


def validate_check(value: dict, shot_ids: list[str], narration: dict[str, str]) -> dict:
    from agents.narrative import NarrativeValidationError
    items = value.get("shots") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise NarrativeValidationError("adherence check needs a shots array")
    result = {}
    for item in items:
        sid = item.get("shot_id") if isinstance(item, dict) else None
        if sid not in shot_ids:
            continue
        problems = []
        for problem in item.get("problems") or []:
            quote = str(problem.get("evidence_quote", "")).strip()
            if problem.get("kind") not in PROBLEM_KINDS or not quote:
                continue
            if _norm(quote) not in _norm(narration.get(sid, "")):
                continue                                  # an invented quote is not evidence
            problems.append({"kind": problem["kind"], "evidence_quote": quote, "fix": str(problem.get("fix", ""))[:200]})
        result[sid] = {"ok": not problems, "problems": problems, "suggested_prompt_patch": str(item.get("suggested_prompt_patch") or "")[:240],
                       "certain": bool(item.get("certain", True))}
    return result


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", str(text or "").lower().replace("’", "'"))).strip()


def check_scene(shots: list[dict], prompts: dict[str, str], staging: dict[str, str], *, generator=None, run_dir=None) -> dict:
    """One low-temperature Gemini call for a scene's shots: {shot_id: {ok, problems, suggested_prompt_patch, certain}}."""
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    narration = {shot["shot_id"]: shot.get("narration_excerpt", "") for shot in shots}
    context = compact_json([{"shot_id": shot["shot_id"], "narration": narration[shot["shot_id"]], "prompt": prompts.get(shot["shot_id"], ""),
                             "staging": staging.get(shot["shot_id"], "")} for shot in shots])
    return generate_json(check_contract(), "prompt adherence", lambda value: validate_check(value, list(narration), narration),
                         generator=generator or generate, context=f"SHOTS: {context}", stage="prompt_adherence",
                         temperature_key="editorial_review", run_dir=run_dir)


def apply_check(shot: dict, verdict: dict, vb: dict, *, log=print) -> str:
    """Fix the shot spec from one verdict. Returns "ok", "fixed" or "review" (uncertain: shown before rendering)."""
    if verdict.get("ok"):
        shot["adherence"] = {"status": "ok"}
        return "ok"
    fixed, unsure = [], False
    for problem in verdict.get("problems") or []:
        if problem["kind"] == "presence":
            if UNNAMED.search(problem["evidence_quote"]) and not shot.get("others_present"):
                shot["others_present"] = True                 # the headcount now allows the stranger the text names
                fixed.append("headcount: others_present")
            elif not UNNAMED.search(problem["evidence_quote"]):
                unsure = True                                  # a named character to add or remove: a person decides
        if problem["kind"] == "location":
            wanted = category(problem["evidence_quote"])
            if wanted:
                chosen, _ = find_or_create(vb, wanted, shot.get("scene_id", ""))
                if shot.get("location") != chosen["location_id"]:
                    shot["location_relocated_from"], shot["location"] = shot.get("location"), chosen["location_id"]
                    shot["setting_condition"] = ""
                    fixed.append(f"location -> {chosen['location_id']}")
    patch_text = verdict.get("suggested_prompt_patch", "").strip()
    if patch_text:
        shot["prompt_patch"] = patch_text
        fixed.append("prompt patch")
    status = "fixed" if fixed and verdict.get("certain", True) and not unsure else "review"
    shot["adherence"] = {"status": status, "problems": verdict.get("problems", []), "fixes": fixed}
    log(f"   -> {shot['shot_id']}: adherence {status}: " + "; ".join(f"{item['kind']} \"{item['evidence_quote'][:50]}\"" for item in verdict.get("problems", [])))
    return status


def deterministic_fix(shot: dict, vb: dict, *, log=print) -> list[str]:
    """The fixes code can make alone: an action drawn outside a car the narration is inside, a character the staging
    says is not there. A standing/seated clash is only flagged (rewriting the pose blindly would be a guess)."""
    fixes, review = [], []
    location = next((item for item in vb.get("locations", []) if item.get("location_id") == shot.get("location")), {})
    if location.get("enclosed") and matches(location, "car"):
        found = INSIDE_CONTRADICTIONS["car"].search(str(shot.get("action", "")))
        if found:
            shot["action"] = INSIDE_CONTRADICTIONS["car"].sub("seen inside the car", shot["action"])
            fixes.append(f"action: \"{found.group(0)}\" -> inside the car")
    absent = set((shot.get("staging") or {}).get("absent") or [])
    gone = [cid for cid in shot.get("characters") or [] if cid in absent]
    if gone:
        shot["characters"] = [cid for cid in shot["characters"] if cid not in absent]
        fixes.append(f"headcount: {', '.join(gone)} not in this moment")
    staging_text = " ".join(f"{item.get('posture', '')} {item.get('position', '')}" for item in (shot.get("staging") or {}).get("characters", []))
    review += [problem for problem in contradictions(shot, vb, staging_text) if problem.startswith("seated/standing")]
    if fixes or review:
        previous = shot.get("adherence") or {}
        shot["adherence"] = {"status": "review" if review else "fixed", "fixes": previous.get("fixes", []) + fixes,
                             "problems": previous.get("problems", []) + [{"kind": "contradiction", "evidence_quote": "", "fix": item} for item in review]}
        log(f"   -> {shot['shot_id']}: " + "; ".join(fixes + review))
    return fixes


def estimate_cost(prompt_chars: int, output_chars: int) -> float:
    """From config prices (the user fills them in; 0 means unknown): USD per million tokens, ~4 characters a token."""
    import config
    return round(prompt_chars / 4 / 1e6 * float(getattr(config, "GEMINI_PRICE_INPUT_PER_MTOK", 0) or 0)
                 + output_chars / 4 / 1e6 * float(getattr(config, "GEMINI_PRICE_OUTPUT_PER_MTOK", 0) or 0), 5)


def check_entries(entries: list[dict], vb: dict, output, *, compose, generator=None, log=print) -> dict:
    """C for one scene: deterministic fixes, then one Gemini call; changed shots are composed again.
    Returns {"calls", "prompt_chars", "cost"}."""
    import config
    usage = {"calls": 0, "prompt_chars": 0, "cost": 0.0}
    for entry in entries:
        deterministic_fix(entry, vb, log=log)
    if not getattr(config, "PROMPT_ADHERENCE", True) or not entries:
        return usage
    prompts = {entry["shot_id"]: compose(entry)["prompt"] for entry in entries}
    staged = {entry["shot_id"]: json.dumps(entry.get("staging") or {}, ensure_ascii=False)[:400] for entry in entries}
    try:
        verdicts = check_scene(entries, prompts, staged, generator=generator, run_dir=str(output))
    except Exception as exc:                             # noqa: BLE001 — no Gemini: the deterministic checks stand
        log(f"   ⚠ prompt adherence skipped: {str(exc)[:160]}")
        return usage
    chars = sum(len(text) for text in prompts.values()) + sum(len(str(entry.get("narration_excerpt", ""))) for entry in entries)
    usage.update(calls=1, prompt_chars=chars, cost=estimate_cost(chars, 400 * len(entries)))
    for entry in entries:
        verdict = verdicts.get(entry["shot_id"])
        if verdict and not verdict.get("ok"):
            apply_check(entry, verdict, vb, log=log)
        elif verdict and not entry.get("adherence"):
            entry["adherence"] = {"status": "ok"}
    return usage


def summary_line(shots: list[dict]) -> str:
    fixed = sum(1 for shot in shots if (shot.get("adherence") or {}).get("status") == "fixed")
    review = sum(1 for shot in shots if (shot.get("adherence") or {}).get("status") == "review")
    return f"Aderência do prompt: {fixed} plano(s) ajustado(s), {review} para revisar"


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)
