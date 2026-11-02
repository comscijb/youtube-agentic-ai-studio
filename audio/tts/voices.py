"""Reference voices for cloning, and their provenance (Phase 3.9-A).

Rules (also shown in the GUI):

* a reference clip must be **your own voice or one licensed for this use**;
* never clone a real public person (an identifiable voice would also require the synthetic-content
  disclosure);
* clips live in ``assets/voice/`` with a provenance note ``<clip>.provenance.txt`` (who is speaking,
  the licence or consent, the date). A clip without its note is refused.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import config

AUDIO_TYPES = (".wav", ".mp3", ".flac", ".m4a", ".ogg")


class VoiceProvenanceError(ValueError):
    """The reference clip has no provenance note (or it is empty)."""


def folder() -> Path:
    return Path(getattr(config, "VOICE_ASSETS_DIR", None)
                or Path(__file__).resolve().parent.parent.parent / "assets" / "voice")


def provenance_path(clip) -> Path:
    clip = Path(clip)
    return clip.with_name(clip.name + ".provenance.txt")


def check_reference(clip) -> Path:
    """The clip, if it has a non-empty provenance note; else VoiceProvenanceError."""
    clip = Path(clip)
    if not clip.is_file():
        raise VoiceProvenanceError(f"reference clip not found: {clip}")
    note = provenance_path(clip)
    if not note.is_file() or not note.read_text(encoding="utf-8").strip():
        raise VoiceProvenanceError(
            f"{clip.name} has no provenance note ({note.name}): a reference voice must be your own or "
            "licensed for this use, never a real public person")
    return clip


def save_reference(data: bytes, filename: str, *, speaker: str, licence: str, own_voice: bool,
                   not_public_person: bool) -> Path:
    """Store an uploaded clip with its provenance note; refuse without a speaker, licence and both
    confirmations."""
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_TYPES:
        raise VoiceProvenanceError(f"unsupported audio type {suffix}")
    if not str(speaker).strip() or not str(licence).strip():
        raise VoiceProvenanceError("say who is speaking and under which licence or consent")
    if not own_voice or not not_public_person:
        raise VoiceProvenanceError("confirm it is your own (or licensed) voice and not a real public person")
    target = folder() / Path(filename).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    provenance_path(target).write_text(
        f"speaker: {speaker.strip()}\nlicence/consent: {licence.strip()}\nown or licensed voice: yes\n"
        f"real public person: no\nadded: {datetime.now(timezone.utc).date().isoformat()}\n", encoding="utf-8")
    return target


def references() -> list[dict]:
    """Clips in assets/voice/ and whether each can be used."""
    found = []
    for clip in sorted(folder().glob("*")) if folder().is_dir() else []:
        if clip.suffix.lower() not in AUDIO_TYPES:
            continue
        try:
            check_reference(clip)
            found.append({"name": clip.name, "usable": True, "provenance": provenance_path(clip).read_text(encoding="utf-8")})
        except VoiceProvenanceError as exc:
            found.append({"name": clip.name, "usable": False, "error": str(exc)})
    return found
