"""Channel-level style anchors: our own approved images that define the drawing style.

``python -m scripts.generate_style_refs`` renders 6 character-free "style
board" candidates in the chosen variant; the user approves 1-2 of them. They
live in ``config.STYLE_REFS_DIR`` (shared by every episode) and are keyed by
the style hash, so changing the style or variant never reuses old anchors.
Only our own generated images are used as style references — never
third-party artworks.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import config
from agents import identity

APPROVED_FILE = "approved.json"
MAX_ANCHORS = 2
BOARD_SUBJECTS = (
    "a vast empty hall with towering doorways and a single lamp on a long table",
    "an endless staircase descending into fog, a small landing lit by a candle",
    "a corridor that is far too long, doors receding into darkness, cold light from one window",
    "a high-ceilinged dining room lost in shadow, smoked glass decanters catching a warm glint",
    "a rain-dark window over a sleeping city, fog tendrils curling along the glass",
    "a single chair in an enormous empty room, its long shadow stretching across the floor",
)


def root() -> Path:
    return Path(getattr(config, "STYLE_REFS_DIR", "assets/style_refs"))


def candidates_dir(style_hash: str | None = None) -> Path:
    return root() / "candidates" / (style_hash or identity.style_hash())


def _load() -> dict:
    try:
        return json.loads((root() / APPROVED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def approved(style_hash: str | None = None) -> list[dict]:
    """Approved anchors for the current style whose files still exist."""
    entries = _load().get(style_hash or identity.style_hash(), [])
    return [entry for entry in entries if (root() / entry["path"]).is_file()]


def approved_paths(style_hash: str | None = None) -> list[str]:
    return [str(root() / entry["path"]) for entry in approved(style_hash)]


def approve(number: int, *, seed: int | None = None, prompt: str = "") -> list[dict]:
    """Approve candidate ``number`` of the current style (keeps at most MAX_ANCHORS)."""
    style_hash = identity.style_hash()
    candidate = candidates_dir(style_hash) / f"board_{int(number)}.jpg"
    if not candidate.is_file():
        raise FileNotFoundError(f"no style board #{number} for the current style; "
                                "run python -m scripts.generate_style_refs first")
    target = root() / "approved" / style_hash / f"board_{int(number)}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    with Image.open(candidate) as image:
        image.convert("RGB").save(target, "PNG")
    data = _load()
    entries = [entry for entry in data.get(style_hash, []) if entry["number"] != int(number)]
    entries.append({"number": int(number), "path": str(target.relative_to(root())).replace("\\", "/"),
                    "seed": seed, "prompt": prompt, "style": identity.visual_style(),
                    "variant": identity.style_variant()})
    data[style_hash] = entries[-MAX_ANCHORS:]
    root().mkdir(parents=True, exist_ok=True)
    (root() / APPROVED_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data[style_hash]


def board_prompt(index: int) -> str:
    """Worded positively (the negative is zeroed: "no people" draws people), figure-free suffix."""
    subject = BOARD_SUBJECTS[index % len(BOARD_SUBJECTS)]
    return (f"An {identity.image_noun()} of {subject}; deserted and unoccupied, only architecture, furniture "
            f"and objects, perfectly still. {identity.style_suffix(figures=False)}")


def empty_paths(style_hash: str | None = None) -> list[str]:
    """Approved anchors with nobody in them (layer-1 detector, result kept in approved.json): the only
    anchors used for location references and shots without characters. Unchecked anchors are not used."""
    style_hash = style_hash or identity.style_hash()
    data = _load()
    entries = [entry for entry in data.get(style_hash, []) if (root() / entry["path"]).is_file()]
    changed = False
    for entry in entries:
        if "people" in entry:
            continue
        from video.qa import fixes
        detector = fixes._detector()
        if detector is None:
            continue
        entry["people"] = len(detector.detect(root() / entry["path"]).get("persons", []))
        changed = True
    if changed:
        (root() / APPROVED_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return [str(root() / entry["path"]) for entry in entries if entry.get("people") == 0]


def clear_candidates(style_hash: str | None = None) -> None:
    shutil.rmtree(candidates_dir(style_hash), ignore_errors=True)
