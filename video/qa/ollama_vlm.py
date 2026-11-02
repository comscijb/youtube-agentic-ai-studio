"""Optional layer-2 backend: a vision model served by Ollama (Phase 3.5-C).

``IMAGEGEN_QA_VLM_BACKEND=ollama`` and ``IMAGEGEN_QA_OLLAMA_MODEL`` (default
``qwen2.5vl:3b``). The model is never pulled automatically: ``available()`` is
false until ``ollama pull <model>`` was run. One request per question,
temperature 0, a few tokens; ``keep_alive: 0`` unloads it afterwards so Klein
gets the VRAM back.
"""
from __future__ import annotations

import base64
import io

import requests

import config
from video.qa import base


class OllamaVLM:
    name = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None, session=None, timeout: float = 120.0):
        self.url = (url or getattr(config, "IMAGEGEN_QA_OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or getattr(config, "IMAGEGEN_QA_OLLAMA_MODEL", "qwen2.5vl:3b")
        self.session = session or requests.Session()
        self.timeout = timeout

    def available(self) -> bool:
        try:
            tags = self.session.get(self.url + "/api/tags", timeout=5).json()
        except (requests.RequestException, ValueError):
            return False
        names = {item.get("name") for item in tags.get("models", [])}
        return self.model in names or f"{self.model}:latest" in names

    def _generate(self, question: base.Question) -> str:
        buffer = io.BytesIO()
        question.image.save(buffer, "PNG")
        payload = {"model": self.model, "prompt": question.prompt, "stream": False, "keep_alive": "5m",
                   "images": [base64.b64encode(buffer.getvalue()).decode("ascii")],
                   "options": {"temperature": 0, "num_predict": 8}}
        try:
            response = self.session.post(self.url + "/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
            return str(response.json().get("response", ""))
        except (requests.RequestException, ValueError) as exc:
            raise base.VLMUnavailable(f"Ollama at {self.url}: {exc.__class__.__name__}") from None

    def ask(self, questions: list[base.Question]) -> dict[str, str]:
        answers = {}
        for question in questions:
            parsed = base.parse_choice(self._generate(question), question.choices)
            if parsed is None:
                parsed = base.parse_choice(self._generate(question), question.choices)
            answers[question.qid] = parsed or "unclear"
        return answers

    def unload(self) -> None:
        try:
            self.session.post(self.url + "/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=10)
        except requests.RequestException:
            pass
