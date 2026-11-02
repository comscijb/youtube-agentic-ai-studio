"""Feasibility probe: visual QA with the models already installed in ComfyUI (Phase 3.5-A).

Run (live, opt-in, ComfyUI running):
    python -m scripts.probe_local_vlm
    python -m scripts.probe_local_vlm --models qwen3vl_4b_int8_convrot.safetensors --no-swap

What it does, without installing anything:

1. Reads ``/object_info`` and reports whether ``TextGenerate`` exists, whether it
   takes an ``image``, which ``CLIPLoader`` types and text encoders/LLMs are on
   disk, and which of them could feed it. Node inputs are taken from
   ``/object_info`` and the user's exported edit workflow, never guessed; every
   graph is validated before it is queued.
2. For each usable model, runs the QA prompt (section B schema, JSON only, low
   temperature, thinking off) on the 8 frames listed in
   ``tests/fixtures/qa_probe_expected.json``.
3. Measures the valid-JSON rate, accuracy per check (people count, duplicate,
   glasses, mark, extra people, text), box IoU against the fixture boxes,
   seconds per image and the VRAM peak, and the cost of swapping models:
   unload Klein (``/free``) → load the QA model and run the 8 images → unload →
   reload Klein.

Writes ``data/qa_probe_report.json`` and ``data/qa_probe_contact_sheet.jpg``
(each model's answers drawn over the frames).

``--backend gemini`` (Phase 3.5.1-D, live, run by the user) asks Gemini the same
focused questions on the same 8 fixtures with the same metrics; ComfyUI is not
needed. Its entry (``"backend": "gemini"``) is added next to the local one
already in the report, and the summary prints both side by side.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

import config
from studio import projects
from studio.filenames import safe_filename
from video import imagegen_comfyui as comfy
from video import imagegen_templates as templates
from video.qa import base, focused
from video.qa import deterministic as det
from video.qa.comfy_vlm import ComfyVLM

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "qa_probe_expected.json"
REPORT = ROOT / "data" / "qa_probe_report.json"
SHEET = ROOT / "data" / "qa_probe_contact_sheet.jpg"

PREFERRED = ("qwen3vl_4b_int8_convrot", "qwen3.5_9b_flux3_prompt_upsampler_w4a8")
LLM_NAME = re.compile(r"qwen|[-_]vl|llava|gemma|llama|mistral|phi|pixtral|smol|internvl|minicpm", re.I)
ISSUE_TYPES = ("missing_marker", "duplicate_person", "extra_person", "unstated_injury", "text", "border",
               "style_break", "wrong_subject", "other")
CHECKS = ("people_count", "duplicate_person", "missing_glasses", "unstated_mark", "extra_person", "text")
MAX_LENGTH = 1024
TEMPERATURE = 0.1
MODEL_COLORS = ((255, 70, 70), (70, 200, 255), (255, 200, 40), (120, 255, 120))


# ── /object_info inspection ─────────────────────────────────────────────────

def _spec(object_info: dict, class_type: str) -> dict:
    sections = (object_info.get(class_type) or {}).get("input") or {}
    return {**(sections.get("required") or {}), **(sections.get("optional") or {})}


def _type_name(spec) -> str:
    if isinstance(spec, (list, tuple)) and spec:
        return "COMBO" if isinstance(spec[0], list) else str(spec[0])
    return "?"


def _choices(spec) -> list:
    """Plain combo options, or the keys of a dynamic combo (e.g. sampling_mode on/off)."""
    options = templates._options(spec)
    if options is None and isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get("options")
    keys = []
    for option in options or []:
        keys.append(option.get("key") if isinstance(option, dict) else option)
    return [key for key in keys if key is not None]


def inspect(object_info: dict) -> dict:
    """What ComfyUI can and cannot do for local visual QA, from /object_info alone."""
    generate = _spec(object_info, "TextGenerate")
    loader = _spec(object_info, "CLIPLoader")
    encoders = _choices(loader.get("clip_name"))
    candidates = [name for name in encoders if LLM_NAME.search(name)]
    candidates.sort(key=lambda name: next((i for i, p in enumerate(PREFERRED) if name.startswith(p)), 99))
    findings = {
        "text_generate": bool(generate),
        "text_generate_inputs": {name: _type_name(spec) for name, spec in generate.items()},
        "text_generate_takes_image": _type_name(generate.get("image")) == "IMAGE",
        "sampling_modes": _choices(generate.get("sampling_mode")),
        "clip_loader_types": _choices(loader.get("type")),
        "clip_loader_has_device": "device" in loader,
        "text_encoders": encoders,
        "candidates": candidates,
        "preview_any": "PreviewAny" in object_info,
        "load_image": "LoadImage" in object_info,
        "problems": [],
    }
    if not findings["text_generate"]:
        findings["problems"].append("TextGenerate is not installed: no local VLM QA through ComfyUI (use ollama)")
    elif not findings["text_generate_takes_image"]:
        findings["problems"].append("TextGenerate has no IMAGE input: it cannot look at a frame")
    if not findings["preview_any"]:
        findings["problems"].append("PreviewAny is missing: the generated text cannot be read back")
    if not candidates:
        findings["problems"].append("no LLM/VLM text encoder found among the CLIPLoader models")
    return findings


def clip_types(findings: dict) -> list[str]:
    """CLIPLoader types worth trying, the one of the user's export first."""
    types = findings["clip_loader_types"]
    ordered = [kind for kind in ("flux3",) if kind in types]
    ordered += [kind for kind in types if "qwen" in kind.lower() and kind not in ordered]
    return ordered or types[:1]


# ── QA prompt (section B schema) and graph ─────────────────────────────────

def qa_prompt(item: dict, characters: dict) -> str:
    expected = item["expected"]
    listed = [{"id": cid, "who": characters[cid]["who"], "markers": characters[cid]["markers"]}
              for cid in expected["characters"]]
    others = ("Unnamed distant guests may be present." if expected.get("others_present")
              else "Nobody else should be present.")
    return f"""You check one frame of an illustrated film. Answer ONLY one JSON object, no other text.
Expected: exactly {len(listed)} person/people. {others}
Characters (each must show its identity markers when its face is visible):
{json.dumps(listed, ensure_ascii=False)}
The scene narration mentions no injuries, blood, tears or marks.

Return:
{{"people_count": <int, every person visible, background figures included>,
 "issues": [{{"type": "missing_marker|duplicate_person|extra_person|unstated_injury|text|border|style_break|wrong_subject|other",
             "severity": "blocking|advisory",
             "box": [x0, y0, x1, y1],
             "target": "<character id or empty>",
             "note": "<short description>"}}],
 "verdict": "pass|edit|regenerate"}}
Boxes are normalized 0-1 (left, top, right, bottom) around the problem. Report: a listed character
drawn twice (duplicate_person, one box per copy); a visible face missing one of its markers
(missing_marker, box on the face); people beyond the expected ones (extra_person, one box each);
blood, wounds or red marks (unstated_injury); letters or watermarks (text). No issues: "issues": []."""


def sampling_inputs(findings: dict, template: dict) -> dict:
    """Greedy decoding when the node offers it, else the export's sampler at a low temperature."""
    modes = findings["sampling_modes"]
    if "off" in modes:
        return {"sampling_mode": "off"}
    inputs = {key: value for key, value in template.items() if key.startswith("sampling_mode")}
    inputs["sampling_mode"] = "on"
    if "sampling_mode.temperature" in inputs:
        inputs["sampling_mode.temperature"] = TEMPERATURE
    if "sampling_mode.top_k" in inputs:
        inputs["sampling_mode.top_k"] = 1
    return inputs


def build_graph(findings: dict, model: str, clip_type: str, image_name: str, prompt: str,
                edit_path=templates.EDIT_TEMPLATE) -> dict:
    """CLIPLoader → TextGenerate(image) → PreviewAny, inputs restricted to what /object_info lists."""
    template = templates.load_edit_template(edit_path).get("47", {}).get("inputs", {})
    accepted = set(findings["text_generate_inputs"])
    inputs = {"prompt": prompt, "clip": ["1", 0], "image": ["2", 0], "max_length": MAX_LENGTH,
              "thinking": False, "use_default_template": True, "mtp": "off", **sampling_inputs(findings, template)}
    inputs = {key: value for key, value in inputs.items() if key.split(".")[0] in accepted}
    loader = {"clip_name": model, "type": clip_type}
    if findings.get("clip_loader_has_device"):
        loader["device"] = "default"
    return {
        "1": {"class_type": "CLIPLoader", "inputs": loader},
        "2": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "3": {"class_type": "TextGenerate", "inputs": inputs},
        "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}},
    }


def text_from_outputs(outputs: dict, node_id: str = "4") -> str:
    """The string PreviewAny reports in /history (``{"text": [...]}`` or similar)."""
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
    walk(outputs.get(node_id, outputs))
    return max(found, key=len) if found else ""


# ── Answers and scoring ─────────────────────────────────────────────────────

def _box(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if max(box) > 1.5:                            # Qwen-VL answers on a 0-1000 grid
        box = [item / 1000.0 for item in box]
    box = [min(max(item, 0.0), 1.0) for item in box]
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return [round(item, 4) for item in box]


def parse_answer(text: str) -> tuple[dict | None, str]:
    """(normalized answer, "") or (None, reason). Valid = one JSON object with the schema's keys."""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", str(text or "")).strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.I).strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None, "no JSON object"
    try:
        value = json.loads(match.group())
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON ({exc.msg})"
    if not isinstance(value, dict) or not isinstance(value.get("issues"), list) or "people_count" not in value:
        return None, "JSON without people_count/issues"
    issues = []
    for issue in value["issues"]:
        if not isinstance(issue, dict):
            continue
        kind = str(issue.get("type", "other")).strip().lower()
        issues.append({"type": kind if kind in ISSUE_TYPES else "other",
                       "severity": "advisory" if str(issue.get("severity")).lower() == "advisory" else "blocking",
                       "box": _box(issue.get("box")), "target": str(issue.get("target") or ""),
                       "note": str(issue.get("note") or "")[:200]})
    try:
        count = int(value.get("people_count"))
    except (TypeError, ValueError):
        count = None
    return {"people_count": count, "issues": issues, "verdict": str(value.get("verdict", ""))}, ""


def answered(answer: dict) -> dict:
    """The six probe checks as the model answered them (blocking issues only)."""
    issues = [issue for issue in answer["issues"] if issue.get("severity") != "advisory"]
    kinds = {issue["type"] for issue in issues}
    notes = " ".join(issue["note"].lower() for issue in issues)
    glasses = any(issue["type"] == "missing_marker" and ("glass" in issue["note"].lower()
                                                         or issue["target"] == "julian_vance")
                  for issue in issues)
    return {"people_count": answer["people_count"], "duplicate_person": "duplicate_person" in kinds,
            "missing_glasses": glasses,
            "unstated_mark": "unstated_injury" in kinds or ("mark" in notes and "missing_marker" not in kinds),
            "extra_person": "extra_person" in kinds, "text": "text" in kinds}


def iou(first, second) -> float:
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = ((first[2] - first[0]) * (first[3] - first[1]) + (second[2] - second[0]) * (second[3] - second[1])
             - inter)
    return inter / union if union > 0 else 0.0


def box_scores(answer: dict, truth: dict) -> list[float]:
    """Best IoU of the model's boxes for each fixture box, by issue type."""
    scores = []
    for kind, boxes in (truth.get("boxes") or {}).items():
        mine = [issue["box"] for issue in answer["issues"] if issue["type"] == kind and issue["box"]]
        for box in boxes:
            scores.append(max((iou(box, other) for other in mine), default=0.0))
    return scores


def score(results: list[dict], fixture: dict) -> dict:
    truths = {item["shot_id"]: item["truth"] for item in fixture["images"]}
    valid = [result for result in results if result.get("answer")]
    accuracy = {}
    for check in CHECKS:
        hits = [answered(result["answer"])[check] == truths[result["shot_id"]][check] for result in valid]
        accuracy[check] = round(sum(hits) / len(results), 3) if results else None
    ious = [value for result in valid for value in box_scores(result["answer"], truths[result["shot_id"]])]
    seconds = [result["seconds"] for result in results if result.get("seconds") is not None]
    peaks = [result["vram_peak_mb"] for result in results if result.get("vram_peak_mb") is not None]
    return {"images": len(results), "valid_json_rate": round(len(valid) / len(results), 3) if results else None,
            "accuracy": accuracy,
            "box_iou_mean": round(sum(ious) / len(ious), 3) if ious else None,
            "box_hits_iou_0_3": sum(value >= 0.3 for value in ious), "boxes_expected": len(ious),
            "seconds_per_image": round(sum(seconds) / len(seconds), 2) if seconds else None,
            "seconds_first_image": seconds[0] if seconds else None,
            "vram_peak_mb": max(peaks) if peaks else None}


# ── Live runs ───────────────────────────────────────────────────────────────

class VramSampler:
    """Used as the ComfyClient ``sleep``: samples VRAM while a prompt runs."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep):
        self.sleep = sleep
        self.client = None
        self.peak = None

    def reset(self):
        self.peak = None
        self.sample()

    def sample(self):
        used = self.client.vram_used_mb() if self.client else None
        if used is not None:
            self.peak = used if self.peak is None else max(self.peak, used)

    def __call__(self, seconds: float):
        self.sample()
        self.sleep(seconds)


def run_graph(client, sampler: VramSampler, graph: dict, timeout: float = 600.0) -> tuple[dict, float, float | None]:
    sampler.reset()
    started = time.monotonic()
    outputs = client.wait(client.queue(graph), timeout=timeout, poll=0.5)
    sampler.sample()
    return outputs, round(time.monotonic() - started, 2), sampler.peak


def unload(client) -> float:
    started = time.monotonic()
    client._request("POST", "/free", json={"unload_models": True, "free_memory": True})
    return round(time.monotonic() - started, 2)


def load_klein(client, sampler: VramSampler) -> float:
    """A small Klein render, so the image model is resident again (as before QA)."""
    graph = templates.build_graph("t2i", prompt="an empty quiet room, engraving illustration", seed=1,
                                  width=512, height=288, steps=4, filename_prefix="pg_probe_klein")
    return run_graph(client, sampler, graph)[1]


def upload_downscaled(client, path: Path, max_side: int, scratch: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side), Image.LANCZOS)
        target = scratch / f"{path.stem}_{max_side}.png"
        image.save(target, "PNG")
    return client.upload_image(target)


def probe_model(client, sampler, findings, model, fixture, uploads, progress, *, swap: bool) -> dict:
    record = {"model": model, "clip_type": None, "attempts": [], "results": [], "swap": None}
    first = fixture["images"][0]
    timings = {}
    if swap:
        try:
            timings["klein_warmup_s"] = load_klein(client, sampler)
        except (comfy.GenerationFailed, comfy.ComfyUnavailable, templates.TemplateError) as exc:
            record["attempts"].append({"step": "klein warm-up", "error": str(exc)[:300]})
        timings["unload_klein_s"] = unload(client)
    for clip_type in clip_types(findings):
        graph = build_graph(findings, model, clip_type, uploads[first["shot_id"]],
                            qa_prompt(first, fixture["characters"]))
        problems = templates.validate_graph(graph, client.object_info())
        if problems:
            record["attempts"].append({"clip_type": clip_type, "error": "; ".join(problems)[:500]})
            continue
        try:
            outputs, seconds, peak = run_graph(client, sampler, graph)
        except (comfy.GenerationFailed, comfy.ComfyUnavailable) as exc:
            record["attempts"].append({"clip_type": clip_type, "error": str(exc)[:500]})
            continue
        record["clip_type"] = clip_type
        record["results"].append(_result(first["shot_id"], text_from_outputs(outputs), seconds, peak))
        break
    if record["clip_type"]:
        for item in fixture["images"][1:]:
            graph = build_graph(findings, model, record["clip_type"], uploads[item["shot_id"]],
                                qa_prompt(item, fixture["characters"]))
            try:
                outputs, seconds, peak = run_graph(client, sampler, graph)
                result = _result(item["shot_id"], text_from_outputs(outputs), seconds, peak)
            except (comfy.GenerationFailed, comfy.ComfyUnavailable) as exc:
                result = {"shot_id": item["shot_id"], "raw": "", "answer": None, "error": str(exc)[:300],
                          "seconds": None, "vram_peak_mb": None}
            record["results"].append(result)
            progress(f"   {item['shot_id']}: {'ok' if result['answer'] else result['error']} "
                     f"({result['seconds']}s)")
        record["metrics"] = score(record["results"], fixture)
    if swap:
        timings["qa_load_and_run_s"] = round(sum(r["seconds"] or 0 for r in record["results"]), 2)
        timings["unload_qa_s"] = unload(client)
        try:
            timings["reload_klein_s"] = load_klein(client, sampler)
        except (comfy.GenerationFailed, comfy.ComfyUnavailable, templates.TemplateError) as exc:
            record["attempts"].append({"step": "klein reload", "error": str(exc)[:300]})
        timings["total_swap_s"] = round(sum(value for key, value in timings.items() if key != "klein_warmup_s"), 2)
        record["swap"] = timings
    return record


def fixture_expected(item: dict, characters: dict) -> dict:
    """The fixture frame's expectation in the shape visual_qa.expected_for returns."""
    listed = item["expected"]["characters"]
    return {"people": len(listed), "background": 0, "others_present": bool(item["expected"].get("others_present")),
            "characters": [{"id": cid, **characters[cid]} for cid in listed], "narration": ""}


def probe_focused(client, sampler, model, fixture, images, progress, *, swap: bool,
                  detector=None, vlm=None) -> dict:
    """Decision 6: layer 1 (detector) + focused one-word questions to the VLM, scored like the JSON mode."""
    detector = detector or det.Detector()
    vlm = vlm or ComfyVLM(client, model=model)
    record = {"model": model, "mode": "focused", "clip_type": getattr(vlm, "clip_type", ""), "attempts": [],
              "results": [], "swap": None}
    timings = {}
    if swap:
        try:
            timings["klein_warmup_s"] = load_klein(client, sampler)
        except (comfy.GenerationFailed, comfy.ComfyUnavailable, templates.TemplateError) as exc:
            record["attempts"].append({"step": "klein warm-up", "error": str(exc)[:300]})
        timings["unload_klein_s"] = unload(client)
    ocr = det.ocr_available()
    for item in fixture["images"]:
        expected = fixture_expected(item, fixture["characters"])
        started = time.monotonic()
        detection = detector.detect(images[item["shot_id"]])
        with Image.open(images[item["shot_id"]]) as opened:
            image = opened.convert("RGB")
        issues = det.layer1(detection, expected, image)
        asked = focused.questions(image, detection, expected, ocr=ocr)
        layer1_seconds = round(time.monotonic() - started, 2)
        sampler.reset()
        started = time.monotonic()
        try:
            answers = vlm.ask(asked)
            error = ""
        except base.VLMUnavailable as exc:
            answers, error = {}, str(exc)[:300]
            record["attempts"].append({"shot_id": item["shot_id"], "error": error})
        sampler.sample()
        issues, count = focused.apply(asked, answers, detection, expected, issues)
        record["results"].append({
            "shot_id": item["shot_id"], "raw": json.dumps(answers), "error": error,
            "model_texts": dict(getattr(vlm, "texts", {}) or {}),
            "answer": None if error else {"people_count": count, "issues": issues, "verdict": ""},
            "seconds": round(time.monotonic() - started, 2), "layer1_seconds": layer1_seconds,
            "questions": len(asked), "detected_people": len(detection["persons"]), "vram_peak_mb": sampler.peak})
        progress(f"   {item['shot_id']}: {len(asked)} questions, "
                 f"{', '.join(i['type'] for i in base.blocking({'issues': issues})) or 'pass'}"
                 + (f" — {error}" if error else ""))
    record["metrics"] = score(record["results"], fixture)
    truths = {item["shot_id"]: item["truth"]["people_count"] for item in fixture["images"]}
    detected = sum(min(result["detected_people"], truths[result["shot_id"]]) for result in record["results"])
    record["metrics"].update({
        "detector_person_recall": round(detected / sum(truths.values()), 3),
        "layer1_seconds_per_image": round(sum(r["layer1_seconds"] for r in record["results"]) / len(truths), 2),
        "questions_per_image": round(sum(r["questions"] for r in record["results"]) / len(truths), 1),
        "ocr": ocr})
    if swap:
        timings["qa_load_and_run_s"] = round(sum(r["seconds"] or 0 for r in record["results"]), 2)
        timings["unload_qa_s"] = unload(client)
        try:
            timings["reload_klein_s"] = load_klein(client, sampler)
        except (comfy.GenerationFailed, comfy.ComfyUnavailable, templates.TemplateError) as exc:
            record["attempts"].append({"step": "klein reload", "error": str(exc)[:300]})
        timings["total_swap_s"] = round(sum(value for key, value in timings.items() if key != "klein_warmup_s"), 2)
        record["swap"] = timings
    return record


def _result(shot_id: str, raw: str, seconds: float, peak) -> dict:
    answer, error = parse_answer(raw)
    return {"shot_id": shot_id, "raw": raw[:2000], "answer": answer, "error": error,
            "seconds": seconds, "vram_peak_mb": peak}


# ── Contact sheet ───────────────────────────────────────────────────────────

def contact_sheet(fixture: dict, images: dict, models: list[dict], path: Path, width: int = 640) -> Path:
    """The 8 frames, each model's boxes drawn in its color, verdict and issues written underneath."""
    height = width * 9 // 16
    line, columns = 14, 2
    text_rows = 2 + 3 * max(1, len(models))
    cell = (width, height + text_rows * line)
    rows = (len(fixture["images"]) + columns - 1) // columns
    sheet = Image.new("RGB", (cell[0] * columns, cell[1] * rows), (16, 16, 16))
    for index, item in enumerate(fixture["images"]):
        frame = Image.open(images[item["shot_id"]]).convert("RGB").resize((width, height))
        draw = ImageDraw.Draw(frame)
        for boxes in (item["truth"].get("boxes") or {}).values():
            for box in boxes:
                draw.rectangle([box[0] * width, box[1] * height, box[2] * width, box[3] * height],
                               outline=(255, 255, 255), width=1)
        for number, model in enumerate(models):
            color = MODEL_COLORS[number % len(MODEL_COLORS)]
            result = next((r for r in model["results"] if r["shot_id"] == item["shot_id"]), None)
            answer = (result or {}).get("answer") or {}
            for issue in answer.get("issues", []):
                if issue["box"]:
                    box = issue["box"]
                    draw.rectangle([box[0] * width, box[1] * height, box[2] * width, box[3] * height],
                                   outline=color, width=3)
        x, y = (index % columns) * cell[0], (index // columns) * cell[1]
        sheet.paste(frame, (x, y))
        draw = ImageDraw.Draw(sheet)
        draw.text((x + 4, y + height + 2), f"{item['shot_id']}  [{item['case']}]  truth: {item['truth']['note'][:70]}",
                  fill=(255, 255, 255))
        for number, model in enumerate(models):
            color = MODEL_COLORS[number % len(MODEL_COLORS)]
            result = next((r for r in model["results"] if r["shot_id"] == item["shot_id"]), None)
            top = y + height + 2 + line * (1 + 3 * number)
            name = model["model"][:40]
            if not result:
                draw.text((x + 4, top), f"{name}: not run", fill=color)
                continue
            if not result["answer"]:
                draw.text((x + 4, top), f"{name}: invalid ({result['error']}) {result['raw'][:60]!r}", fill=color)
                continue
            answer = result["answer"]
            issues = ", ".join(f"{i['type']}{'*' if i['box'] else ''}" for i in answer["issues"]) or "none"
            draw.text((x + 4, top), f"{name}: people={answer['people_count']} verdict={answer['verdict']} "
                                    f"({result['seconds']}s)", fill=color)
            draw.text((x + 4, top + line), f"  issues: {issues}"[:100], fill=color)
            note = "; ".join(i["note"] for i in answer["issues"])[:100]
            draw.text((x + 4, top + 2 * line), f"  {note}", fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "JPEG", quality=88)
    return path


# ── Entry point ─────────────────────────────────────────────────────────────

def run_gemini(output: Path, *, vlm=None, detector=None, fixture_path: Path = FIXTURE, report_path: Path = REPORT,
               sheet_path: Path = SHEET, progress: Callable[[str], None] = print) -> dict:
    """The focused probe answered by Gemini; merged into the existing report beside the local entries."""
    from video.qa.gemini_vlm import GeminiVLM
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    images = {item["shot_id"]: Path(output) / "images" / "shots" / f"{safe_filename(item['shot_id'])}.jpg"
              for item in fixture["images"]}
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {"version": 1, "findings": {"text_generate": False, "text_generate_takes_image": False,
                                             "clip_loader_types": [], "candidates": [], "problems": []}}
    missing = [shot for shot, path in images.items() if not path.is_file()]
    report.update(created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), project=Path(output).name,
                  fixture_confirmed_by_user=fixture.get("confirmed_by_user", False), missing_images=missing)
    report["models"] = [entry for entry in report.get("models", []) if entry.get("backend") != "gemini"]
    if missing:
        report.setdefault("findings", {}).setdefault("problems", []).append(
            f"probe frames missing in {output}: {', '.join(missing)}")
    else:
        vlm = vlm or GeminiVLM()
        name = getattr(vlm, "model", None) or getattr(config, "IMAGEGEN_VISUAL_QA_MODEL", "") or "gemini"
        progress(f"→ Gemini ({name}, focused)")
        record = probe_focused(None, VramSampler(), name, fixture, images, progress, swap=False,
                               detector=detector, vlm=vlm)
        record["backend"] = "gemini"
        record["calls"] = getattr(vlm, "calls", None)
        report["models"].append(record)
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not missing:
        contact_sheet(fixture, images, report["models"], Path(sheet_path))
        report["contact_sheet"] = str(sheet_path)
    return report


def run(output: Path, *, client=None, models: list[str] | None = None, swap: bool = True,
        max_side: int | None = None, fixture_path: Path = FIXTURE, report_path: Path = REPORT,
        sheet_path: Path = SHEET, progress: Callable[[str], None] = print, mode: str = "json",
        detector=None) -> dict:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    sampler = VramSampler()
    client = client or comfy.ComfyClient(sleep=sampler)
    sampler.client = client
    if not client.available():
        raise comfy.ComfyUnavailable(f"ComfyUI is not reachable at {client.base_url}. Start it and run again.")
    findings = inspect(client.object_info())
    images = {item["shot_id"]: Path(output) / "images" / "shots" / f"{safe_filename(item['shot_id'])}.jpg"
              for item in fixture["images"]}
    missing = [shot for shot, path in images.items() if not path.is_file()]
    report = {"version": 1, "mode": mode, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "project": Path(output).name,
              "fixture_confirmed_by_user": fixture.get("confirmed_by_user", False),
              "comfyui": client.base_url, "findings": findings, "models": [], "missing_images": missing}
    usable = findings["text_generate"] and findings["text_generate_takes_image"] and findings["preview_any"]
    chosen = list(models or findings["candidates"])
    if mode == "focused":
        chosen = list(models or [getattr(config, "IMAGEGEN_QA_VLM_MODEL", PREFERRED[0] + ".safetensors")])
        if detector is None and det.missing_models():
            findings["problems"].append("layer-1 models missing in models/qa: " + ", ".join(det.missing_models()))
            usable = False
    if usable and not missing and chosen and mode == "focused":
        for model in chosen:
            progress(f"→ {model} (focused)")
            report["models"].append(probe_focused(client, sampler, model, fixture, images, progress, swap=swap,
                                                  detector=detector))
    elif usable and not missing and chosen:
        side = int(max_side or getattr(config, "IMAGEGEN_QA_MAX_SIDE", 768))
        with tempfile.TemporaryDirectory() as scratch:
            uploads = {shot: upload_downscaled(client, path, side, Path(scratch)) for shot, path in images.items()}
        for model in chosen:
            progress(f"→ {model}")
            report["models"].append(probe_model(client, sampler, findings, model, fixture, uploads, progress,
                                                swap=swap))
    elif missing:
        findings["problems"].append(f"probe frames missing in {output}: {', '.join(missing)}")
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not missing:
        contact_sheet(fixture, images, report["models"], Path(sheet_path))
        report["contact_sheet"] = str(sheet_path)
    return report


def summary_lines(report: dict) -> list[str]:
    findings = report["findings"]
    lines = [f"TextGenerate: {'yes' if findings['text_generate'] else 'NO'}"
             f" (image input: {'yes' if findings['text_generate_takes_image'] else 'no'});"
             f" CLIPLoader types: {', '.join(findings['clip_loader_types']) or '-'}",
             f"Candidate models: {', '.join(findings['candidates']) or 'none'}"]
    lines += [f"⚠ {problem}" for problem in findings["problems"]]
    for model in report["models"]:
        metrics = model.get("metrics")
        if not metrics:
            errors = "; ".join(attempt.get("error", "") for attempt in model["attempts"])[:300]
            lines.append(f"{model['model']}: not usable — {errors}")
            continue
        accuracy = ", ".join(f"{key} {value:.0%}" for key, value in metrics["accuracy"].items())
        label = "answered" if model.get("mode") == "focused" else "valid JSON"
        kind = "Gemini" if model.get("backend") == "gemini" else f"type {model['clip_type']}"
        lines.append(f"{model['model']} ({kind}): {label} {metrics['valid_json_rate']:.0%}; "
                     f"{accuracy}; box IoU {metrics['box_iou_mean']}; {metrics['seconds_per_image']} s/image; "
                     f"VRAM peak {metrics['vram_peak_mb']} MB")
        if model.get("swap"):
            lines.append(f"   swap: {json.dumps(model['swap'])}")
        if model.get("mode") == "focused":
            lines.append(f"   detector person recall {metrics['detector_person_recall']:.0%}; layer 1 "
                         f"{metrics['layer1_seconds_per_image']} s/image; {metrics['questions_per_image']} "
                         f"questions/image; OCR {'on' if metrics['ocr'] else 'off (text asked to the VLM)'}")
    lines += comparison_lines(report)
    return lines


def comparison_lines(report: dict) -> list[str]:
    """Local (focused) vs Gemini, metric by metric, when the report has both."""
    local = next((m for m in report["models"] if m.get("backend") != "gemini" and m.get("mode") == "focused"
                  and m.get("metrics")), None)
    gemini = next((m for m in report["models"] if m.get("backend") == "gemini" and m.get("metrics")), None)
    if not (local and gemini):
        return []
    keys = list(local["metrics"]["accuracy"])
    lines = ["", f"{'accuracy':<18}{'local':>8}{'gemini':>8}"]
    for key in keys:
        lines.append(f"{key:<18}{local['metrics']['accuracy'].get(key, 0):>8.0%}"
                     f"{gemini['metrics']['accuracy'].get(key, 0):>8.0%}")
    lines.append(f"{'s/image':<18}{local['metrics']['seconds_per_image']:>8}{gemini['metrics']['seconds_per_image']:>8}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Probe local visual QA with the models installed in ComfyUI.")
    projects.add_argument(parser)
    parser.add_argument("--output-dir", default=None, help="explicit folder (overrides the project)")
    parser.add_argument("--models", nargs="+", help="CLIPLoader model files to try (default: every candidate)")
    parser.add_argument("--no-swap", action="store_true", help="skip the Klein unload/reload timing")
    parser.add_argument("--mode", choices=("json", "focused"), default="json",
                        help="json: one big JSON answer per frame (section A); focused: detector + one-word "
                             "questions about crops (decision 6)")
    parser.add_argument("--max-side", type=int, default=None, help="downscale frames to this side (default 768)")
    parser.add_argument("--backend", choices=("local", "gemini"), default="local",
                        help="gemini: the focused probe answered by Gemini (live), added beside the local result")
    args = parser.parse_args(argv)
    output = projects.cli_output(args)
    if args.backend == "gemini":
        report = run_gemini(output)
        for line in summary_lines(report):
            print(line)
        print(f"Report: {REPORT}")
        return 0
    try:
        report = run(output, models=args.models, swap=not args.no_swap, max_side=args.max_side,
                     mode=args.mode)
    except comfy.ComfyUnavailable as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    for line in summary_lines(report):
        print(line)
    print(f"Report: {REPORT}")
    if report.get("contact_sheet"):
        print(f"Contact sheet: {report['contact_sheet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
