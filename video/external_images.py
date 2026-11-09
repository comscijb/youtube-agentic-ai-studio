"""Images edited outside the studio (Phase 3.9.4-D).

"Exportar para edição" hands the shot, or a crop around a box, to another tool; "Importar imagem editada" takes the
result back at any size, fits it to the frame, runs the same checks as a generated image, and keeps both the image it
replaced and the file as it arrived in the history.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from studio.filenames import safe_filename
from video.qa import borders, copy_check, fixes, region

FRAME = (1920, 1080)
ASPECT_TOLERANCE = 0.08
ALLOWED = {".png", ".jpg", ".jpeg", ".webp"}
EXPORTS = "exports"
BLUR_RADIUS = 40
BLUR_DARKEN = 0.75


class NeedsChoice(ValueError):
    """The aspect is too far from 16:9: the user decides between cropping and extending."""

    def __init__(self, size: tuple, difference: float):
        super().__init__(f"{size[0]}x{size[1]} is {difference * 100:.0f} % away from 16:9")
        self.size, self.difference = size, difference


def aspect_difference(size: tuple) -> float:
    return abs((size[0] / size[1]) / (FRAME[0] / FRAME[1]) - 1)


def crop_window(size: tuple, position: float = 0.5) -> tuple[int, int, int, int]:
    """The largest 16:9 window; ``position`` (0-1) slides it along the free axis."""
    width, height = size
    position = min(max(float(position), 0.0), 1.0)
    if width / height > FRAME[0] / FRAME[1]:
        wide = round(height * FRAME[0] / FRAME[1])
        left = round((width - wide) * position)
        return left, 0, left + wide, height
    tall = round(width * FRAME[1] / FRAME[0])
    top = round((height - tall) * position)
    return 0, top, width, top + tall


def blur_fill(image: Image.Image) -> Image.Image:
    """The whole image, centred, over a blurred and darkened copy of itself."""
    back = ImageOps.fit(image, FRAME, Image.LANCZOS).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    back = ImageEnhance.Brightness(back).enhance(BLUR_DARKEN)
    scale = min(FRAME[0] / image.width, FRAME[1] / image.height)
    front = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    back.paste(front, ((FRAME[0] - front.width) // 2, (FRAME[1] - front.height) // 2))
    return back


def fit_16x9(image: Image.Image, mode: str = "", position: float = 0.5) -> tuple[Image.Image, dict]:
    """1920x1080. Within 8 % of 16:9 a centre crop; beyond it ``mode`` is "crop" or "blur", else NeedsChoice."""
    image = image.convert("RGB")
    difference = aspect_difference(image.size)
    if difference > ASPECT_TOLERANCE and mode not in ("crop", "blur"):
        raise NeedsChoice(image.size, difference)
    info = {"original_size": list(image.size), "aspect_difference": round(difference, 4),
            "upscaled": image.width < FRAME[0] or image.height < FRAME[1]}
    if difference > ASPECT_TOLERANCE and mode == "blur":
        return blur_fill(image), {**info, "fit": "blur"}
    used = position if difference > ASPECT_TOLERANCE else 0.5
    window = crop_window(image.size, used)
    fitted = image.crop(window).resize(FRAME, Image.LANCZOS)
    return fitted, {**info, "fit": "crop" if difference > ASPECT_TOLERANCE else "center", "window": list(window)}


def export_image(output, shot: dict, box=None) -> Path:
    """exports/<shot>.png at full size, or exports/<shot>_crop.png around a box (the ×3 context of the region edits)."""
    output = Path(output)
    with Image.open(output / shot["image_path"]) as opened:
        image = opened.convert("RGB")
    name = safe_filename(shot["shot_id"])
    if box:
        image, name = image.crop(region.crop_window(box, image.size)), f"{name}_crop"
    target = output / EXPORTS / f"{name}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "PNG")
    return target


def run_checks(image: Image.Image, path: Path, *, detector=None, anchors=None) -> dict:
    """The checks a generated image goes through: margin, copy of a style board, people the detector sees."""
    margin = borders.verdict(borders.detect(image))
    copied = copy_check.check(image, (), anchors=anchors)
    people = len(detector.detect(path).get("persons", [])) if detector else None
    return {"margin": margin, "copy": copied, "people": people}


def record_provenance(output, shot: dict, original: str) -> None:
    from agents.narrative import atomic_write_json, load_json
    path = Path(output) / "asset_manifest.json"
    manifest = load_json(path) or {}
    assets = {item.get("job_id"): item for item in manifest.get("generated_assets", [])}
    known = assets.get(shot["shot_id"], {"job_id": shot["shot_id"], "synthetic": True})
    assets[shot["shot_id"]] = {**known, "source": "external", "external_original": original, "at": fixes._now()}
    manifest["generated_assets"] = list(assets.values())
    manifest["synthetic_media"] = any(item.get("synthetic") for item in assets.values())
    atomic_write_json(path, manifest)


def import_image(output, shot: dict, source_path, name: str, *, mode: str = "", position: float = 0.5, detector=None,
                 anchors=None) -> dict:
    """The file at ``source_path`` becomes the shot. NeedsChoice before anything changes; ValueError if not an image."""
    output, source_path = Path(output), Path(source_path)
    suffix = Path(str(name)).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError(f"PNG, JPG or WEBP only (got {suffix or name!r})")
    try:
        with Image.open(source_path) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except OSError as exc:
        raise ValueError("the file is not a readable image") from exc
    fitted, info = fit_16x9(image, mode, position)
    image_path = output / shot["image_path"]
    qa = fixes.state(shot)
    version = fixes.save_version(output, shot, image_path)
    original = fixes.HISTORY / f"{safe_filename(shot['shot_id'])}_v{version['version'] + 1}_external{suffix}"
    (output / original).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, output / original)
    if image_path.suffix.lower() == ".png":
        fitted.save(image_path, "PNG")
    else:
        fitted.save(image_path, "JPEG", quality=95)
    checks = run_checks(fitted, image_path, detector=detector, anchors=anchors)
    labels = []
    if checks["margin"] != "clean":
        labels.append(borders.LABEL)
    shot.pop("copy_check", None)
    if checks["copy"].get("flagged"):
        checks["copy"]["label"] = copy_check.LABEL
        labels.append(copy_check.LABEL)
        shot["copy_check"] = checks["copy"]
    shot.pop("image_flags", None)
    if labels:
        shot["image_flags"] = labels
    shot["source"] = "external"
    number = int(qa.get("human_edits", 0)) + 1
    qa["human_edits"] = number
    action = {"action": "edit", "origin": "human", "request": "Importar imagem editada", "kinds": ["import"], "source": "external",
              "number": number, "replaced_version": version["version"], "original": original.as_posix(), "fit": info,
              "checks": {"margin": checks["margin"], "copy_flagged": bool(checks["copy"].get("flagged")), "people": checks["people"]},
              "at": fixes._now()}
    qa["actions"].append(action)
    qa.update(status="pending", verdict="recheck", flagged=False, reviewed=False)
    shot.pop("removal", None)
    record_provenance(output, shot, original.as_posix())
    return action
