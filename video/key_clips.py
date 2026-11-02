"""Short animated clips for key moments (Phase 3.9-B.3): optional, off by default.

``KEY_MOMENT_CLIPS = 0..3``: image-to-video for the hook, the reversal and the final image, starting
from the approved still, through a local ComfyUI workflow that fits 8 GB of VRAM (Wan 2.2 TI2V-5B,
Apache-2.0, at 832×480, 3-4 s). ``probe()`` first checks what can actually run: the core nodes, the
model files and, with ``measure=True``, the minutes per clip. The feature stays off unless the probe
finds a model that renders a 3-4 s clip in under ``MAX_MINUTES``.

Clips keep the engraving style (the shot's own prompt and style suffix) and are checked against the
reference sheet with the layer-1 checks; on any failure the shot keeps its still with parallax.
Never a realistic look, so no synthetic-content disclosure is triggered by them.
"""
from __future__ import annotations

import time

import config

MAX_MINUTES = 10.0
NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "Wan22ImageToVideoLatent", "KSampler",
         "VAEDecode", "CreateVideo", "SaveVideo")
MODELS = {"diffusion_models": "wan2.2_ti2v_5B", "text_encoders": "umt5_xxl", "vae": "wan2.2_vae"}
MOMENTS = ("hook", "reversal", "ending")


def count() -> int:
    try:
        return max(0, min(3, int(getattr(config, "KEY_MOMENT_CLIPS", 0))))
    except (TypeError, ValueError):
        return 0


def enabled() -> bool:
    return count() > 0


def _get(client, path: str):
    return client.session.get(f"{client.base_url}{path}", timeout=client.timeout).json()


def probe(client=None, *, measure: bool = False) -> dict:
    """What would run here: {"runs", "reason", "nodes", "models", "minutes_per_clip"}."""
    from video.imagegen_comfyui import ComfyClient
    client = client or ComfyClient(timeout=10)
    if not client.available():
        return {"runs": False, "reason": "ComfyUI is not running"}
    info = client.object_info()
    missing_nodes = [node for node in NODES if node not in info]
    models = {}
    for folder, stem in MODELS.items():
        try:
            files = _get(client, f"/models/{folder}")
        except Exception:                                # noqa: BLE001 — a folder ComfyUI does not list
            files = []
        models[folder] = next((name for name in files if stem.lower() in name.lower()), None)
    missing_models = [f"{folder}/{stem}*" for folder, stem in MODELS.items() if not models[folder]]
    if missing_nodes or missing_models:
        return {"runs": False, "nodes": missing_nodes, "models": models,
                "reason": "missing " + ", ".join(missing_nodes + missing_models)}
    report = {"runs": True, "models": models, "reason": "", "minutes_per_clip": None}
    if measure:
        report["minutes_per_clip"] = None                 # filled by scripts.probe_key_clips with a real render
    return report


def workflow(image_name: str, prompt: str, models: dict, *, seconds: float = 3.5, fps: int = 24,
             width: int = 832, height: int = 480, seed: int = 1, prefix: str = "key_clip") -> dict:
    """A Wan 2.2 TI2V-5B image-to-video graph (core ComfyUI nodes only)."""
    frames = int(round(seconds * fps / 4)) * 4 + 1          # Wan wants 4k+1 frames
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": models["diffusion_models"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["text_encoders"], "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0],
                                                       "text": "photorealistic, photo, 3d render, text, watermark"}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "start_image": ["4", 0],
                                                                  "width": width, "height": height, "length": frames,
                                                                  "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0],
                                                   "latent_image": ["7", 0], "seed": seed, "steps": 20, "cfg": 5.0,
                                                   "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": fps}},
        "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": prefix,
                                                     "format": "mp4", "codec": "h264"}},
    }


def timed_probe(client, image_name: str, prompt: str, models: dict) -> float:
    """Minutes for one real 3.5-s clip (used only by scripts.probe_key_clips)."""
    started = time.monotonic()
    client.wait(client.queue(workflow(image_name, prompt, models)), timeout=MAX_MINUTES * 60 * 3)
    return round((time.monotonic() - started) / 60, 1)
