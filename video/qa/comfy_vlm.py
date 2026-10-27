"""Layer-2 backend: ComfyUI's ``TextGenerate`` node with a vision text encoder (Phase 3.5).

The probe (data/qa_probe_report.json) picked ``qwen3vl_4b_int8_convrot`` with the
CLIPLoader type ``flux3``: it does see the image (the prompt upsampler and
``qwen_3_4b`` do not). Each question becomes LoadImage → TextGenerate →
PreviewAny; the questions of one image (up to ``BATCH``) go in a single graph
so the model loads once. Decoding is greedy, thinking off, ``max_length``
small; an unparseable answer is asked once more, then counts as ``unclear``.
The model's built-in system prompt made it answer in HTML (``<div
class="answer" data-ans="no">``) or open a comment and run out of tokens, so
a plain one-word system prompt replaces it (a ``PrimitiveString`` node: the
input only takes a link).

The node inputs come from ``/object_info`` and the user's exported edit
workflow (node 47), never guessed; the graph is validated before it is queued.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import config
from video import imagegen_comfyui as comfy
from video import imagegen_templates as templates
from video.qa import base

BATCH = 12
MAX_LENGTH = 32
SYSTEM_PROMPT = ("You are an image inspector. Reply in plain text with exactly one word from the choices "
                 "the user gives. No markup, no punctuation, no explanation.")
TEMPERATURE = 0.1


def _spec(object_info: dict, class_type: str) -> dict:
    sections = (object_info.get(class_type) or {}).get("input") or {}
    return {**(sections.get("required") or {}), **(sections.get("optional") or {})}


def _choices(spec) -> list:
    options = templates._options(spec)
    if options is None and isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get("options")
    return [option.get("key") if isinstance(option, dict) else option for option in options or []]


def sampling_inputs(modes: list, template: dict) -> dict:
    """Greedy decoding when the node offers it, else the export's sampler at a low temperature."""
    if "off" in modes:
        return {"sampling_mode": "off"}
    inputs = {key: value for key, value in template.items() if key.startswith("sampling_mode")}
    inputs["sampling_mode"] = "on"
    if "sampling_mode.temperature" in inputs:
        inputs["sampling_mode.temperature"] = TEMPERATURE
    if "sampling_mode.top_k" in inputs:
        inputs["sampling_mode.top_k"] = 1
    return inputs


def text_generate_inputs(object_info: dict, prompt: str, clip, image, max_length: int,
                         edit_path=templates.EDIT_TEMPLATE, system_prompt=None) -> dict:
    spec = _spec(object_info, "TextGenerate")
    template = templates.load_edit_template(edit_path).get("47", {}).get("inputs", {})
    inputs = {"prompt": prompt, "clip": clip, "image": image, "max_length": max_length, "thinking": False,
              "use_default_template": True, "mtp": "off",
              **sampling_inputs(_choices(spec.get("sampling_mode")), template)}
    if system_prompt is not None:
        inputs["system_prompt"] = system_prompt
    return {key: value for key, value in inputs.items() if key.split(".")[0] in spec}


class ComfyVLM:
    name = "comfy_textgenerate"

    def __init__(self, client: comfy.ComfyClient | None = None, model: str | None = None,
                 clip_type: str | None = None):
        self.client = client or comfy.ComfyClient()
        self.model = model or getattr(config, "IMAGEGEN_QA_VLM_MODEL", "qwen3vl_4b_int8_convrot.safetensors")
        self.clip_type = clip_type or getattr(config, "IMAGEGEN_QA_VLM_CLIP_TYPE", "flux3")
        self.calls = 0
        self.seconds = 0.0
        self.texts: dict[str, str] = {}       # the model's raw replies of the last ask(), for the probe report

    def problems(self) -> list[str]:
        if not self.client.available():
            return [f"ComfyUI is not reachable at {self.client.base_url}"]
        info = self.client.object_info()
        found = []
        if "image" not in _spec(info, "TextGenerate"):
            found.append("TextGenerate with an image input is not installed")
        if "PreviewAny" not in info:
            found.append("PreviewAny is not installed")
        if self.model not in _choices(_spec(info, "CLIPLoader").get("clip_name")):
            found.append(f"{self.model} is not among the CLIPLoader models")
        return found

    def available(self) -> bool:
        return not self.problems()

    def graph(self, items: list[tuple[str, str]]) -> dict:
        """[(uploaded image name, prompt)] → one graph; question k uses nodes 10+3k..12+3k."""
        info = self.client.object_info()
        loader = {"clip_name": self.model, "type": self.clip_type}
        if "device" in _spec(info, "CLIPLoader"):
            loader["device"] = "default"
        graph = {"1": {"class_type": "CLIPLoader", "inputs": loader}}
        system = None
        if "system_prompt" in _spec(info, "TextGenerate") and "PrimitiveString" in info:
            graph["2"] = {"class_type": "PrimitiveString", "inputs": {"value": SYSTEM_PROMPT}}
            system = ["2", 0]
        for index, (image, prompt) in enumerate(items):
            load, generate, preview = (str(10 + 3 * index + offset) for offset in range(3))
            graph[load] = {"class_type": "LoadImage", "inputs": {"image": image}}
            graph[generate] = {"class_type": "TextGenerate", "inputs": text_generate_inputs(
                info, prompt, ["1", 0], [load, 0], MAX_LENGTH, system_prompt=system)}
            graph[preview] = {"class_type": "PreviewAny", "inputs": {"source": [generate, 0]}}
        return graph

    def _run(self, questions: list[base.Question], folder: Path) -> dict[str, str]:
        names = []
        for question in questions:
            path = folder / f"{question.qid.replace(':', '_').replace(' ', '_')[:60]}.png"
            question.image.save(path, "PNG")
            names.append(self.client.upload_image(path))
        graph = self.graph([(name, question.prompt) for name, question in zip(names, questions)])
        problems = templates.validate_graph(graph, self.client.object_info())
        if problems:
            raise base.VLMUnavailable("the QA graph does not match this ComfyUI: " + "; ".join(problems[:4]))
        started = time.monotonic()
        outputs = self.client.wait(self.client.queue(graph), timeout=600.0, poll=0.5)
        self.seconds += time.monotonic() - started
        self.calls += len(questions)
        return {question.qid: _text(outputs.get(str(12 + 3 * index), {}))
                for index, question in enumerate(questions)}

    def ask(self, questions: list[base.Question]) -> dict[str, str]:
        answers = {}
        self.texts = {}
        try:
            with tempfile.TemporaryDirectory() as folder:
                for start in range(0, len(questions), BATCH):
                    chunk = questions[start:start + BATCH]
                    raw = self._run(chunk, Path(folder))
                    retry = []
                    for question in chunk:
                        self.texts[question.qid] = raw.get(question.qid, "")
                        parsed = base.parse_choice(raw.get(question.qid, ""), question.choices)
                        if parsed is None:
                            retry.append(question)
                        else:
                            answers[question.qid] = parsed
                    if retry:
                        again = self._run(retry, Path(folder))
                        for question in retry:
                            self.texts[question.qid] += " || retry: " + again.get(question.qid, "")
                            answers[question.qid] = base.parse_choice(again.get(question.qid, ""),
                                                                      question.choices) or "unclear"
        except (comfy.ComfyUnavailable, comfy.GenerationFailed) as exc:
            raise base.VLMUnavailable(str(exc)) from None
        return answers

    def unload(self) -> None:
        try:
            self.client.unload_models()
        except (comfy.ComfyUnavailable, comfy.GenerationFailed):
            pass


def _text(output) -> str:
    """The longest string in a PreviewAny history entry (``{"text": [...]}``)."""
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
    walk(output)
    return max(found, key=len) if found else ""
