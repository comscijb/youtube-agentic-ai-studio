"""Local image generation through ComfyUI's HTTP API (FLUX 3 Image Klein).

``ComfyClient`` wraps the endpoints used here: ``/system_stats``,
``/object_info``, ``/upload/image``, ``/prompt``, ``/history/{id}``, ``/view``
and ``/free``. ``ImageGenerator`` builds a graph from the derived templates,
validates it against ``/object_info`` once per graph shape, uploads reference
images by content hash, queues, waits, fetches, upscales to the video size and
caches the result by hash(prompt, seed, size, template, models, references).
Failures retry once with a new seed; the caller can then fall back to stock.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable

import requests
from PIL import Image

import config
from agents import identity
from video import imagegen_templates as templates

RETRY_SEED_OFFSET = 1_000_003


class ComfyUnavailable(RuntimeError):
    """ComfyUI is not running or not reachable."""


class GenerationFailed(RuntimeError):
    """ComfyUI accepted the request but did not produce an image."""


def comfy_url() -> str:
    return str(getattr(config, "IMAGEGEN_COMFYUI_URL", "http://127.0.0.1:8188")).rstrip("/")


def file_hash(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ComfyClient:
    def __init__(self, base_url: str | None = None, session=None, timeout: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.base_url = (base_url or comfy_url()).rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleep = sleep
        self.client_id = uuid.uuid4().hex
        self._object_info = None

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path,
                                            timeout=kwargs.pop("timeout", self.timeout), **kwargs)
        except requests.RequestException as exc:
            raise ComfyUnavailable(
                f"ComfyUI is not reachable at {self.base_url} ({exc.__class__.__name__}). "
                "Start ComfyUI Desktop and try again."
            ) from None
        if response.status_code >= 400:
            raise GenerationFailed(f"ComfyUI {path} answered HTTP {response.status_code}: "
                                   f"{response.text[:300]}")
        return response

    def system_stats(self) -> dict:
        return self._request("GET", "/system_stats").json()

    def available(self) -> bool:
        try:
            self.system_stats()
            return True
        except (ComfyUnavailable, GenerationFailed):
            return False

    def vram_used_mb(self) -> float | None:
        try:
            devices = self.system_stats().get("devices") or []
        except (ComfyUnavailable, GenerationFailed):
            return None
        if not devices:
            return None
        device = devices[0]
        return round((device.get("vram_total", 0) - device.get("vram_free", 0)) / 2 ** 20, 1)

    def object_info(self) -> dict:
        if self._object_info is None:
            self._object_info = self._request("GET", "/object_info", timeout=60).json()
        return self._object_info

    def upload_image(self, path) -> str:
        """Upload under a content-hash name (re-uploading the same file is a no-op overwrite)."""
        path = Path(path)
        name = f"pg_{file_hash(path)[:20]}{path.suffix.lower() or '.png'}"
        response = self._request("POST", "/upload/image",
                                 files={"image": (name, path.read_bytes(), "image/png")},
                                 data={"overwrite": "true", "type": "input"})
        return response.json().get("name", name)

    def queue(self, graph: dict) -> str:
        response = self._request("POST", "/prompt",
                                 json={"prompt": graph, "client_id": self.client_id})
        data = response.json()
        if data.get("node_errors"):
            raise GenerationFailed(f"ComfyUI rejected the graph: {json.dumps(data['node_errors'])[:400]}")
        return data["prompt_id"]

    def wait(self, prompt_id: str, timeout: float = 900.0, poll: float = 1.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            history = self._request("GET", f"/history/{prompt_id}").json()
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    messages = [message for message in status.get("messages", [])
                                if message and message[0] == "execution_error"]
                    raise GenerationFailed(f"ComfyUI execution error: {json.dumps(messages)[:400]}")
                if status.get("completed", True) and entry.get("outputs"):
                    return entry["outputs"]
            self.sleep(poll)
        raise GenerationFailed(f"ComfyUI did not finish prompt {prompt_id} within {timeout:.0f}s")

    def fetch(self, image: dict) -> bytes:
        return self._request("GET", "/view", params={
            "filename": image["filename"], "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output")}).content

    def free_memory(self) -> None:
        try:
            self._request("POST", "/free", json={"unload_models": False, "free_memory": True})
        except (ComfyUnavailable, GenerationFailed):
            pass


@dataclass
class ImageJob:
    """One image to generate. ``references`` are local image paths in prompt order."""
    job_id: str
    prompt: str
    seed: int
    output_path: str
    kind: str = "t2i"
    references: list = field(default_factory=list)
    latent_source: str = ""
    size: tuple = templates.DEFAULT_SIZE
    final_size: tuple = (1920, 1080)


def cover_resize(image: Image.Image, size: tuple) -> Image.Image:
    """Lanczos upscale/crop to exactly ``size`` (no letterboxing)."""
    width, height = size
    scale = max(width / image.width, height / image.height)
    resized = image.convert("RGB").resize((round(image.width * scale), round(image.height * scale)),
                                          Image.LANCZOS)
    left, top = (resized.width - width) // 2, (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


class ImageGenerator:
    def __init__(self, output_dir, client: ComfyClient | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.output_dir = Path(output_dir)
        self.cache_dir = self.output_dir / ".imagegen_cache"
        self.client = client or ComfyClient()
        self.clock = clock
        self._validated: set[str] = set()

    def _graph(self, job: ImageJob, seed: int, uploaded: list[str]) -> dict:
        latent = job.latent_source or getattr(config, "IMAGEGEN_LATENT_SOURCE", "reference")
        return templates.build_graph(
            job.kind, prompt=job.prompt, seed=seed, width=job.size[0], height=job.size[1],
            steps=getattr(config, "IMAGEGEN_STEPS", None), references=uploaded,
            latent_source=latent,
            upsampler=bool(getattr(config, "IMAGEGEN_PROMPT_UPSAMPLER", False)),
            filename_prefix=f"private_gravity/{job.job_id}",
        )

    def cache_key(self, job: ImageJob, seed: int) -> str:
        graph = self._graph(job, seed, [f"ref{i}" for i in range(len(job.references))])
        _, _, mapping = templates.load_templates()
        payload = {
            "prompt": job.prompt, "seed": seed, "size": list(job.size), "kind": job.kind,
            "template": templates.graph_hash({k: v for k, v in graph.items()
                                              if k not in {"4", "7", "44"}}),
            "models": templates.model_names(graph, mapping),
            "references": [file_hash(path) for path in job.references],
            "latent": job.latent_source or getattr(config, "IMAGEGEN_LATENT_SOURCE", "reference"),
            "upsampler": bool(getattr(config, "IMAGEGEN_PROMPT_UPSAMPLER", False)),
            "style": identity.style_hash(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _validate(self, graph: dict) -> None:
        shape = templates.graph_hash({node: value["class_type"] for node, value in graph.items()})
        if shape in self._validated:
            return
        problems = templates.validate_graph(graph, self.client.object_info())
        if problems:
            raise templates.TemplateError(
                "The derived FLUX 3 Klein graph does not match this ComfyUI installation: "
                + "; ".join(problems[:6])
                + ". Export this variant from ComfyUI (Workflow > Export (API)) into workflows/ "
                  "instead of the derived template."
            )
        self._validated.add(shape)

    def generate(self, job: ImageJob, seed: int | None = None) -> dict:
        """Generate (or reuse from cache) one image; returns a provenance record."""
        seed = job.seed if seed is None else seed
        key = self.cache_key(job, seed)
        cached = self.cache_dir / f"{key}.png"
        started = self.clock()
        hit = cached.is_file()
        graph = None
        if not hit:
            uploaded = [self.client.upload_image(path) for path in job.references]
            graph = self._graph(job, seed, uploaded)
            self._validate(graph)
            outputs = self.client.wait(self.client.queue(graph),
                                       timeout=float(getattr(config, "IMAGEGEN_TIMEOUT_SECONDS", 900)))
            images = [image for output in outputs.values() for image in output.get("images", [])]
            if not images:
                raise GenerationFailed(f"ComfyUI returned no image for {job.job_id}")
            data = self.client.fetch(images[0])
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(BytesIO(data)) as image:
                image.convert("RGB").save(cached, "PNG")
        target = Path(job.output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(cached) as image:
            cover_resize(image, job.final_size).save(target, "JPEG", quality=93)
        graph = graph or self._graph(job, seed, [Path(path).name for path in job.references])
        _, _, mapping = templates.load_templates()
        return {
            "provider": "comfyui", "synthetic": True, "job_id": job.job_id, "kind": job.kind,
            "visual_style": identity.visual_style(), "style_hash": identity.style_hash(),
            "model": templates.model_names(graph, mapping),
            "workflow_hash": templates.graph_hash(graph), "prompt": job.prompt, "seed": seed,
            "size": list(job.size), "final_size": list(job.final_size),
            "references": [Path(path).name for path in job.references],
            "latent_source": job.latent_source or getattr(config, "IMAGEGEN_LATENT_SOURCE", "reference"),
            "seconds": round(self.clock() - started, 1), "cache_hit": hit,
            "local_path": str(target), "cache_key": key,
        }

    def generate_with_retry(self, job: ImageJob, fallback: Callable[[ImageJob], dict | None] | None = None,
                            new_seeds: int = 1) -> dict:
        """Original seed, then ``new_seeds`` new seeds, then the fallback (e.g. stock)."""
        errors = []
        for seed in [job.seed + RETRY_SEED_OFFSET * attempt for attempt in range(new_seeds + 1)]:
            try:
                record = self.generate(job, seed)
                if errors:
                    record["retried_after"] = errors
                return record
            except ComfyUnavailable:
                raise
            except (GenerationFailed, OSError, ValueError) as exc:
                errors.append(f"seed {seed}: {exc}")
        if fallback is not None:
            record = fallback(job)
            if record:
                record.update({"fallback_from": "comfyui", "generation_errors": errors})
                return record
        raise GenerationFailed(f"{job.job_id}: generation failed twice ({'; '.join(errors)})")
