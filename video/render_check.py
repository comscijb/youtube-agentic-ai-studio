"""What the Video stage checks before it renders (Phase 3.9.4-F).

Every shot has its image (a missing one fails the stage: a normal episode never renders with gradients),
images are 1920x1080 and have no paper margin, and the shot timings cover the narration. The last two
are warnings: the render still works, with the flaw on screen.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

FRAME = (1920, 1080)
COVERAGE_TOLERANCE = 0.5


class ShotImagesMissing(RuntimeError):
    """A shot has no image: name them all, and stop instead of falling back to stock or gradients."""

    def __init__(self, shots: list[str]):
        self.shots = list(shots)
        super().__init__(f"{len(shots)} shot(s) have no image: {', '.join(shots)}. Generate them before the video.")


def _load(path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def missing_images(output_dir, shots: list[dict]) -> list[str]:
    from agents.shot_list import resolve_image
    output = Path(output_dir)
    missing = []
    for shot in shots:
        image = resolve_image(shots, shot)
        if not image or not (output / image).is_file():
            missing.append(str(shot.get("shot_id")))
    return missing


def preflight(output_dir, audio_seconds: float | None = None) -> dict:
    """{"ok", "checks": [{"name", "ok", "blocking", "detail"}]}: "ok" means nothing blocks the render."""
    from agents.shot_list import resolve_image
    from video.qa import borders
    output = Path(output_dir)
    shots = _load(output / "shot_list.json").get("shots", [])
    checks = []
    missing = missing_images(output, shots)
    checks.append({"name": "imagens", "ok": not missing, "blocking": True,
                   "detail": f"{len(shots) - len(missing)}/{len(shots)} planos com imagem"
                             + (f"; faltam: {', '.join(missing)}" if missing else "")})
    wrong, margins = [], []
    for shot in shots:
        if shot.get("shot_id") in missing or shot.get("reuse_of"):
            continue
        with Image.open(output / resolve_image(shots, shot)) as image:
            rgb = image.convert("RGB")
        if rgb.size != FRAME:
            wrong.append(f"{shot['shot_id']} ({rgb.width}x{rgb.height})")
        if borders.detect(rgb)["margin"]:
            margins.append(shot["shot_id"])
    checks.append({"name": "tamanho", "ok": not wrong, "blocking": False,
                   "detail": "todas 1920x1080" if not wrong else f"fora de 1920x1080: {', '.join(wrong)}"})
    checks.append({"name": "margens", "ok": not margins, "blocking": False,
                   "detail": "sem margens" if not margins else
                   f"margem em {', '.join(margins)} (python -m scripts.fix_borders)"})
    total = audio_seconds if audio_seconds is not None else float(_load(output / "scene_timeline.json").get("total_duration") or 0)
    gaps = []
    if shots and total:
        ordered = sorted(shots, key=lambda shot: float(shot.get("start", 0)))
        cursor = 0.0
        for shot in ordered:
            if float(shot["start"]) - cursor > COVERAGE_TOLERANCE:
                gaps.append(f"{cursor:.1f}-{float(shot['start']):.1f} s")
            cursor = max(cursor, float(shot["end"]))
        if total - cursor > COVERAGE_TOLERANCE:
            gaps.append(f"{cursor:.1f}-{total:.1f} s")
    checks.append({"name": "tempos", "ok": not gaps, "blocking": False,
                   "detail": f"os planos cobrem os {total:.0f} s da narração" if not gaps else
                   f"narração sem plano em {', '.join(gaps)}"})
    return {"ok": not any(item["blocking"] and not item["ok"] for item in checks), "checks": checks}
