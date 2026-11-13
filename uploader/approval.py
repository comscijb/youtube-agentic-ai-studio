"""Pre-publication human review gate.

Uploads are public by default, so safety comes from this gate: an upload is
refused unless ``<run_dir>/review_approval.json`` holds a complete reviewer
checklist bound to the exact artifacts being uploaded (script.json, video
file, title, description, tags and thumbnail). Any change after approval
invalidates it. Narrative status ``structurally_invalid`` or
``revision_required`` additionally needs a typed override reason, which is
recorded in the approval and in the publication ledger.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from agents.narrative import (
    atomic_write_json,
    load_json,
    title_passes,
    validate_thumbnail_text,
    validate_title,
)

APPROVAL_FILE = "review_approval.json"
APPROVAL_SCHEMA_VERSION = 1
CHECKLIST = {
    "watched_full_video": "I watched the full video.",
    "read_narrative_review": "I read the narrative review report.",
    "title_and_thumbnail_text_pass": (
        "The title and thumbnail text pass the title policy (or I give an override reason)."
    ),
    "license_log_reviewed": "The stock license log has no flagged items, or I reviewed them.",
    "description_and_tags_reviewed": "I reviewed the description and tags.",
}
SYNTHETIC_ITEM = {
    "synthetic_media_disclosed": (
        "The video contains AI-generated realistic people/places: it will be uploaded with "
        "YouTube's altered/synthetic content disclosure (containsSyntheticMedia) and I reviewed it."
    ),
}
ILLUSTRATION_ITEM = {
    "synthetic_media_disclosed": (
        "Reviewer decision on AI-generated images: they are clearly non-photorealistic "
        "illustration, so the realistic altered/synthetic disclosure is NOT set by default. "
        "Tick to confirm; choose 'set disclosure' instead if any image could pass as real."
    ),
}
BLOCKED_STATUSES = {"structurally_invalid", "revision_required"}
_MIN_REASON_CHARS = 10


class UploadNotApproved(RuntimeError):
    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("Upload refused: " + "; ".join(self.reasons))


def _sha256_text(value) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_file(path) -> str | None:
    if not path or not Path(path).is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bindings(run_dir, *, video_path, title, description, tags=None, thumbnail_path=None) -> dict:
    """Hashes of everything an upload sends; an approval is valid only for these."""
    return {
        "script": _sha256_file(Path(run_dir) / "script.json"),
        "video": _sha256_file(video_path),
        "title": _sha256_text(title),
        "description": _sha256_text(description),
        "tags": _sha256_text("\n".join(str(tag) for tag in tags or [])),
        "thumbnail": _sha256_file(thumbnail_path),
        "shorts": _shorts_binding(run_dir),
    }


def _shorts_binding(run_dir) -> str:
    """The included Shorts (files and texts) are approved together with the episode (Phase 3.7)."""
    from uploader import shorts_upload
    return shorts_upload.binding(run_dir)


def contains_synthetic_media(run_dir) -> bool:
    manifest = load_json(Path(run_dir) / "asset_manifest.json") or {}
    return bool(manifest.get("synthetic_media")) or any(
        item.get("synthetic") for item in manifest.get("generated_assets", []))


def voice_imitation(run_dir) -> str:
    """Why the narration voice may imitate a real person (a cloned reference clip whose provenance
    note does not say "real public person: no"), or '' when it does not."""
    voice = (load_json(Path(run_dir) / "narration_words.json") or {}).get("voice") or {}
    reference = voice.get("reference") if isinstance(voice, dict) else ""
    if not reference:
        return ""
    from audio.tts import voices
    note = voices.provenance_path(voices.folder() / Path(reference).name)
    text = note.read_text(encoding="utf-8").lower() if note.is_file() else ""
    if "real public person: no" in text:
        return ""
    return f"the narration clones {Path(reference).name}, whose provenance does not rule out a real person"


def disclosure_decision(run_dir, *, manifest: dict | None = None) -> dict:
    """The one rule for YouTube's altered/synthetic flag (Phase 3.9-F): ``True`` when any generated
    shot, clip or thumbnail is realistic (photographic style, or flagged ``realistic``; assets
    predating the style field count as photographic) or the voice imitates a real person.
    Returns ``{"value": bool, "reason": str}``."""
    if manifest is None:
        manifest = load_json(Path(run_dir) / "asset_manifest.json") or {}
    assets = [item for item in manifest.get("generated_assets", []) if item.get("synthetic")]
    realistic = [item for item in assets if item.get("realistic")
                 or item.get("visual_style", "cinematic_photo") != "dark_illustration"]
    reasons = []
    if realistic:
        kinds = "/".join(sorted({str(item.get("kind") or "image") for item in realistic}))
        reasons.append(f"realistic: {len(realistic)} generated {kinds} asset(s) in a photographic style")
    voice = voice_imitation(run_dir)
    if voice:
        reasons.append(voice)
    if reasons:
        return {"value": True, "reason": "; ".join(reasons)}
    if assets:
        return {"value": False, "reason": f"stylised: all {len(assets)} generated image(s) are non-photorealistic "
                                          "illustration; the voice is not a real person"}
    return {"value": False, "reason": "no generated imagery; the voice is not a real person"}


def realistic_synthetic_media(run_dir) -> bool:
    """The disclosure decision's value (see ``disclosure_decision``)."""
    return disclosure_decision(run_dir)["value"]


def episode_disclosure(run_dir, approval: dict | None = None) -> dict:
    """The decision stored once per episode in its approval (the episode and all its Shorts use it);
    the rule's own answer when no approval holds one."""
    record = approval if approval is not None else (load_json(Path(run_dir) / APPROVAL_FILE) or {})
    if "synthetic_disclosure" in record:
        return {"value": bool(record["synthetic_disclosure"]),
                "reason": record.get("synthetic_disclosure_reason") or "reviewer decision at approval"}
    return disclosure_decision(run_dir)


SHORTS_ITEM = {
    "shorts_reviewed": "I watched every included Short and checked its title, texts and description.",
}


def checklist_for(run_dir) -> dict:
    """The base checklist, plus the synthetic-media decision when generated images are used and the
    Shorts item when Shorts will be uploaded with the episode."""
    from uploader import shorts_upload
    items = dict(CHECKLIST)
    if contains_synthetic_media(run_dir):
        items.update(SYNTHETIC_ITEM if realistic_synthetic_media(run_dir) else ILLUSTRATION_ITEM)
    if shorts_upload.included(run_dir):
        items.update(SHORTS_ITEM)
    return items


def narrative_status(run_dir) -> str:
    review = load_json(Path(run_dir) / "narrative_review.json") or {}
    return str(review.get("status") or "missing")


def license_flags(run_dir) -> list[str]:
    """Stock assets whose provenance needs a human look."""
    manifest = load_json(Path(run_dir) / "asset_manifest.json")
    if manifest is None:
        return ["asset_manifest.json is missing: the license log could not be checked"]
    flags = []
    for asset in manifest.get("assets") or []:
        label = asset.get("source_url") or asset.get("local_path") or asset.get("id") or "asset"
        if not asset.get("license"):
            flags.append(f"{label}: no license recorded")
        elif not asset.get("license_url") and asset.get("license") != "Manually approved local asset":
            flags.append(f"{label}: license '{asset.get('license')}' has no license URL")
    for section in manifest.get("sections") or []:
        if section.get("used_rendering_fallback"):
            flags.append(f"section {section.get('section_id')}: no stock asset, rendering fallback used")
    return flags


def packaging_findings(run_dir, title: str) -> list[dict]:
    """Title-policy findings plus thumbnail-text findings for this run."""
    brief = (load_json(Path(run_dir) / "story_brief.json")
             or load_json(Path(run_dir) / "research.json") or {})
    script = load_json(Path(run_dir) / "script.json") or {}
    if not brief.get("psychological_theme"):
        brief = dict(brief, psychological_theme=script.get("psychological_theme", ""))
    findings = validate_title(title, brief)
    from agents.packaging import text_repeats_title, thumbnail_text_for
    thumbnail_text = thumbnail_text_for(run_dir, script, brief)
    if thumbnail_text:
        findings += validate_thumbnail_text(thumbnail_text, brief)
        if title and text_repeats_title(thumbnail_text, title):           # Phase 3.6-B: they complement
            findings.append({"severity": "blocking", "rule": "thumbnail_repeats_title",
                             "claim": f"The thumbnail text {thumbnail_text!r} repeats the title.",
                             "evidence": [thumbnail_text, title], "fix": "Pick another thumbnail or title.",
                             "issue_type": "thumbnail_text_policy"})
    return findings


def topic_findings(run_dir, title: str, description: str, tags=None) -> list[dict]:
    """Phase 4.0-A: the topic in the title or the first line, no keyword stuffing, at most 8 tags."""
    from agents import packaging, topic
    from agents.narrative import load_json
    script = load_json(Path(run_dir) / "script.json") or {}
    brief = load_json(Path(run_dir) / "story_brief.json") or {}
    parts = load_json(Path(run_dir) / packaging.DESCRIPTION_PARTS_FILE) or {}
    kind = script.get("narrative_type") or brief.get("narrative_type") or ""
    return topic.validate(title, description, kind, parts.get("topic_words") or brief.get("topic_words"),
                          list(tags) if tags is not None else None)


def review_summary(run_dir, *, title: str, generator=None, **artifacts) -> dict:
    """Everything the reviewer must see before approving (for GUI/dashboard). ``generator`` runs the
    cached thumbnail-text check (Phase 3.9-E); without it only a cached answer is used."""
    status = narrative_status(run_dir)
    disclosure = disclosure_decision(run_dir)
    from video import coherence
    findings = packaging_findings(run_dir, title)
    current = check_approval(run_dir, title=title, **artifacts)
    from video import description as upload_description
    description_findings = upload_description.validate(artifacts.get("description", ""))
    description_findings += topic_findings(run_dir, title, artifacts.get("description", ""), artifacts.get("tags"))
    return {
        "narrative_status": status,
        "status_override_required": status in BLOCKED_STATUSES or status == "missing",
        "description_findings": description_findings,
        "shorts": [{key: item.get(key) for key in ("id", "title", "hook_text", "end_text", "description", "start", "end")}
                   | {"video": f"shorts/{item['id']}.mp4"} for item in _included_shorts(run_dir)],
        "description_passes": upload_description.passes(description_findings),
        "title_findings": findings,
        "title_passes": title_passes(findings),
        "license_flags": license_flags(run_dir),
        "checklist": checklist_for(run_dir),
        "synthetic_media": contains_synthetic_media(run_dir),
        "synthetic_disclosure_default": disclosure["value"],
        "synthetic_disclosure_reason": disclosure["reason"],
        "approval_valid": not current,
        "approval_problems": current,
        "coherence": coherence.findings(run_dir, generator=generator),     # warnings, never a block
    }


def _included_shorts(run_dir) -> list[dict]:
    from uploader import shorts_upload
    return shorts_upload.included(run_dir)


def approve(run_dir, *, checklist: dict, video_path, title, description, tags=None,
            thumbnail_path=None, notes: str = "", title_override_reason: str = "",
            status_override_reason: str = "", reviewer: str = "",
            synthetic_disclosure: bool | None = None, disclosure_override_reason: str = "") -> dict:
    """Validate the checklist and write the approval bound to the current artifacts.

    ``synthetic_disclosure`` is YouTube's realistic altered/synthetic content flag. It defaults to
    ``disclosure_decision``; choosing the other value is an override that needs a typed
    ``disclosure_override_reason``. The value and its reason are stored once for the episode.
    """
    problems = []
    required = checklist_for(run_dir)
    missing = [key for key in required if checklist.get(key) is not True]
    if missing:
        problems.append("checklist incomplete: " + ", ".join(missing))
    if not Path(video_path or "").is_file():
        problems.append("the video file does not exist")
    findings = packaging_findings(run_dir, title)
    title_reason = str(title_override_reason or "").strip()
    if not title_passes(findings) and len(title_reason) < _MIN_REASON_CHARS:
        problems.append("title/thumbnail text break the title policy; fix them or type an "
                        f"override reason (at least {_MIN_REASON_CHARS} characters)")
    status = narrative_status(run_dir)
    status_reason = str(status_override_reason or "").strip()
    if (status in BLOCKED_STATUSES or status == "missing") and len(status_reason) < _MIN_REASON_CHARS:
        problems.append(f"narrative status is {status}; uploading requires a typed override "
                        f"reason (at least {_MIN_REASON_CHARS} characters)")
    decision = disclosure_decision(run_dir)
    disclosure = decision["value"] if synthetic_disclosure is None else bool(synthetic_disclosure)
    disclosure_reason = str(disclosure_override_reason or "").strip()
    if disclosure != decision["value"] and len(disclosure_reason) < _MIN_REASON_CHARS:
        problems.append(f"the synthetic-content disclosure differs from the rule ({decision['reason']}); type an "
                        f"override reason (at least {_MIN_REASON_CHARS} characters)")
    if problems:
        raise UploadNotApproved(problems)
    record = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "reviewer": reviewer,
        "checklist": {key: True for key in required},
        "synthetic_media": contains_synthetic_media(run_dir),
        "synthetic_disclosure": disclosure,
        "synthetic_disclosure_reason": decision["reason"] if disclosure == decision["value"]
                                       else f"reviewer override: {disclosure_reason}",
        "synthetic_disclosure_rule": decision,
        "notes": str(notes or "").strip(),
        "narrative_status": status,
        "status_override_reason": status_reason if status in BLOCKED_STATUSES | {"missing"} else "",
        "title": title,
        "title_findings": findings,
        "title_override_reason": title_reason if not title_passes(findings) else "",
        "license_flags": license_flags(run_dir),
        "bindings": bindings(run_dir, video_path=video_path, title=title,
                             description=description, tags=tags, thumbnail_path=thumbnail_path),
    }
    from uploader import editorial_record              # Phase 3.9-D: evidence of human creative input
    record["editorial_record"] = editorial_record.build(run_dir)
    record["editorial_summary"] = editorial_record.summary(record["editorial_record"])
    atomic_write_json(Path(run_dir) / APPROVAL_FILE, record)
    return record


def check_approval(run_dir, *, video_path, title, description, tags=None,
                   thumbnail_path=None) -> list[str]:
    """Return why the current artifacts are not approved (empty list = approved)."""
    record = load_json(Path(run_dir) / APPROVAL_FILE)
    if not record:
        return ["no review approval exists for this run; complete the review checklist"]
    problems = []
    if any(record.get("checklist", {}).get(key) is not True for key in checklist_for(run_dir)):
        problems.append("the stored approval has an incomplete checklist")
    current = bindings(run_dir, video_path=video_path, title=title, description=description,
                       tags=tags, thumbnail_path=thumbnail_path)
    changed = [key for key, value in current.items()
               if record.get("bindings", {}).get(key) != value]
    if changed:
        problems.append("approval is stale: changed since approval: " + ", ".join(changed))
    status = narrative_status(run_dir)
    if status != record.get("narrative_status"):
        problems.append(f"narrative status changed since approval ({record.get('narrative_status')} -> {status})")
    elif (status in BLOCKED_STATUSES or status == "missing") \
            and len(str(record.get("status_override_reason") or "").strip()) < _MIN_REASON_CHARS:
        problems.append(f"narrative status is {status} and the approval has no override reason")
    return problems


def require_approval(run_dir, **artifacts) -> dict:
    """Raise UploadNotApproved unless a valid approval exists; return it otherwise."""
    problems = check_approval(run_dir, **artifacts)
    if problems:
        raise UploadNotApproved(problems)
    return load_json(Path(run_dir) / APPROVAL_FILE)
