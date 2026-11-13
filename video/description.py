"""Upload description from a template, with chapters and CTAs, validated (Phase 3.6-C).

The template (``data/description_template.txt``, editable in Settings) holds
placeholders filled here:

* ``{hook}`` — 1–2 lines from the logline, no spoiler of the ending
  (``description_parts.json``, Gemini, see ``agents.packaging``);
* ``{chapters}`` — from ``scene_timeline.json`` with short evocative titles:
  first at 0:00, at least 3, each at least 10 s (YouTube's chapter rules);
  scenes shorter than 10 s are merged into the previous chapter;
* ``{cta}`` — subscribe, like, share with someone who'd recognise themselves,
  and a comment prompt specific to the episode;
* ``{channel_line}``, ``{fiction_note}``, ``{credits}`` (music/visual
  attributions when required) and ``{hashtags}`` (2–3, at the end).

``validate_description`` checks the result: ≤ 5000 characters, the CTA block,
valid chapters. A pinned-comment suggestion is produced alongside (the API
cannot pin comments: the GUI shows it to copy by hand).
"""
from __future__ import annotations

import re
from pathlib import Path

import config
from agents.narrative import load_json
from video.timeline import load_timeline

TEMPLATE_FILE = Path(__file__).resolve().parent.parent / "data" / "description_template.txt"
DEFAULT_TEMPLATE = """{premise}
{series_line}

{hook}

{chapters}

{cta}

{channel_line}
{fiction_note}
{credits}
{authorship}
{hashtags}"""
REQUIRED_PLACEHOLDERS = ("{chapters}", "{cta}")
MAX_CHARS = 5000
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10
CTA_MARKER = "▶ Subscribe"
TIMESTAMP = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2})\s+(\S.*)$")


class DescriptionError(ValueError):
    """The assembled description breaks a rule (too long, no CTA, invalid chapters)."""


def template() -> str:
    try:
        text = TEMPLATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_TEMPLATE
    return text if text.strip() else DEFAULT_TEMPLATE


def template_problems(text: str) -> list[str]:
    problems = [f"missing placeholder {name}" for name in REQUIRED_PLACEHOLDERS if name not in text]
    try:
        text.format(**{key: "" for key in ("hook", "chapters", "cta", "channel_line", "fiction_note", "credits",
                                           "hashtags", "title", "premise", "series_line", "authorship")})
    except (KeyError, IndexError, ValueError) as exc:
        problems.append(f"unknown or broken placeholder: {exc}")
    return problems


def save_template(text: str) -> None:
    problems = template_problems(text)
    if problems:
        raise DescriptionError("; ".join(problems))
    TEMPLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_FILE.write_text(text.replace("\r\n", "\n"), encoding="utf-8")


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    hours, minutes, rest = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def chapters(timeline: dict | None, titles: list[str] | None = None) -> list[dict]:
    """[{start, title}] from the scene timeline: the first at 0:00; a scene shorter than 10 s is
    absorbed by the previous chapter (the first one by the next), so every chapter lasts ≥ 10 s."""
    scenes = (timeline or {}).get("scenes") or []
    if not scenes:
        return []
    total = float(timeline.get("total_duration") or sum(float(scene.get("duration", 0)) for scene in scenes))
    merged = []
    for index, scene in enumerate(scenes):
        title = (titles[index] if titles and index < len(titles) and titles[index] else scene.get("title", "")) or             f"Part {index + 1}"
        start = float(scene.get("start", 0))
        duration = float(scene.get("duration", float(scene.get("end", start)) - start))
        if merged and duration < MIN_CHAPTER_SECONDS:
            continue                                  # absorbed by the previous chapter
        merged.append({"start": start, "title": str(title).strip(), "duration": duration})
    merged[0]["start"] = 0.0                          # a short first scene: the next chapter starts at 0:00
    if len(merged) > 1 and merged[1]["start"] - merged[0]["start"] < MIN_CHAPTER_SECONDS:
        merged[1]["start"] = 0.0
        merged.pop(0)
    while len(merged) > 1 and total - merged[-1]["start"] < MIN_CHAPTER_SECONDS:
        merged.pop()                                  # the last chapter must last 10 s too
    return [{"start": item["start"], "title": item["title"]} for item in merged]


def chapter_block(items: list[dict]) -> str:
    if len(items) < MIN_CHAPTERS:
        return ""
    return "Chapters\n" + "\n".join(f"{timestamp(item['start'])} {item['title']}" for item in items)


def cta_block(question: str) -> str:
    question = str(question or "").strip() or "What stayed with you after the ending?"
    return "\n".join([
        f"{CTA_MARKER} for more stories about the hidden forces that shape us.",
        "👍 Like the video, and share it with someone who'd recognise themselves in it.",
        f"💬 {question} Tell us in the comments.",
    ])


def hashtags(tags: list, limit: int = 3) -> str:
    found = []
    for tag in ["Private Gravity", *(tags or [])]:
        word = re.sub(r"[^A-Za-z0-9]", "", str(tag).title())
        if word and word.lower() not in {item.lower() for item in found}:
            found.append(word)
        if len(found) == limit:
            break
    return " ".join(f"#{word}" for word in found)


def credits(output_dir) -> str:
    manifest = load_json(Path(output_dir) / "asset_manifest.json") or {}
    lines = []
    attributed = []
    for asset in manifest.get("assets", []):
        if asset.get("source_url") and asset.get("creator"):
            license_note = f" ({asset['license_url']})" if asset.get("license_url") else ""
            attributed.append(f"{asset['creator']} — {asset['source_url']}{license_note}")
    if attributed:
        lines += ["Visual credits:", *dict.fromkeys(attributed)]
    music = manifest.get("music") or {}
    if music.get("track") and music.get("attribution"):
        lines.append(f"Music: {music['attribution']}")
    return "\n".join(lines)


def authorship() -> str:
    """The honest credit line (Phase 3.9-D): human story and editing, AI as a production tool."""
    return str(getattr(config, "DESCRIPTION_CREDIT_LINE", "") or "").replace("{channel}", config.CHANNEL_NAME).strip()


def about_text() -> str:
    """A suggested channel "About" paragraph (pasted by hand in YouTube Studio; no API write)."""
    return (f"{config.CHANNEL_NAME} is an anthology of original cinematic short fiction about the private forces "
            "that shape people: envy, loneliness, the need to be needed, the stories we tell ourselves. Every "
            "episode is a new story written, edited and reviewed by a person; the illustrations and the narration "
            "are produced with AI tools under that direction, in a consistent hand-drawn style. The stories are "
            "fiction: any resemblance to real people is coincidental. New episodes arrive at a steady, unhurried "
            "pace.")


def channel_line() -> str:
    text = " ".join(str(getattr(config, "CHANNEL_DESCRIPTION", "")).split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    return f"{config.CHANNEL_NAME} — {first[0].lower() + first[1:] if first else 'original cinematic psychological fiction.'}"


def build(output_dir, script: dict, brief: dict, parts: dict | None = None) -> str:
    """The upload description for a normal episode, validated (DescriptionError when it breaks a rule)."""
    output = Path(output_dir)
    parts = parts or {}
    hook = str(parts.get("hook") or brief.get("logline") or script.get("description") or "").strip()
    from agents import cold_open
    if cold_open.repeats_teaser(hook, script):          # Phase 3.9.7: the video already opens with that line
        hook = next((str(item).strip() for item in (brief.get("logline"), script.get("description"))
                     if item and not cold_open.repeats_teaser(str(item), script)), hook)
    titles = list(parts.get("chapter_titles") or [])
    script_sections = [section for section in (load_json(output / "script.json") or {}).get("sections", [])
                       if str(section.get("narration", "")).strip()]
    if titles and script_sections and script_sections[0].get("section_type") == "cold_open":
        titles[0] = getattr(config, "COLD_OPEN_CHAPTER_TITLE", "Prologue")      # 0:00 over a teaser: never a spoiler
    items = chapters(load_timeline(output / "scene_timeline.json"), titles)
    from agents import topic
    kind = script.get("narrative_type") or brief.get("narrative_type") or ""
    words = list(parts.get("topic_words") or brief.get("topic_words") or [])
    premise = str(parts.get("premise_line") or "").strip()
    if premise and premise == hook:
        premise = ""
    values = {"premise": premise, "series_line": topic.series_line(kind), "hook": hook, "chapters": chapter_block(items), "cta": cta_block(parts.get("comment_question", "")),
              "channel_line": channel_line(),
              "fiction_note": "This episode is an original work of fiction. Characters and events are fictional.",
              "credits": credits(output),
              "hashtags": topic.hashtags(kind, words) if kind else hashtags(script.get("tags", [])),
              "title": script.get("title", ""), "authorship": authorship()}
    text = template().format(**values)
    head = "\n".join(line for line in (values["premise"], values["series_line"]) if line and line not in text)
    if head:                                            # Phase 4.0-A: a custom template without {premise}/{series_line}
        text = f"{head}\n\n{text}"
    if values["authorship"] and values["authorship"] not in text:     # a custom template without {authorship}
        tags = values["hashtags"]
        text = text.replace(tags, f"{values['authorship']}\n{tags}", 1) if tags and tags in text \
            else text.rstrip() + "\n" + values["authorship"]
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    blocking = [item for item in validate(text) if item["severity"] == "blocking"]
    if blocking:
        raise DescriptionError("; ".join(item["claim"] for item in blocking))
    return text


def validate(text: str) -> list[dict]:
    """Findings: length, CTA block, chapters (first 0:00, ≥ 3, ≥ 10 s each, in order), hashtags."""
    findings = []

    def finding(severity, rule, claim):
        findings.append({"severity": severity, "rule": rule, "claim": claim})
    text = str(text or "")
    if len(text) > MAX_CHARS:
        finding("blocking", "too_long", f"The description has {len(text)} characters (max {MAX_CHARS}).")
    if CTA_MARKER not in text or "comments" not in text:
        finding("blocking", "no_cta", "The CTA block (subscribe, like, share, comment prompt) is missing.")
    stamps = []
    for line in text.splitlines():
        match = TIMESTAMP.match(line)
        if match:
            parts = [int(value) for value in match.group(1).split(":")]
            seconds = parts[-1] + 60 * parts[-2] + (3600 * parts[0] if len(parts) == 3 else 0)
            stamps.append(seconds)
    if stamps:
        if stamps[0] != 0:
            finding("blocking", "chapters_start", "The first chapter must start at 0:00.")
        if len(stamps) < MIN_CHAPTERS:
            finding("blocking", "chapters_count", f"{len(stamps)} chapters (YouTube needs at least {MIN_CHAPTERS}).")
        gaps = [after - before for before, after in zip(stamps, stamps[1:])]
        if any(gap < MIN_CHAPTER_SECONDS for gap in gaps):
            finding("blocking", "chapters_short", f"A chapter is shorter than {MIN_CHAPTER_SECONDS} s or out of order.")
    else:
        finding("major", "no_chapters", "No chapters: YouTube shows none for this video.")
    if authorship() and authorship() not in text:
        finding("blocking", "no_authorship", "The credit line on human authorship and AI tools is missing.")
    tags = re.findall(r"(?<!\S)#\w+", text)
    if len(tags) > 15:
        finding("blocking", "too_many_hashtags", f"{len(tags)} hashtags (YouTube ignores all above 15).")
    return findings


def passes(findings: list[dict]) -> bool:
    return not any(item["severity"] == "blocking" for item in findings)
