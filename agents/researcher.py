"""Story Development Agent for Private Gravity."""
from __future__ import annotations

import json
import os
import random
import re
import time

import config
from agents.gemini_client import generate
from agents.narrative import (
    NARRATIVE_TYPES,
    PSYCHOLOGICAL_THEMES,
    NarrativeValidationError,
    atomic_write_json,
    load_json,
    select_title,
    title_passes,
    stable_hash,
    validate_story_brief,
)
from agents.identity import TITLE_POLICY, identity_block
from agents.structured import compact_json, generate_json

_BANNED_TOPICS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "banned_topics.txt"
)
_BUILTIN_BANNED: list[str] = []

# Kept as a public compatibility alias for the existing GUI.
FOCUS_ANGLES = list(PSYCHOLOGICAL_THEMES)


BANNED_FIELDS = ("theme", "angle", "premise", "title", "video", "published")
BANNED_RULE = (
    "Do not reuse a listed premise or the same theme + angle combination; the same broad theme "
    "from a clearly different psychological angle is allowed."
)
_BANNED_HEADER = (
    "# Published topics. Plain lines are premises; automatic lines are written when a video\n"
    "# becomes public: theme: ... | angle: ... | premise: ... | title: ... | video: ... | published: ...\n"
    "# Automatic premise generation must not reuse a premise or the same theme + angle.\n\n"
)


def parse_banned_line(line: str) -> dict:
    """Parse one banned_topics.txt line; plain lines are a premise only."""
    raw = line.strip()
    entry = {"raw": raw}
    fields = {}
    for part in (part.strip() for part in raw.split(" | ")):
        key, sep, value = part.partition(":")
        if not sep or key.strip().lower() not in BANNED_FIELDS:
            fields = {}
            break
        fields[key.strip().lower()] = value.strip()
    if "premise" in fields or "theme" in fields:
        entry.update(fields)
    else:
        entry["premise"] = raw
    return entry


def format_banned_entry(entry: dict) -> str:
    def clean(value):
        return " ".join(str(value or "").replace("|", "/").split())
    return " | ".join(f"{key}: {clean(entry.get(key))}" for key in BANNED_FIELDS
                      if clean(entry.get(key)))


def load_banned_topics() -> list[dict]:
    """Return structured entries; each keeps its original text in ``raw``."""
    entries = [parse_banned_line(topic) for topic in _BUILTIN_BANNED]
    seen = {entry["raw"] for entry in entries}
    try:
        if os.path.exists(_BANNED_TOPICS_FILE):
            with open(_BANNED_TOPICS_FILE, encoding="utf-8") as handle:
                for line in handle:
                    topic = line.strip()
                    if topic and not topic.startswith("#") and topic not in seen:
                        seen.add(topic)
                        entries.append(parse_banned_line(topic))
    except OSError as exc:
        print(f"   WARNING: Could not read banned_topics.txt: {exc}")
    return entries


def save_banned_topics(topics: list) -> None:
    """Rewrite the file from raw lines (strings) or structured entries."""
    with open(_BANNED_TOPICS_FILE, "w", encoding="utf-8") as handle:
        handle.write(_BANNED_HEADER)
        for topic in topics:
            line = (topic.get("raw") or format_banned_entry(topic)) if isinstance(topic, dict) else topic
            if str(line).strip():
                handle.write(str(line).strip() + "\n")


def append_banned_entry(entry: dict) -> bool:
    """Append one published topic unless its video (or the identical line) is listed."""
    line = format_banned_entry(entry)
    video = str(entry.get("video") or "")
    if any(item["raw"] == line or (video and item.get("video") == video)
           for item in load_banned_topics()):
        return False
    exists = os.path.exists(_BANNED_TOPICS_FILE) and os.path.getsize(_BANNED_TOPICS_FILE) > 0
    needs_newline = False
    if exists:
        with open(_BANNED_TOPICS_FILE, "rb") as check:
            check.seek(-1, os.SEEK_END)
            needs_newline = check.read(1) != b"\n"
    with open(_BANNED_TOPICS_FILE, "a", encoding="utf-8") as handle:
        if not exists:
            handle.write(_BANNED_HEADER)
        handle.write(("\n" if needs_newline else "") + line + "\n")
    return True


def _normalize_premise(text) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def published_premise_matches(premise: str, entries: list[dict]) -> list[dict]:
    target = _normalize_premise(premise)
    return [entry for entry in entries if target and target in {
        _normalize_premise(entry.get("premise")), _normalize_premise(entry.get("title"))
    }]


def _banned_for_prompt(entries: list[dict]) -> list[dict]:
    return [{key: entry[key] for key in ("theme", "angle", "premise") if entry.get(key)}
            for entry in entries]


def research_topic(
    channel_description: str,
    topic_override: str = "",
    focus_angle: str = "",
    narrative_type: str = "auto",
    target_duration_minutes: float | None = None,
    reuse_cached: bool = False,
    premise_brief: dict | None = None,
) -> dict:
    """Develop several premises and select one production-ready story brief.

    ``topic_override`` is authoritative: the returned brief always preserves it
    verbatim in ``topic`` and ``requested_premise`` even if the model paraphrases.
    With ``reuse_cached`` a valid ``story_brief.json`` produced by the same
    request in ``config.OUTPUT_DIR`` is returned without an API call.

    ``premise_brief`` (Phase 3.7, ``agents.premise_brief``) is a detailed author brief: it is
    authoritative — provided fields are preserved, Gemini fills only what is missing. The story
    brief records ``requested_brief_hash``, ``brief_coverage`` and ``author_brief`` (the parts the
    bible, outline and scene prompts need).
    """
    from agents import premise_brief as briefs
    author = dict(premise_brief or {})
    if author:
        narrative_type = author["narrative_type"] if author.get("narrative_type") in NARRATIVE_TYPES \
            else narrative_type
        topic_override = topic_override.strip() or briefs.exact_premise(author)
        target_duration_minutes = briefs.target_minutes(author, target_duration_minutes)
    if narrative_type != "auto" and narrative_type not in NARRATIVE_TYPES:
        raise ValueError(f"Unsupported narrative type: {narrative_type}")
    exact = topic_override.strip()
    duration = float(target_duration_minutes or getattr(config, "TARGET_DURATION_MINUTES", 15))
    request = {"premise": exact, "theme": focus_angle.strip(), "narrative_type": narrative_type,
               "duration": duration}
    accepted = []
    if author:
        # Phase 3.7 hashed the saved record with its own "hash" key; that cache key stays valid.
        accepted.append(stable_hash({**request, "brief": stable_hash(
            {key: value for key, value in author.items() if key != "raw"})}))
        request["brief"] = briefs.brief_hash(author)
    request_hash = stable_hash(request)
    brief_path = os.path.join(config.OUTPUT_DIR, "story_brief.json")
    if reuse_cached:
        cached = _cached_brief(brief_path, [request_hash] + accepted)
        if cached:
            print("   -> Reusing cached story brief")
            return cached

    theme = focus_angle.strip() or random.choice(PSYCHOLOGICAL_THEMES)
    if not exact:
        # Ban topics of videos that became public since the last run.
        from uploader.publications import sync_publications_safely
        sync_publications_safely(interactive=False)
    published = load_banned_topics()
    banned = [] if exact else _banned_for_prompt(published)
    matches = published_premise_matches(exact, published) if exact else []
    if matches:
        print("   WARNING: this exact premise matches an already published topic: "
              + "; ".join(item["raw"] for item in matches))
    mode_instruction = (
        f'The user premise is authoritative: "{exact}". Develop this exact premise; '
        "do not replace it with a related idea."
        if exact else
        "Invent four substantially different premises, score them, and select the strongest."
    )
    type_instruction = (
        "Choose the narrative type that best serves the story."
        if narrative_type == "auto" else f'Use exactly this narrative type: "{narrative_type}".'
    )
    if author:
        mode_instruction += "\n\n" + briefs.contract_block(author)
    print(f"   -> Developing {'author brief' if author else 'exact premise' if exact else 'original premises'}: "
          f"{exact or theme}")
    contract = f"""You are the Story Development Agent for Private Gravity, an anthology of
cinematic psychological fiction. Ideas from psychology and philosophy must emerge through
character choice, consequence, image and subtext—never through a lecture or explainer.
The channel description, theme, duration and previously used premises are supplied after this contract.

{identity_block(None if narrative_type == "auto" else narrative_type)}

{TITLE_POLICY}

{mode_instruction}
{type_instruction}

Create four premise_candidates unless an exact premise was supplied (then create at least one
candidate faithful to it). Each candidate needs premise, narrative_type, originality_score,
emotional_power_score, dramatic_potential_score, visual_feasibility_score, audience_access_score,
weighted_total, strengths, and risks. Weights: dramatic 30%, emotional 25%, originality 20%,
audience access 15%, visual feasibility 10%. Select the best candidate without claiming that
scores predict audience performance.

Return ONLY one complete JSON object with these keys:
premise_candidates, selected_candidate_index, video_title, narrative_type,
psychological_theme, psychological_angle, logline, main_character_concept, central_dramatic_question,
protagonist_desire, underlying_emotional_need, principal_conflict, central_contradiction,
stakes, potential_turning_point, intended_emotional_experience, philosophical_question,
ending_direction, intended_audience, thumbnail_concept, originality_assessment,
production_feasibility, tags, title_candidates, thumbnail_text_candidates.
psychological_theme is the broad theme (e.g. "envy"); psychological_angle is ONE specific sentence
naming the mechanism explored (e.g. "needing to be envied as a substitute for self-worth").
PUBLISHED TOPICS RULE: {BANNED_RULE}
title_candidates: exactly 5 objects {{"title": "...", "rationale": "one sentence on the curiosity it
creates"}} that follow the title policy; video_title must be the strongest of them. At least two of
them must be built on the story's most shareable moment (the reversal: what the protagonist does that
a viewer would retell to a friend), not only on the premise. At most 60 characters, the hook within
the first ~45.
thumbnail_text_candidates: exactly 3 strings that follow the thumbnail text policy.

Use one of these narrative_type values: {json.dumps(sorted(NARRATIVE_TYPES))}.
The story must be original, causally coherent, emotionally plausible, filmable with cinematic
stock imagery, and must not use educational segments, factual listicles, or generic science hooks."""
    context = f"""Channel identity:
{channel_description}

Primary theme: {theme}
Planned duration: {duration:g} minutes.
Published topics to avoid during automatic generation: {compact_json(banned)}
Uniqueness nonce: {time.time_ns()}"""

    def finalize(value: dict) -> dict:
        brief = dict(value)
        if exact:
            brief["topic"] = exact
            brief["requested_premise"] = exact
            brief["exact_premise_override"] = True
        else:
            brief.setdefault("topic", brief.get("logline", ""))
            brief["exact_premise_override"] = False
        if narrative_type != "auto":
            brief["narrative_type"] = narrative_type
            for candidate in brief.get("premise_candidates", []):
                if isinstance(candidate, dict):
                    candidate["narrative_type"] = narrative_type
        brief["target_duration_minutes"] = duration
        brief["request_hash"] = request_hash
        if not str(brief.get("psychological_angle") or "").strip():
            raise NarrativeValidationError(
                "story brief requires a nonempty psychological_angle: one sentence naming the "
                "specific mechanism explored"
            )
        if matches:
            brief["published_topic_warning"] = [item["raw"] for item in matches]
        if author:
            _apply_author_brief(brief, author, published)
        if not exact and len(brief.get("premise_candidates", [])) < 3:
            raise NarrativeValidationError(
                "automatic story development requires at least three premise_candidates"
            )
        return validate_story_brief(brief)

    brief = generate_json(
        contract, "Story Development Agent", finalize, generator=generate,
        context=context, stage="premise", temperature_key="premise",
        run_dir=config.OUTPUT_DIR,
    )
    _apply_title_review(brief, _review_titles(brief))
    _score_titles(brief)
    atomic_write_json(brief_path, brief)
    return brief


def _review_titles(brief: dict) -> dict:
    """Pick the first policy-compliant title; otherwise make one bounded repair call."""
    review = select_title(brief)
    if review["status"] == "passed":
        return review
    failures = [
        {"title": item["title"], "problems": [finding["claim"] for finding in item["findings"]]}
        for item in review["title_candidates"] if not item["passes"]
    ] + [
        {"thumbnail_text": item["text"],
         "problems": [finding["claim"] for finding in item["findings"]]}
        for item in review["thumbnail_text_candidates"] if not item["passes"]
    ]
    print("   -> No title/thumbnail candidate passed the title policy; requesting new candidates")
    contract = f"""You write packaging for one Private Gravity episode. Return ONLY JSON
{{"title_candidates": [5 objects {{"title": "...", "rationale": "..."}}],
"thumbnail_text_candidates": [3 strings]}}.

{TITLE_POLICY}

The previous candidates failed for these reasons; do not repeat them:
{compact_json(failures)}"""
    story = {key: brief.get(key, "") for key in (
        "topic", "logline", "main_character_concept", "protagonist_desire",
        "central_contradiction", "psychological_theme", "potential_turning_point",
    )}

    def require_candidates(value: dict) -> dict:
        titles = value.get("title_candidates")
        thumbnails = value.get("thumbnail_text_candidates")
        if (not isinstance(titles, list) or not titles
                or not isinstance(thumbnails, list) or not thumbnails):
            raise NarrativeValidationError(
                "title repair requires nonempty title_candidates and thumbnail_text_candidates arrays"
            )
        return value

    try:
        repaired = generate_json(
            contract, "title candidates", require_candidates, generator=generate,
            context=f"STORY: {compact_json(story)}", stage="title_repair",
            temperature_key="premise", run_dir=config.OUTPUT_DIR,
        )
    except NarrativeValidationError as exc:
        review["repair_error"] = str(exc)
        review["repair_attempted"] = True
        return review
    retried = select_title({
        "psychological_theme": brief.get("psychological_theme", ""),
        "title_candidates": repaired["title_candidates"],
        "thumbnail_text_candidates": repaired["thumbnail_text_candidates"],
    })
    if retried["status"] != "passed":
        # Keep the best of both rounds: a passing title or thumbnail from either.
        for key, selected, candidates in (
                ("title", "selected_title", "title_candidates"),
                ("text", "selected_thumbnail_text", "thumbnail_text_candidates")):
            if not any(item["passes"] for item in retried[candidates]):
                passing = next((item for item in review[candidates] if item["passes"]), None)
                if passing:
                    retried[selected] = passing[key]
                    if key == "title":
                        retried["selected_title_findings"] = passing["findings"]
        if (title_passes(retried["selected_title_findings"]) and any(
                item["passes"] and item["text"] == retried["selected_thumbnail_text"]
                for item in retried["thumbnail_text_candidates"] + review["thumbnail_text_candidates"])):
            retried["status"] = "passed"
    retried["repair_attempted"] = True
    retried["initial_candidates"] = {
        "title_candidates": review["title_candidates"],
        "thumbnail_text_candidates": review["thumbnail_text_candidates"],
    }
    return retried


def _apply_author_brief(brief: dict, author: dict, published: list[dict]) -> None:
    """Phase 3.7-A: provided fields win over the model's; record the coverage and a topic warning."""
    from agents import premise_brief as briefs
    if author.get("logline"):
        brief["logline"] = author["logline"]
    if author.get("narrative_type") in NARRATIVE_TYPES:
        brief["narrative_type"] = author["narrative_type"]
    if author.get("thumbnail_idea"):
        brief["thumbnail_concept"] = author["thumbnail_idea"]
    ideas = [{"title": title, "rationale": "author's title idea"} for title in author.get("title_ideas") or []]
    if author.get("working_title"):
        ideas.insert(0, {"title": author["working_title"], "rationale": "author's working title"})
    existing = {str(item.get("title") if isinstance(item, dict) else item) for item in brief.get("title_candidates") or []}
    brief["title_candidates"] = [item for item in ideas if item["title"] not in existing] + \
        list(brief.get("title_candidates") or [])
    brief["author_brief"] = briefs.author_parts(author)
    brief["requested_brief_hash"] = briefs.brief_hash(author)
    model_coverage = brief.get("brief_coverage") if isinstance(brief.get("brief_coverage"), dict) else {}
    brief["brief_coverage"] = {**{key: str(value) for key, value in model_coverage.items()},
                               **briefs.coverage(author, brief)}
    theme, angle = brief.get("psychological_theme", ""), brief.get("psychological_angle", "")
    similar = [item["raw"] for item in published if item.get("theme") and
               str(item["theme"]).strip().lower() == str(theme).strip().lower()
               and briefs.mentioned(item.get("angle", ""), angle, 0.5)]
    if similar:
        print("   WARNING: the brief's theme and angle match a published episode: " + "; ".join(similar))
        brief["published_topic_warning"] = list(brief.get("published_topic_warning") or []) + similar


def _score_titles(brief: dict) -> None:
    """Phase 3.6-B: score the candidates and choose the best one that passes the title policy.
    A scoring failure keeps the policy's choice (scoring is advice, the validator is the gate)."""
    from agents import packaging
    review = brief.get("title_review") or {}
    try:
        scored = packaging.score_titles(brief, review.get("title_candidates") or [], generator=generate)
    except (NarrativeValidationError, KeyError, TypeError, ValueError) as exc:
        brief["title_scoring_error"] = str(exc)[:300]
        return
    brief["title_scores"] = scored
    best = next((row for row in scored if row["passes"]), None)
    if best:
        brief["video_title"] = best["title"]
        review["selected_title"] = best["title"]
        review["selected_title_findings"] = best["findings"]
    packaging.save_titles(config.OUTPUT_DIR, scored, brief.get("video_title", ""))


def _apply_title_review(brief: dict, review: dict) -> None:
    if review.get("selected_title"):
        brief["video_title"] = review["selected_title"]
    brief["thumbnail_text"] = review.get("selected_thumbnail_text", "")
    brief["title_review"] = review


def _cached_brief(path: str, request_hashes) -> dict | None:
    value = load_json(path)
    hashes = [request_hashes] if isinstance(request_hashes, str) else list(request_hashes)
    if not value or value.get("request_hash") not in hashes:
        return None
    try:
        return validate_story_brief(value)
    except (NarrativeValidationError, KeyError, TypeError):
        return None
