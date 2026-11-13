"""Topic clarity in every package (Phase 4.0-A).

A new channel is recommended largely by topic matching: with no viewer history, YouTube reads the title, the
description and the chapters. So every package names **one searchable concept** a viewer might type ("thought
experiment", "parable", "AI clone", "family vlogger", "mirror") next to its curiosity hook, without turning into
keyword soup:

* ``concepts`` — the searchable concepts for an episode: the brief's ``topic_words`` (Gemini writes them with
  the description parts), the type's own words, and a small vocabulary of story concepts;
* ``topic_score`` — the title criterion (1 or 5, deterministic) that ``agents.packaging`` adds to the scores;
* ``series_line`` / ``type_hashtag`` / ``tags`` / ``hashtags`` — the description's series line, at most 8 tags
  (topic + type, never another channel's name) and 2-3 hashtags with the type tag;
* ``validate`` — topic words in the title or the description's first line; no keyword more than twice in the
  description's prose (anti-stuffing; the chapter list and hashtags are not prose).
"""
from __future__ import annotations

import re

SERIES_LINES = {
    "philosophical_thought_experiment": "A Private Gravity philosophical thought experiment.",
    "dark_human_parable": "A Private Gravity dark parable.",
    "realistic_psychological_fiction": "Private Gravity · realistic psychological fiction.",
}
TYPE_HASHTAGS = {
    "philosophical_thought_experiment": "#thoughtexperiment",
    "dark_human_parable": "#parable",
    "realistic_psychological_fiction": "#psychologicalfiction",
}
TYPE_CONCEPTS = {
    "philosophical_thought_experiment": ["thought experiment", "what if", "philosophy"],
    "dark_human_parable": ["parable", "fable", "village"],
    "realistic_psychological_fiction": ["psychological", "family", "obsession"],
}
TYPE_TAGS = {
    "philosophical_thought_experiment": ["thought experiment", "philosophical story"],
    "dark_human_parable": ["dark parable", "modern fable"],
    "realistic_psychological_fiction": ["psychological fiction", "psychological drama"],
}
# Story concepts a viewer types into search; whole words or phrases, lower case.
VOCABULARY = (
    "thought experiment", "parable", "fable", "ai clone", "clone", "duplicate", "double", "mirror", "family vlogger",
    "vlogger", "influencer", "memory", "memories", "forget", "envy", "jealousy", "loneliness", "lonely", "regret",
    "ferryman", "baker", "mayor", "village", "stranger", "his own face", "own face", "mind reading", "thoughts",
    "perfect listener", "warm glow", "daughter", "son", "mother", "father", "videos", "camera", "obsession",
    "identity", "simulation", "afterlife", "immortality", "dream", "time loop", "twin", "doppelganger",
)
MAX_TAGS = 8
MAX_REPEATS = 2
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "his", "her", "their", "who", "that",
        "this", "it", "is", "was", "be", "at", "by", "from", "as", "what", "when", "you", "your", "they", "he", "she",
        "private", "gravity", "story", "episode", "video", "full", "subscribe", "like", "share", "comments", "tell"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", str(text or "").lower().replace("’", "'"))).strip()


def contains(text: str, phrase: str) -> bool:
    return bool(phrase) and re.search(rf"(?<![a-z0-9]){re.escape(_norm(phrase))}(?![a-z0-9])", _norm(text)) is not None


def concepts(narrative_type: str = "", topic_words=None) -> list[str]:
    """Searchable concepts for one episode: the brief's own topic words first, then the type's, then the vocabulary."""
    found = [str(word).strip().lower() for word in (topic_words or []) if str(word).strip()]
    found += TYPE_CONCEPTS.get(narrative_type, [])
    found += list(VOCABULARY)
    return list(dict.fromkeys(found))


def found_concepts(text: str, narrative_type: str = "", topic_words=None) -> list[str]:
    return [phrase for phrase in concepts(narrative_type, topic_words) if contains(text, phrase)]


def topic_score(title: str, narrative_type: str = "", topic_words=None) -> int:
    """5 when the title carries a searchable concept, else 1 (the curiosity hook alone is not found by search)."""
    return 5 if found_concepts(title, narrative_type, topic_words) else 1


def series_line(narrative_type: str) -> str:
    return SERIES_LINES.get(narrative_type, "")


def type_hashtag(narrative_type: str) -> str:
    return TYPE_HASHTAGS.get(narrative_type, "")


def _blocked(tag: str, blocked_names) -> bool:
    lowered = tag.lower()
    return any(name and name.lower() in lowered for name in blocked_names)


def tags(narrative_type: str, topic_words=None, existing=None, *, blocked_names=()) -> list[str]:
    """At most 8 relevant tags: the topic words, the type's tags, then the script's own; never a channel's name."""
    found = []
    for tag in [*(topic_words or []), *TYPE_TAGS.get(narrative_type, []), "short story", *(existing or [])]:
        clean = re.sub(r"\s+", " ", str(tag).replace("#", "")).strip()
        if not clean or len(clean) > 30 or _blocked(clean, blocked_names):
            continue
        if clean.lower() not in {item.lower() for item in found}:
            found.append(clean)
        if len(found) == MAX_TAGS:
            break
    return found


def hashtags(narrative_type: str, topic_words=None, limit: int = 3) -> str:
    """2-3 hashtags: #PrivateGravity, the type tag, and one topic word when there is room."""
    found = ["#PrivateGravity"]
    if type_hashtag(narrative_type):
        found.append(type_hashtag(narrative_type))
    for word in topic_words or []:
        tag = "#" + re.sub(r"[^A-Za-z0-9]", "", str(word).title())
        if len(found) < limit and len(tag) > 2 and tag.lower() not in {item.lower() for item in found}:
            found.append(tag)
    return " ".join(found[:limit])


def prose(description: str) -> str:
    """The description without its chapter list and hashtag lines (they are structure, not prose)."""
    lines = []
    for line in str(description or "").splitlines():
        if re.match(r"^\s*(?:\d+:)?\d{1,2}:\d{2}\s", line) or line.strip().lower() == "chapters":
            continue
        line = re.sub(r"(?<!\S)#\w+", "", line)
        lines.append(line)
    return "\n".join(lines)


def repeated_keywords(description: str, keywords, limit: int = MAX_REPEATS) -> dict[str, int]:
    """Keywords (topic words and tags) used more than ``limit`` times in the prose."""
    text = prose(description)
    counts = {}
    for keyword in dict.fromkeys(str(item).strip().lower() for item in keywords or [] if str(item).strip()):
        if keyword in STOP:
            continue
        count = len(re.findall(rf"(?<![a-z0-9]){re.escape(_norm(keyword))}(?![a-z0-9])", _norm(text)))
        if count > limit:
            counts[keyword] = count
    return counts


def validate(title: str, description: str, narrative_type: str = "", topic_words=None, tag_list=None) -> list[dict]:
    """Findings: the topic in the title or the first line; anti-stuffing; tag count."""
    findings = []
    first = next((line for line in str(description or "").splitlines() if line.strip()), "")
    if not found_concepts(f"{title}\n{first}", narrative_type, topic_words):
        findings.append({"severity": "major", "rule": "no_topic",
                         "claim": "Neither the title nor the description's first line names a searchable topic."})
    for keyword, count in repeated_keywords(description, [*(topic_words or []), *(tag_list or [])]).items():
        findings.append({"severity": "blocking", "rule": "keyword_stuffing",
                         "claim": f"\"{keyword}\" appears {count} times in the description (max {MAX_REPEATS})."})
    if tag_list is not None and len(tag_list) > MAX_TAGS:
        findings.append({"severity": "major", "rule": "too_many_tags", "claim": f"{len(tag_list)} tags (max {MAX_TAGS})."})
    return findings


def chapter_has_topic(chapter_titles, narrative_type: str = "", topic_words=None) -> bool:
    return any(found_concepts(title, narrative_type, topic_words) for title in chapter_titles or [])


def episode_tags(run_dir, script: dict) -> list[str]:
    """The tags an upload sends: topic words, type tags, then the script's own; at most 8, no channel names."""
    from pathlib import Path

    import config
    from agents.narrative import load_json
    brief = load_json(Path(run_dir) / "story_brief.json") or {}
    parts = load_json(Path(run_dir) / "description_parts.json") or {}
    kind = script.get("narrative_type") or brief.get("narrative_type") or ""
    return tags(kind, parts.get("topic_words") or brief.get("topic_words"), script.get("tags", []),
                blocked_names=list(getattr(config, "TAG_BLOCKED_NAMES", []) or []))
