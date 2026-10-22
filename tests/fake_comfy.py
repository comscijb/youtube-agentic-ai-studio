"""In-memory ComfyUI HTTP API used by the image-generation tests (no network)."""
from __future__ import annotations

import io
from types import SimpleNamespace

from PIL import Image

MODELS = {
    "unet": "flux3-image-klein-int8-convrot.safetensors",
    "clip": "qwen3vl_4b_int8_convrot.safetensors",
    "vae": "flux2-vae.safetensors",
}

OBJECT_INFO = {
    "UNETLoader": {"input": {"required": {"unet_name": [[MODELS["unet"]]],
                                          "weight_dtype": [["default", "fp8"]]}}},
    "CLIPLoader": {"input": {"required": {"clip_name": [[MODELS["clip"], "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors"]],
                                          "type": [["flux3", "flux2"]]},
                             "optional": {"device": [["default", "cpu"]]}}},
    "VAELoader": {"input": {"required": {"vae_name": [[MODELS["vae"]]]}}},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}], "clip": ["CLIP"]}}},
    "ConditioningZeroOut": {"input": {"required": {"conditioning": ["CONDITIONING"]}}},
    "EmptyFlux2LatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}],
                                                     "batch_size": ["INT", {}]}}},
    "KSampler": {"input": {"required": {name: ["X"] for name in (
        "seed", "steps", "cfg", "denoise", "model", "positive", "negative", "latent_image")} | {
        "sampler_name": [["euler", "dpmpp_2m"]], "scheduler": [["simple", "normal"]]}}},
    "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
    "SaveImage": {"input": {"required": {"filename_prefix": ["STRING", {}], "images": ["IMAGE"]}}},
    "LoadImage": {"input": {"required": {"image": [["example.png"]]}}},
    "ReferenceMultiLatent": {"input": {"required": {
        "positive": ["CONDITIONING"], "negative": ["CONDITIONING"], "vae": ["VAE"],
        "resolution": ["COMFY_DYNAMICCOMBO_V3", {}], "images": ["COMFY_AUTOGROW_V3", {}]}}},
    "TextGenerate": {"input": {"required": {"prompt": ["STRING", {}], "clip": ["CLIP"],
                                            "max_length": ["INT", {}], "sampling_mode": ["X"]},
                               "optional": {"image": ["IMAGE"], "system_prompt": ["STRING", {}],
                                            "thinking": ["BOOLEAN"], "use_default_template": ["B"],
                                            "mtp": [["off", "on"]]}}},
    "PrimitiveStringMultiline": {"input": {"required": {"value": ["STRING", {}]}}},
    "BatchImagesNode": {"input": {"required": {"images": ["COMFY_AUTOGROW_V3", {}]}}},
}


def png_bytes(size=(1360, 752), color=(40, 35, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class Response:
    def __init__(self, payload=None, status=200, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = str(payload)[:300]

    def json(self):
        return self._payload


class FakeComfy:
    """Requests-compatible session. ``fail_runs`` makes the next N runs error."""

    def __init__(self, fail_runs=0, object_info=None, image=None, offline=False):
        self.fail_runs = fail_runs
        self.object_info = object_info or OBJECT_INFO
        self.image = image or png_bytes()
        self.offline = offline
        self.uploads = []
        self.prompts = []
        self.freed = 0
        self.calls = []

    def request(self, method, url, **kwargs):
        import requests
        path = url.split("8188", 1)[-1]
        self.calls.append((method, path))
        if self.offline:
            raise requests.ConnectionError("refused")
        if path == "/system_stats":
            return Response({"devices": [{"vram_total": 8 * 2 ** 30, "vram_free": 3 * 2 ** 30}]})
        if path == "/object_info":
            return Response(self.object_info)
        if path == "/upload/image":
            name, data, _ = kwargs["files"]["image"]
            self.uploads.append((name, len(data)))
            return Response({"name": name})
        if path == "/prompt":
            self.prompts.append(kwargs["json"]["prompt"])
            return Response({"prompt_id": f"p{len(self.prompts)}", "node_errors": {}})
        if path.startswith("/history/"):
            prompt_id = path.rsplit("/", 1)[-1]
            if self.fail_runs:
                self.fail_runs -= 1
                return Response({prompt_id: {"status": {"status_str": "error", "completed": False,
                                                        "messages": [["execution_error", {"m": "OOM"}]]},
                                             "outputs": {}}})
            return Response({prompt_id: {"status": {"status_str": "success", "completed": True},
                                         "outputs": {"9": {"images": [
                                             {"filename": f"{prompt_id}.png", "subfolder": "",
                                              "type": "output"}]}}}})
        if path == "/view":
            return Response(content=self.image)
        if path == "/free":
            self.freed += 1
            return Response({})
        return Response({"error": "unknown"}, status=404)


def namespace(**kwargs):
    return SimpleNamespace(**kwargs)
