"""Run the configured QA layers on one image or a batch (Phase 3.5-C).

``IMAGEGEN_QA_LAYERS`` (default ``deterministic,local_vlm``) picks the layers;
``IMAGEGEN_QA_GEMINI=true`` adds Gemini as layer 3, used only when the local
VLM is unavailable (or explicitly, ``qa_shots --gemini``).

A batch runs in three steps so the VLM is loaded once and never together
with Klein: ``prepare`` (layer 1 on the CPU for every image, and the focused
questions), ``answer`` (free ComfyUI's models, ask every question, unload the
VLM), ``finish`` (issues → verdict). Layer 2 is skipped for an image whose
layer-1 verdict is already "regenerate": it will be redrawn anyway.
"""
from __future__ import annotations

import time
from typing import Callable

from PIL import Image

import config
from video.qa import base, decision, focused
from video.qa import deterministic as det

LAYERS = ("deterministic", "local_vlm", "gemini")


def configured_layers(text: str | None = None, gemini: bool | None = None) -> list[str]:
    raw = text if text is not None else getattr(config, "IMAGEGEN_QA_LAYERS", "deterministic,local_vlm")
    layers = [item.strip() for item in str(raw).split(",") if item.strip() in LAYERS]
    if (gemini if gemini is not None else getattr(config, "IMAGEGEN_QA_GEMINI", False)) and "gemini" not in layers:
        layers.append("gemini")
    return layers or ["deterministic"]


def local_backend(client=None):
    name = getattr(config, "IMAGEGEN_QA_VLM_BACKEND", "comfy_textgenerate")
    if name == "ollama":
        from video.qa.ollama_vlm import OllamaVLM
        return OllamaVLM()
    from video.qa.comfy_vlm import ComfyVLM
    return ComfyVLM(client)


class LayeredQA:
    def __init__(self, layers: list[str] | None = None, *, detector=None, vlm=None, gemini=None, comfy=None,
                 progress: Callable[[str], None] = print):
        self.layers = list(layers or configured_layers())
        self.detector = detector or det.Detector()
        self.comfy = comfy
        self._vlm = vlm
        self._gemini = gemini
        self.progress = progress
        self.ocr = None

    # ── backends ────────────────────────────────────────────────────────────
    def vlm(self):
        if self._vlm is None and "local_vlm" in self.layers:
            self._vlm = local_backend(self.comfy)
        return self._vlm

    def gemini(self):
        if self._gemini is None and "gemini" in self.layers:
            from video.qa.gemini_vlm import GeminiVLM
            self._gemini = GeminiVLM()
        return self._gemini

    def backend(self) -> tuple[object | None, list[str]]:
        """The layer-2 answerer: the local VLM, else Gemini (layer 3) when enabled."""
        warnings = []
        local = self.vlm()
        if local is not None:
            try:
                if local.available():
                    return local, warnings
                warnings.append(f"local VLM ({local.name}) unavailable")
            except Exception as exc:                  # noqa: BLE001 — an unreachable backend never blocks
                warnings.append(f"local VLM ({local.name}) unavailable: {exc}")
        remote = self.gemini()
        if remote is not None:
            return remote, warnings
        return None, warnings

    # ── one batch ───────────────────────────────────────────────────────────
    def prepare(self, path, expected: dict, edits_done: int = 0) -> dict:
        started = time.monotonic()
        pending = {"path": str(path), "expected": expected, "edits_done": edits_done, "detection": None,
                   "issues": [], "questions": [], "warnings": [], "layers": [], "answers": {}}
        if "deterministic" not in self.layers:
            pending["warnings"].append("layer 1 disabled: no people/face detection")
        elif not self.detector.available():
            pending["warnings"].append("layer 1 skipped: OpenCV or models/qa missing "
                                       "(python -m scripts.setup_qa_models)")
        else:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
            if self.ocr is None:
                self.ocr = det.ocr_available()
            detection = self.detector.detect(path)
            pending.update(detection=detection, issues=det.layer1(detection, expected, image))
            pending["layers"].append("deterministic")
            layer1 = decision.verdict({"issues": pending["issues"]}, edits_done)[0]
            if layer1 != "regenerate" and ({"local_vlm", "gemini"} & set(self.layers)):
                pending["questions"] = focused.questions(image, detection, expected, ocr=self.ocr)
        pending["seconds"] = round(time.monotonic() - started, 2)
        return pending

    def answer(self, pendings: list[dict]) -> None:
        """Ask every pending question in one go (the VLM is loaded once)."""
        asked = [(index, question) for index, pending in enumerate(pendings) for question in pending["questions"]]
        if not asked:
            return
        backend, warnings = self.backend()
        if backend is None:
            for pending in pendings:
                if pending["questions"]:
                    pending["warnings"] += warnings + ["layer 2 skipped: no VLM available"]
            return
        renamed = []
        for index, question in asked:
            renamed.append(base.Question(f"{index}|{question.qid}", question.kind, question.image, question.prompt,
                                         question.choices, question.meta))
        started = time.monotonic()
        if backend.name != "gemini" and self.comfy is not None:
            try:
                self.comfy.unload_models()           # Klein out before the VLM comes in
            except Exception:                        # noqa: BLE001
                pass
        try:
            answers = backend.ask(renamed)
        except base.VLMUnavailable as exc:
            for pending in pendings:
                if pending["questions"]:
                    pending["warnings"] += warnings + [f"layer 2 skipped: {exc}"]
            return
        finally:
            unload = getattr(backend, "unload", None)
            if unload:
                unload()
        seconds = time.monotonic() - started
        for index, pending in enumerate(pendings):
            prefix = f"{index}|"
            pending["answers"] = {qid[len(prefix):]: value for qid, value in answers.items() if qid.startswith(prefix)}
            if pending["questions"]:
                pending["layers"].append("gemini" if backend.name == "gemini" else "local_vlm")
                pending["warnings"] += warnings
                pending["seconds"] = round(pending["seconds"] + seconds * len(pending["questions"]) / len(asked), 2)

    def finish(self, pending: dict) -> dict:
        issues, people = pending["issues"], None
        if pending["detection"] is not None:
            issues, people = focused.apply(pending["questions"], pending["answers"], pending["detection"],
                                           pending["expected"], pending["issues"])
        status = "skipped" if not pending["layers"] else None
        result = {"version": base.QA_VERSION, "status": status, "people_count": people, "issues": issues,
                  "layers": pending["layers"], "warnings": pending["warnings"],
                  "questions": len(pending["questions"]), "seconds": pending["seconds"]}
        return decision.finalize(result, pending["edits_done"])

    def review_batch(self, items: list[tuple]) -> list[dict]:
        """[(path, expected[, edits_done])] → results, layer 2 asked once for the whole batch."""
        pendings = [self.prepare(*item) for item in items]
        self.answer(pendings)
        return [self.finish(pending) for pending in pendings]

    def review(self, path, expected: dict, edits_done: int = 0) -> dict:
        return self.review_batch([(path, expected, edits_done)])[0]
