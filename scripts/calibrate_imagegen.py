"""Measure FLUX 3 Klein on this machine and compare t2i vs reference generation.

Run:  python -m scripts.calibrate_imagegen

Generates the same 3 prompts with (1) text-to-image, (2) reference + latent
from the reference, (3) reference + empty 16:9 latent. Records seconds per
image and the VRAM peak (/system_stats) in data/imagegen_calibration.json and
writes output/imagegen_calibration/contact_sheet.jpg so you can choose the
default latent source (IMAGEGEN_LATENT_SOURCE) and whether identity retention
is good enough for the "reference" policy (else IMAGEGEN_IDENTITY_POLICY=limited_faces).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from agents import identity
from agents import visual_bible as vbible
from agents.narrative import load_json
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyUnavailable, ImageGenerator, ImageJob

MODES = (("t2i", "t2i", ""), ("ref_reference", "ref", "reference"), ("ref_empty_16x9", "ref", "empty_16x9"))
PROMPTS = (
    "the man from the first image sits alone at a long dark dining table, candlelight, looking down",
    "the man from the first image stands by a tall window at night, city lights behind him, half in shadow",
    "close-up of the man from the first image adjusting his cuff link",
)
STYLE = identity.style_suffix()
REFERENCE_PROMPT = (f"An {identity.image_noun()} portrait, chest-up, front view, of a 37-year-old man with a lean face, "
                    "short dark hair and a charcoal suit. Neutral-to-dark backdrop. " + STYLE)


class VramSampler:
    def __init__(self, client, interval: float = 1.0):
        self.client, self.interval, self.peak = client, interval, 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            used = self.client.vram_used_mb()
            if used:
                self.peak = max(self.peak, used)
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def reference_image(output: Path, generator: ImageGenerator) -> str:
    vb = load_json(output / vbible.FILE_NAME) or {}
    for item in vb.get("characters", []):
        path = vbible.approved_reference(vb, "character", item["character_id"], output)
        if path:
            return path
    job = ImageJob(job_id="calibration_reference", prompt=REFERENCE_PROMPT, seed=1234,
                   output_path=str(output / "imagegen_calibration" / "reference.jpg"),
                   size=gen.PORTRAIT_SIZE, final_size=gen.PORTRAIT_SIZE)
    generator.generate(job)
    return job.output_path


def contact_sheet(rows: list[list[str]], labels: list[str], destination: Path) -> None:
    thumb = (448, 248)
    sheet = Image.new("RGB", (thumb[0] * len(labels), (thumb[1] + 30) * len(rows) + 30), (12, 18, 24))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for column, label in enumerate(labels):
        draw.text((column * thumb[0] + 10, 6), label, fill=(224, 186, 121), font=font)
    for row, paths in enumerate(rows):
        for column, path in enumerate(paths):
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize(thumb), (column * thumb[0], 30 + row * (thumb[1] + 30)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, "JPEG", quality=90)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate FLUX 3 Klein generation on this machine.")
    parser.add_argument("--output-dir", default=config.OUTPUT_DIR)
    args = parser.parse_args(argv)
    output = Path(args.output_dir)
    generator = ImageGenerator(output)
    run = int(time.time())          # fresh seeds: calibration must not hit the cache
    try:
        reference = reference_image(output, generator)
        results = {name: {"seconds": [], "vram_peak_mb": 0.0} for name, _, _ in MODES}
        rows = [[] for _ in PROMPTS]
        for index, prompt in enumerate(PROMPTS):
            for name, kind, latent in MODES:
                text = (f"Create an {identity.image_noun()} of {prompt}. {STYLE}" if kind == "ref"
                        else f"{prompt.replace('the man from the first image', 'a lean 37-year-old man in a charcoal suit')}. {STYLE}")
                job = ImageJob(job_id=f"calibration_{name}_{index}", prompt=text, seed=run + index,
                               output_path=str(output / "imagegen_calibration" / f"{name}_{index}.jpg"),
                               kind=kind, references=[reference] if kind == "ref" else [],
                               latent_source=latent, size=gen.native_size())
                with VramSampler(generator.client) as sampler:
                    started = time.monotonic()
                    generator.generate(job)
                    seconds = time.monotonic() - started
                results[name]["seconds"].append(round(seconds, 1))
                results[name]["vram_peak_mb"] = max(results[name]["vram_peak_mb"], sampler.peak)
                rows[index].append(job.output_path)
                print(f"   {name:<16} prompt {index + 1}: {seconds:5.1f}s  VRAM peak {sampler.peak:.0f} MB")
    except ComfyUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    modes = {name: {"seconds_per_image": round(sum(r["seconds"]) / len(r["seconds"]), 1),
                    "vram_peak_mb": r["vram_peak_mb"], "samples": r["seconds"]}
             for name, r in results.items()}
    modes["ref"] = modes["ref_reference" if config.IMAGEGEN_LATENT_SOURCE == "reference" else "ref_empty_16x9"]
    calibration = {"date": datetime.now(timezone.utc).date().isoformat(), "size": list(gen.native_size()),
                   "modes": modes}
    gen.CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    gen.CALIBRATION_FILE.write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
    sheet = output / "imagegen_calibration" / "contact_sheet.jpg"
    contact_sheet(rows, [name for name, _, _ in MODES], sheet)
    print(f"\nSaved {gen.CALIBRATION_FILE} and {sheet}.")
    print("Compare the columns: pick IMAGEGEN_LATENT_SOURCE (reference | empty_16x9) in config.py,")
    print("and set IMAGEGEN_IDENTITY_POLICY=limited_faces if the man does not look like the reference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
