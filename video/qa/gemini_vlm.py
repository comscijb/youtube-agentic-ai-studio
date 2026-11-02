"""Optional layer 3: Gemini vision answering the same focused questions (Phase 3.5-C).

Off by default (``IMAGEGEN_QA_GEMINI=false``). Used when the local VLM is
unavailable, or explicitly with ``qa_shots --gemini``. The questions of a
batch go out together (``BATCH`` crops per multimodal call, JSON mode), so a
shot costs one call, not one per question.
"""
from __future__ import annotations

import io
import json

import config
from video.qa import base

BATCH = 16
STAGE = "visual_qa"


class GeminiVLM:
    name = "gemini"

    def __init__(self, generator=None, model: str | None = None):
        self.generator = generator
        self.model = model or getattr(config, "IMAGEGEN_VISUAL_QA_MODEL", "") or None
        self.calls = 0

    def available(self) -> bool:
        return True                                   # failures surface as VLMUnavailable on ask()

    def _call(self, questions: list[base.Question]) -> dict[str, str]:
        from agents.gemini_client import generate
        from agents.narrative import extract_json
        generator = self.generator or generate
        lines = [f'"q{index}": (image {index + 1}) {question.prompt} Allowed: {", ".join(question.choices)}.'
                 for index, question in enumerate(questions)]
        prompt = ("You check frames of an illustrated film. Each numbered question refers to the image with the "
                  "same number, in order. Answer each with exactly one allowed word. Return ONLY JSON "
                  '{"q0": "...", ...}.\n' + "\n".join(lines))
        images = []
        for question in questions:
            buffer = io.BytesIO()
            question.image.convert("RGB").save(buffer, "JPEG", quality=85)
            images.append((buffer.getvalue(), "image/jpeg"))
        try:
            raw = generator(prompt, self.model, stage=STAGE, temperature=0.0,
                            response_mime_type="application/json", images=images)
            answer = extract_json(raw, "Gemini QA")
        except Exception as exc:                      # noqa: BLE001 — quota, overload, parse: layer skipped
            raise base.VLMUnavailable(f"Gemini: {exc.__class__.__name__}: {str(exc)[:160]}") from None
        self.calls += 1
        return {question.qid: base.parse_choice(json.dumps(answer.get(f"q{index}", "")).strip('"'),
                                                question.choices) or "unclear"
                for index, question in enumerate(questions)}

    def ask(self, questions: list[base.Question]) -> dict[str, str]:
        answers = {}
        for start in range(0, len(questions), BATCH):
            answers.update(self._call(questions[start:start + BATCH]))
        return answers
