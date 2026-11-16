"""Sharp crops for the derived views (Phase 4.2-A).

A crop of 40-60 % of a 1280x720 image is soft at 1920x1080. ``probe`` asks ComfyUI which upscale models it has
(``UpscaleModelLoader`` + ``ImageUpscaleWithModel``); ``ensure`` upscales only the images that get crops, once, into
``images/shots/upscaled/<shot_id>.png`` (stored at 2x, which is enough for a 50 % crop at 1080p). Without a model
nothing is installed: the crops use Lanczos (the film grain hides some softness) and stay ≥ 60 % of the frame.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

import config

FOLDER = Path("images/shots/upscaled")
STORED_SCALE = 2
INSTALL_OPTIONS = [
    {"name": "4x-UltraSharp", "license": "CC BY-NC-SA 4.0 (non-commercial: not for a monetised channel)",
     "where": "https://openmodeldb.info/models/4x-UltraSharp"},
    {"name": "RealESRGAN_x4plus / x4plus_anime_6B", "license": "BSD-3-Clause (commercial use allowed)",
     "where": "https://github.com/xinntao/Real-ESRGAN/releases"},
    {"name": "4x_NMKD-Siax_200k", "license": "WTFPL (commercial use allowed)", "where": "https://openmodeldb.info/models/4x-NMKD-Siax-CX"},
]


def probe(client=None) -> dict:
    """{"available", "node", "models", "chosen", "install_options"}; never installs anything."""
    from video.imagegen_comfyui import ComfyClient
    client = client or ComfyClient()
    try:
        info = client.object_info()
    except Exception as exc:                             # noqa: BLE001 — ComfyUI off: Lanczos
        return {"available": False, "reason": f"ComfyUI indisponível ({exc.__class__.__name__})", "models": [],
                "install_options": INSTALL_OPTIONS}
    node = "ImageUpscaleWithModel" in info
    loader = (info.get("UpscaleModelLoader") or {}).get("input", {}).get("required", {}).get("model_name") or []
    models = []
    if loader:
        options = loader[1].get("options") if len(loader) > 1 and isinstance(loader[1], dict) else None
        models = list(options if options is not None else loader[0] if isinstance(loader[0], list) else [])
    wanted = str(getattr(config, "UPSCALE_MODEL", "") or "")
    chosen = wanted if wanted in models else (models[0] if models else "")
    return {"available": bool(node and chosen), "node": node, "models": models, "chosen": chosen,
            "install_options": [] if chosen else INSTALL_OPTIONS,
            "reason": "" if chosen else "nenhum modelo de upscale no ComfyUI"}


def graph(uploaded: str, model: str, prefix: str) -> dict:
    return {"1": {"class_type": "LoadImage", "inputs": {"image": uploaded}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": prefix}}}


def cached_path(output_dir, shot_id: str) -> Path:
    return Path(output_dir) / FOLDER / f"{shot_id}.png"


def ensure(output_dir, shot_id: str, image_path, *, client=None, model: str | None = None) -> Path | None:
    """The upscaled image (cached), or None when no model is available or the upscale failed."""
    target = cached_path(output_dir, shot_id)
    if target.is_file() and target.stat().st_mtime >= Path(image_path).stat().st_mtime:
        return target
    from video.imagegen_comfyui import ComfyClient
    client = client or ComfyClient()
    model = model or probe(client).get("chosen")
    if not model:
        return None
    try:
        outputs = client.wait(client.queue(graph(client.upload_image(image_path), model, f"pg_upscale_{shot_id}")))
        images = [image for node in outputs.values() for image in node.get("images", [])]
        data = client.fetch(images[0])
    except Exception as exc:                             # noqa: BLE001 — the crop falls back to Lanczos
        print(f"   ⚠ upscale {shot_id} failed ({exc.__class__.__name__}); Lanczos")
        return None
    import io
    with Image.open(io.BytesIO(data)) as opened, Image.open(image_path) as source:
        size = (source.width * STORED_SCALE, source.height * STORED_SCALE)
        target.parent.mkdir(parents=True, exist_ok=True)
        opened.convert("RGB").resize(size, Image.LANCZOS).save(target)
    return target


def crop_image(image: Image.Image, box, size: tuple[int, int]) -> Image.Image:
    """The normalised ``box`` of ``image`` resized to ``size`` with Lanczos."""
    x0, y0, x1, y1 = box
    width, height = image.size
    region = image.crop((int(round(x0 * width)), int(round(y0 * height)), int(round(x1 * width)), int(round(y1 * height))))
    return region.resize(size, Image.LANCZOS)
