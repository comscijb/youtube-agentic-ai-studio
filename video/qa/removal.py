"""A "Remover figura" that really removes: mask inpainting (Phase 3.9.4-B, repainting since 3.9.6).

Klein's reference edit preserves what it sees, so asking it to remove a figure redraws the figure. Here the
masked pixels never reach the model:

1. **Mask:** the user's brush, or their box refined to the detected person, dilated and extended down over
   the cast shadow, feathered.
2. **Erase:** LaMa on the crop (the ComfyUI node pack, or a local ONNX model), else OpenCV inpainting. Floors,
   walls and sky continue; the figure is gone before any generation.
3. **Repaint (3.9.6):** a masked Klein pass at denoise >= 0.8 with differential diffusion over a soft ring. A low
   denoise keeps the layout of its input, and a fill on this channel's dense hatching is a smear in the exact shape of
   the figure: restored at 0.45 it stayed a silhouette. The mask grows ~22 % beyond the figure so the old contour falls
   inside the repainted area. LaMa is only a low-frequency start, and only for small masks; large ones start from the
   blurred surroundings. The prompt says what belongs there (the user's "Substituir por" first), never a person.
4. **Composite** through the protected-pixel path of 3.5.3: outside the mask nothing changes.
5. **Verify:** the layer-1 detector (the person is gone) and the ghost detector (no silhouette left, ``ghosts``).
   A result is good only with both. Up to 3 results wait in the viewer ("Usar este"); nothing replaces the shot until the
   user picks one. When no result is good, the last automatic step regenerates the shot without the figure.
"""
from __future__ import annotations

import re
import shutil
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import config
from agents import identity
from agents import visual_bible as vbible
from studio.filenames import safe_filename
from video.imagegen_comfyui import GenerationFailed, ImageJob
from video.imagegen_templates import TemplateError
from video.qa import fixes, region

FOLDER = Path("images") / "shots" / "removal"
FIGURE_GROW = 0.05            # the mask shown in the viewer: the figure itself
GROW = 0.22
SHADOW = 0.12
FEATHER_PIXELS = (6, 12)
SOFT_RING = 0.04              # differential diffusion's gradient ring, share of the crop side
BLUR_INIT_SHARE = 0.12        # over this share of the crop, start from the blurred surroundings instead of LaMa
LARGE_SHARE = 0.15            # over this share of the frame, regenerating beats repainting
MAX_RESULTS = 3
# The 3.9.6 ladder, measured on s01_04 and s07_07 (docs/reports/3.9.6/probe_ladder.json): at 0.75 the blurred start stays a
# flat grey patch (texture 0.09-0.14, a ghost); from 0.85 the hatching comes back (1.4-2.0). A ghost or a person moves one step up.
PROFILES = ({"name": "base", "grow": GROW, "denoise": 0.85},
            {"name": "denoise 0,92", "grow": GROW, "denoise": 0.92},
            {"name": "máscara maior", "grow": 0.35, "denoise": 0.95},
            {"name": "gerar de novo", "regenerate": True})
ERASE_ONLY = {"name": "só apagar", "grow": GROW, "denoise": 0.0}       # manual only, never automatic
SEED_OFFSET = 5_000_011
PERSON_WORDS = re.compile(r"\b(?:person|people|figures?|man|men|woman|women|boy|girl|child|children|someone|anyone|nobody|"
                          r"silhouettes?|baker|customers?|crowd|townsfolk|guests?|staff|remove|removed|removal|erase|erased|"
                          r"character|presence|deserted|unoccupied)\b", re.I)
ONNX_MODEL = "models/lama/big-lama.onnx"
LAMA_NODES = ("INPAINT_LoadInpaintModel", "INPAINT_InpaintWithModel")
LAMA_NAME = "big-lama"


# ── Mask ─────────────────────────────────────────────────────────────────────

def _covered(person: list, box: list) -> float:
    return region._intersection(person, box) / max((person[2] - person[0]) * (person[3] - person[1]), 1e-9)


def refine_box(box: list, persons: list[dict]) -> tuple[list, bool]:
    """The detected person the user's box mostly covers, or the box itself."""
    best = max((person["box"] for person in persons), key=lambda item: _covered(item, box), default=None)
    if best is not None and _covered(best, box) >= 0.5:
        return list(best), True
    return list(box), False


def _grow_parts(hard: np.ndarray, grow: float) -> np.ndarray:
    """Each painted part grown by ``grow`` of its own width (sideways) and half that of its height: three figures painted
    far apart stay three regions, not one band."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats(hard.astype(np.uint8), connectivity=8)
    grown = np.zeros_like(hard, dtype=bool)
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < 4:
            continue
        rx, ry = max(1, round(grow * w)), max(1, round(grow * h / 2))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rx + 1, 2 * ry + 1))
        grown |= cv2.dilate((labels == label).astype(np.uint8), kernel) > 0
    return grown


def build_mask(size: tuple, box: list | None, *, persons=(), painted: Image.Image | None = None, grow: float = GROW,
               shadow: float = SHADOW) -> Image.Image:
    """Hard mask ("L", image size). Painted by hand, each part grows by ``grow``; from a box it follows the detected
    person, dilated by ``grow`` of its size on each side, and runs further down for the shadow and the feet's contact.
    The repaint masks grow ~22 %: the old contour must fall inside the repainted area."""
    width, height = size
    if painted is not None:
        hard = np.asarray(painted.convert("L").resize(size, Image.BILINEAR)) > 127
        if not hard.any():
            raise ValueError("the mask is empty")
        return Image.fromarray((_grow_parts(hard, grow) * 255).astype(np.uint8)) if grow > 0 else Image.fromarray((hard * 255).astype(np.uint8))
    if not box:
        raise ValueError("a mask needs a box or a brush")
    target, _ = refine_box(box, persons)
    bw, bh = (target[2] - target[0]) * width, (target[3] - target[1]) * height
    left, top = target[0] * width - bw * grow, target[1] * height - bh * grow
    right, bottom = target[2] * width + bw * grow, target[3] * height + bh * (grow + shadow)
    hard = Image.new("L", size, 0)
    hard.paste(255, (max(0, round(left)), max(0, round(top)), min(width, round(right)), min(height, round(bottom))))
    return hard


def soft_mask(hard: Image.Image, ring: int) -> Image.Image:
    """Full strength inside, falling to nothing over ``ring`` pixels outside: differential diffusion blends the border."""
    import cv2
    inside = np.asarray(hard.convert("L")) > 127
    distance = cv2.distanceTransform((~inside).astype(np.uint8), cv2.DIST_L2, 5)
    values = np.clip(1.0 - distance / max(ring, 1), 0.0, 1.0)
    return Image.fromarray((values * 255).astype(np.uint8))


def share(hard: Image.Image) -> float:
    return float((np.asarray(hard.convert("L")) > 127).mean())


def regenerate_suggested(hard_full: Image.Image, overlaps: bool = False) -> bool:
    """Over ~15 % of the frame, or on a listed character: regenerating the shot beats repainting it (3.9.6-D)."""
    return overlaps or share(hard_full) > LARGE_SHARE


def feather_radius(hard: Image.Image) -> int:
    """6-12 px, by the mask's size."""
    found = hard.getbbox() or (0, 0, 1, 1)
    return int(min(FEATHER_PIXELS[1], max(FEATHER_PIXELS[0], min(found[2] - found[0], found[3] - found[1]) // 16)))


def feather(hard: Image.Image, radius: int | None = None) -> Image.Image:
    """Soft inside the hard mask only: pixels outside it stay untouched by construction."""
    radius = radius or feather_radius(hard)
    inner = hard.filter(ImageFilter.MinFilter(radius * 2 + 1)) if radius * 2 + 1 <= 31 else hard
    blurred = inner.filter(ImageFilter.GaussianBlur(radius / 2))
    return Image.fromarray(np.minimum(np.asarray(blurred), np.asarray(hard)))


def mask_box(hard: Image.Image) -> list[float]:
    left, top, right, bottom = hard.getbbox()
    return [round(left / hard.width, 4), round(top / hard.height, 4), round(right / hard.width, 4), round(bottom / hard.height, 4)]


def tone_ring(hard_crop: Image.Image) -> Image.Image:
    """The band just outside the mask, for the colour match."""
    size = max(3, round(region.RING * max(hard_crop.size)) * 2 + 1)
    grown = hard_crop.filter(ImageFilter.MaxFilter(min(size | 1, 31)))
    return Image.fromarray(np.where(np.asarray(hard_crop) > 0, 0, np.asarray(grown)).astype(np.uint8))


# ── Erase ────────────────────────────────────────────────────────────────────

def _lama_models(object_info: dict) -> list[str]:
    """big-lama options of the loader, in either /object_info shape: [[...]] or ["COMBO", {"options": [...]}]."""
    spec = object_info.get(LAMA_NODES[0], {}).get("input", {}).get("required", {}).get("model_name") or [[]]
    options = spec[1].get("options", []) if spec[0] == "COMBO" and len(spec) > 1 else spec[0]
    return [str(option) for option in options if LAMA_NAME in str(option)]


def eraser_available(object_info: dict) -> str:
    """"lama_comfy" with the node pack and big-lama, "lama_onnx" with a local model, else "opencv"."""
    if all(name in object_info for name in LAMA_NODES) and _lama_models(object_info):
        return "lama_comfy"
    if Path(ONNX_MODEL).is_file():
        return "lama_onnx"
    return "opencv"


def erase_opencv(image: Image.Image, mask: Image.Image) -> Image.Image:
    import cv2
    pixels = cv2.inpaint(np.asarray(image.convert("RGB"))[..., ::-1].copy(), np.asarray(mask.convert("L")), 9, cv2.INPAINT_TELEA)
    return Image.fromarray(pixels[..., ::-1].copy())


def erase_onnx(image: Image.Image, mask: Image.Image, model_path=ONNX_MODEL) -> Image.Image:
    """LaMa exported to ONNX, on the CPU (image and mask at 512 px, the result back at the crop's size)."""
    import onnxruntime
    session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    size = (512, 512)
    pixels = np.asarray(image.convert("RGB").resize(size, Image.LANCZOS), dtype=np.float32).transpose(2, 0, 1)[None] / 255
    hole = (np.asarray(mask.convert("L").resize(size, Image.NEAREST), dtype=np.float32) > 127).astype(np.float32)[None, None]
    names = [item.name for item in session.get_inputs()]
    output = session.run(None, {names[0]: pixels, names[1]: hole})[0][0]
    output = output if output.max() > 1.5 else output * 255
    return Image.fromarray(np.clip(output.transpose(1, 2, 0), 0, 255).astype(np.uint8)).resize(image.size, Image.LANCZOS)


def lama_graph(image_name: str, mask_name: str, seed: int, object_info: dict, prefix: str) -> dict:
    """LaMa through the comfyui-inpaint-nodes pack."""
    model = _lama_models(object_info)[0]
    inputs = {"inpaint_model": ["1", 0], "image": ["2", 0], "mask": ["3", 0]}
    if "seed" in object_info[LAMA_NODES[1]]["input"]["required"]:
        inputs["seed"] = int(seed) % (2 ** 63)
    return {"1": {"class_type": LAMA_NODES[0], "inputs": {"model_name": model}},
            "2": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "3": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
            "4": {"class_type": LAMA_NODES[1], "inputs": inputs},
            "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": prefix}}}


def erase_comfy(client, image_path, mask_path, seed: int, prefix: str = "private_gravity/lama") -> Image.Image:
    graph = lama_graph(client.upload_image(image_path), client.upload_image(mask_path), seed, client.object_info(), prefix)
    outputs = client.wait(client.queue(graph), timeout=float(getattr(config, "IMAGEGEN_TIMEOUT_SECONDS", 900)))
    images = [item for output in outputs.values() for item in output.get("images", [])]
    if not images:
        raise GenerationFailed("LaMa returned no image")
    with Image.open(BytesIO(client.fetch(images[0]))) as image:
        return image.convert("RGB")


# ── Restore prompt ───────────────────────────────────────────────────────────

def surroundings(text: str) -> str:
    """``text`` without any clause that names a person or the removal."""
    clauses = [clause.strip() for clause in re.split(r"[.;,]\s+|\.$", str(text)) if clause.strip()]
    return ", ".join(clause for clause in clauses if not PERSON_WORDS.search(clause))


def restore_prompt(vb: dict, shot: dict, replace: str = "") -> str:
    """What belongs behind the figure, in positive words: the user's "Substituir por" first, then the place (its descriptor,
    the shot's setting and the person-free parts of its action), then the style. Naming people draws them when the negative
    is zeroed, and naming nothing lets the model trace the old shape."""
    location = vbible.location(vb, shot.get("location")) or {}
    wanted = surroundings(str(replace or "").strip().rstrip("."))
    parts = list(dict.fromkeys(part for part in (surroundings(location.get("descriptor", "")),
                                                 surroundings(shot.get("setting_condition", "")),
                                                 surroundings(shot.get("action", ""))) if part))
    exterior = bool(location) and not vbible.location_interior(location)
    style = surroundings(identity.style_suffix(figures=False, exterior=exterior))
    place = ", ".join(parts)
    lead = f"{wanted[:1].upper()}{wanted[1:]} fills this part of the picture. " if wanted else ""
    return f"{lead}{place}. Draw the surface right through, dense ink hatching like the rest. {style}".strip(". ") + "."


# ── The removal ──────────────────────────────────────────────────────────────

def _save_mask(hard: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    hard.save(path, "PNG")


def _start(generator, engine: str, crop_image: Image.Image, edit_mask: Image.Image, folder: Path, stem: str, seed: int) -> tuple[Image.Image, str]:
    """The repaint's starting point: LaMa (a low-frequency hint) for small masks when its fill has no blocks, else the
    blurred surroundings. Returns (image, "lama_comfy" | "lama_onnx" | "opencv" | "blur")."""
    from video.qa import ghosts
    if engine == "blur" or share(edit_mask) > BLUR_INIT_SHARE:
        return ghosts.blur_fill(crop_image, edit_mask), "blur"
    if engine == "lama_comfy":
        erased = erase_comfy(generator.client, folder / f"{stem}_crop.png", folder / f"{stem}_crop_mask.png", seed)
    elif engine == "lama_onnx":
        erased = erase_onnx(crop_image, edit_mask)
    else:
        erased = erase_opencv(crop_image, edit_mask)
    erased = erased.resize(crop_image.size, Image.LANCZOS)
    if ghosts.score(erased, edit_mask)["blocks"] > ghosts.BLOCK_MAX:
        return ghosts.blur_fill(crop_image, edit_mask), "blur"
    return erased, engine


def attempt(generator, output: Path, shot: dict, vb: dict, source: Image.Image, hard: Image.Image, profile: dict, *,
            engine: str, number, seed: int, figure: Image.Image | None = None, replace: str = "") -> dict:
    """One repaint on the crop, composited back and scored for ghosts. The candidate image and its record."""
    from video.qa import copy_check, ghosts
    folder = output / FOLDER
    stem = f"{safe_filename(shot['shot_id'])}_{number}"
    box = mask_box(hard)
    crop = region.crop_window(box, source.size)
    size = region.edit_size(crop)
    crop_image = source.crop(crop).resize(size, Image.LANCZOS)
    hard_crop = hard.crop(crop)
    edit_mask = hard_crop.resize(size, Image.NEAREST)
    _save_mask(edit_mask, folder / f"{stem}_crop_mask.png")
    crop_image.save(folder / f"{stem}_crop.png")
    started = time.monotonic()
    start, used = _start(generator, engine, crop_image, edit_mask, folder, stem, seed)
    erase_seconds = time.monotonic() - started
    erased_path = folder / f"{stem}_erased.png"
    start.save(erased_path)
    started = time.monotonic()
    finished = start
    denoise = float(profile.get("denoise", 0.0))
    differential = "DifferentialDiffusion" in (generator.client.object_info() or {})
    if denoise > 0:
        ring = max(4, round(SOFT_RING * min(size)))
        repaint_mask = soft_mask(edit_mask, ring) if differential else edit_mask
        _save_mask(repaint_mask, folder / f"{stem}_repaint_mask.png")
        location_ref = vbible.approved_reference(vb, "location", shot.get("location") or "", output)
        style_refs = [path for path in copy_check.boards()[:1] if Path(path).is_file()]
        after = folder / f"{stem}_restored.jpg"
        generator.generate(ImageJob(job_id=f"{shot['shot_id']}_remove{number}", prompt=restore_prompt(vb, shot, replace), seed=seed,
                                    output_path=str(after), kind="masked_restore", size=size, final_size=size, denoise=denoise,
                                    differential=differential,
                                    references=[str(erased_path), str(folder / f"{stem}_repaint_mask.png")]
                                    + ([location_ref] if location_ref else []) + style_refs))
        with Image.open(after) as restored:
            finished = restored.convert("RGB")
    restore_seconds = time.monotonic() - started
    result = region.composite(source, finished, crop, feather(hard_crop), tone_ring(hard_crop))
    change = region.check(source, result, crop, hard_crop)
    if change < region.MIN_CHANGE:
        raise region.NoEffect(f"nothing changed inside the mask (mean difference {change:.1f})")
    path = folder / f"{stem}_candidate.jpg"
    result.save(path, "JPEG", quality=93)
    contour = figure.crop(crop) if figure is not None else None
    ghost = ghosts.score(result.crop(crop), hard_crop, contour)
    return {"path": (FOLDER / path.name).as_posix(), "profile": profile["name"], "denoise": round(denoise, 2), "engine": used,
            "differential": differential and denoise > 0, "seed": seed, "inside_change": round(change, 2), "box": box,
            "crop": list(crop), "ghost": ghost, "erase_seconds": round(erase_seconds, 2), "restore_seconds": round(restore_seconds, 2)}


def regenerate_job(factory, shot: dict, offset: int):
    """The shot's own job, built as if nobody else were there, with the headcount said outright."""
    job = factory({**shot, "others_present": False})(offset)
    listed = len(shot.get("characters") or [])
    count = "No people" if not listed else f"Exactly {listed} {'person' if listed == 1 else 'people'}"
    job.prompt = f"{job.prompt} {count} in the whole frame, nobody else anywhere, no background figures."
    return job


def regenerate_without(generator, output, shot: dict, vb: dict, job_factory, *, progress=print) -> dict:
    """"Gerar o plano de novo sem a figura": a new seed through the normal regenerate path (history, QA). The removal
    candidates stay on the shot for comparison."""
    image = Path(output) / shot["image_path"]
    kept = shot.get("removal")
    action = fixes.apply(generator, Path(output), shot, vb, image_path=image, force="regenerate", progress=progress,
                         job_factory=lambda offset: regenerate_job(job_factory, shot, offset))
    if kept:
        shot["removal"] = kept
    action["request"] = "Gerar o plano de novo sem a figura"
    return action


def run(generator, output, shot: dict, vb: dict, request: dict, *, detector=None, engine: str | None = None,
        job_factory=None, progress=print) -> dict:
    """Up to MAX_RESULTS candidates for one figure, stored on ``shot["removal"]``; the shot's image is not touched, unless
    no candidate is good and ``job_factory`` is given: then the last step regenerates the shot without the figure."""
    output = Path(output)
    image_path = output / shot["image_path"]
    with Image.open(image_path) as opened:
        source = opened.convert("RGB")
    detector = fixes._detector() if detector is None else detector
    before = detector.detect(image_path) if detector else {"persons": []}
    engine = engine or eraser_available(generator.client.object_info())
    painted = None
    if request.get("mask") and (output / request["mask"]).is_file():
        with Image.open(output / request["mask"]) as opened:
            painted = opened.convert("L")
    replace = str(request.get("replace") or "")
    results, errors = [], []
    qa = fixes.state(shot)
    base = shot.get("seed", 0) + fixes.EDIT_SEED_OFFSET + SEED_OFFSET * (int(qa.get("human_edits", 0)) + 1)
    run_id = f"{int(time.time())}"
    figure = build_mask(source.size, request.get("box"), persons=before.get("persons", []), painted=painted, grow=0.0)

    def one(profile: dict, label) -> dict | None:
        try:
            hard = build_mask(source.size, request.get("box"), persons=before.get("persons", []), painted=painted, grow=profile["grow"])
            item = attempt(generator, output, shot, vb, source, hard, profile, engine=engine, number=f"{run_id}_{label}",
                           seed=base + (label if isinstance(label, int) else 0), figure=figure, replace=replace)
        except (GenerationFailed, TemplateError, region.NoEffect, OSError, ValueError) as exc:
            errors.append(f"{profile['name']}: {str(exc)[:160]}")
            return None
        boxes = [item["box"]]
        inside_before = region.persons_inside(before, boxes)
        inside_after = region.persons_inside(detector.detect(output / item["path"]), boxes) if detector else inside_before
        gone = inside_after == 0 or inside_after < inside_before
        item.update(people_inside=[inside_before, inside_after], clean=gone and not item["ghost"]["ghost"], person_gone=gone)
        mask_path = FOLDER / f"{safe_filename(shot['shot_id'])}_{run_id}_{label}_mask.png"
        _save_mask(hard, output / mask_path)
        item["mask"] = mask_path.as_posix()
        results.append(item)
        verdict = "silhueta visivel" if item["ghost"]["ghost"] else "sem silhueta"
        progress(f"   {shot['shot_id']}: {profile['name']} ({item['engine']}): pessoas na mascara {inside_before} -> {inside_after}, {verdict}")
        return item

    if request.get("erase_only"):
        one(ERASE_ONLY, "e")
    else:
        ladder = 0
        for number in range(1, MAX_RESULTS + 1):                  # at most 3 repaints; a good one keeps its profile
            item = one(PROFILES[min(ladder, len(PROFILES) - 2)], number)
            if not item or not item["clean"]:
                ladder += 1
    results.sort(key=lambda item: (item["ghost"]["ghost"], not item.get("person_gone", item.get("clean"))))
    action = {"action": "removal_candidates" if results else "no_effect", "origin": "human", "request": "Remover figura",
              "engine": engine, "results": len(results), "clean": sum(1 for item in results if item.get("clean")),
              "ghosts": sum(1 for item in results if item["ghost"]["ghost"]), "errors": errors, "at": fixes._now()}
    qa["actions"].append(action)
    if results:
        shot["removal"] = {"box": request.get("box"), "engine": engine, "candidates": results, "replace": replace, "at": fixes._now(),
                           "regenerate_suggested": regenerate_suggested(figure)}
    else:
        progress(f"   {shot['shot_id']}: edição sem efeito, nada salvo ({'; '.join(errors)})")
    if not request.get("erase_only") and job_factory and not any(item.get("clean") for item in results):
        progress(f"   {shot['shot_id']}: nenhum resultado sem silhueta; gerando o plano de novo sem a figura")
        regenerated = regenerate_without(generator, output, shot, vb, job_factory, progress=progress)
        action["regenerated"] = regenerated.get("action")
    return action


def choose(output, shot: dict, index: int) -> dict:
    """"Usar este": the candidate becomes the shot; the image before it stays in the history."""
    output = Path(output)
    candidates = (shot.get("removal") or {}).get("candidates") or []
    if not 0 <= index < len(candidates):
        raise IndexError("no such result")
    chosen = candidates[index]
    image_path = output / shot["image_path"]
    qa = fixes.state(shot)
    version = fixes.save_version(output, shot, image_path)
    shutil.copy2(output / chosen["path"], image_path)
    number = int(qa.get("human_edits", 0)) + 1
    qa["human_edits"] = number
    action = {"action": "edit", "origin": "human", "request": "Remover figura", "kinds": ["remove_figure"], "number": number,
              "replaced_version": version["version"], "mask": chosen.get("mask"), "candidate": index + 1, "engine": chosen.get("engine"),
              "profile": chosen.get("profile"), "boxes": [chosen.get("box")], "crop": chosen.get("crop"), "at": fixes._now(),
              "verify": {"people_inside": chosen.get("people_inside"), "inside_change": chosen.get("inside_change")}}
    qa["actions"].append(action)
    qa.update(status="pending", verdict="recheck", flagged=False, reviewed=False)
    shot.pop("removal", None)
    return action
