"""Shorts for other platforms (Phase 4.0-H): "Exportar para TikTok / Reels".

The same 9:16 file (already safe-area aware) plus a ``.txt`` with one caption per platform: the hook line, 3-5
hashtags and "Full story on YouTube: Private Gravity". Written to ``exports/social/<slug>/``. Nothing is posted.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import config
from agents.narrative import load_json

PLATFORMS = ("TikTok", "Instagram Reels")
FULL_STORY = "Full story on YouTube: Private Gravity"
GENERIC_TAGS = {"TikTok": ["#storytime", "#shortstory"], "Instagram Reels": ["#shortstory", "#storytelling"]}


def root() -> Path:
    return Path(getattr(config, "SOCIAL_EXPORT_DIR", Path(config.PUBLICATIONS_FILE).parent.parent / "exports" / "social"))


def hook_line(segment: dict) -> str:
    hook = str(segment.get("hook_text") or "").strip()
    title = re.sub(r"\s*#shorts\b", "", str(segment.get("title") or ""), flags=re.I).strip()
    if hook and title and hook.lower() not in title.lower():
        return f"{hook} {title}" if len(hook) + len(title) < 120 else hook
    return hook or title


def hashtags(platform: str, narrative_type: str, topic_words=None) -> list[str]:
    """3-5 hashtags: the channel, the type tag, one topic word, the platform's own story tags."""
    from agents import topic
    found = ["#PrivateGravity"]
    if topic.type_hashtag(narrative_type):
        found.append(topic.type_hashtag(narrative_type))
    for word in topic_words or []:
        tag = "#" + re.sub(r"[^A-Za-z0-9]", "", str(word).title())
        if len(tag) > 2:
            found.append(tag)
            break
    found += GENERIC_TAGS.get(platform, [])
    unique = list(dict.fromkeys(found))
    return unique[:5] if len(unique) >= 3 else (unique + ["#shortstory", "#storytime"])[:3]


def captions(segment: dict, narrative_type: str, topic_words=None) -> str:
    blocks = []
    for platform in PLATFORMS:
        blocks.append(f"== {platform} ==\n{hook_line(segment)}\n{' '.join(hashtags(platform, narrative_type, topic_words))}\n{FULL_STORY}")
    return "\n\n".join(blocks) + "\n"


def export(run_dir, short_id: str, *, out_root=None) -> dict:
    """Copy the Short and write its captions; returns {"folder", "video", "captions"}."""
    from video import shorts
    run_dir = Path(run_dir)
    plan = shorts.load(run_dir) or {}
    segment = next((item for item in plan.get("segments", []) if item.get("id") == short_id), None)
    video = run_dir / shorts.FOLDER / f"{short_id}.mp4"
    if segment is None or not video.is_file():
        raise LookupError(f"Short {short_id} não foi renderizado")
    script = load_json(run_dir / "script.json") or {}
    parts = load_json(run_dir / "description_parts.json") or {}
    slug = run_dir.name
    folder = Path(out_root or root()) / slug
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{short_id}.mp4"
    shutil.copyfile(video, target)
    text = folder / f"{short_id}.txt"
    text.write_text(captions(segment, script.get("narrative_type", ""), parts.get("topic_words")), encoding="utf-8")
    return {"folder": str(folder), "video": str(target), "captions": str(text)}
