"""Compare the three dark-illustration variants on the same 6 prompts.

Run:  python -m scripts.calibrate_style

Prompts: 2 character-free establishing shots, 2 character shots, 1 object
insert and 1 thumbnail composition, each rendered as (1) engraving,
(2) charcoal & gouache and (3) hybrid. Writes a labelled contact sheet to
output/style_calibration/contact_sheet.jpg and timings to
data/style_calibration.json. Pick one and set VISUAL_STYLE_VARIANT in config.py.
"""
from __future__ import annotations

import argparse
import json
import sys
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

VARIANTS = ("engraving", "charcoal_gouache", "hybrid")
# A different seed per variant, so columns differ in composition as well as texture.
VARIANT_SEEDS = {"engraving": 4242, "charcoal_gouache": 5353, "hybrid": 6464}
RESULT_FILE = Path(__file__).resolve().parent.parent / "data" / "style_calibration.json"


def prompts(vb: dict) -> list[tuple[str, str]]:
    """(label, subject) pairs, using this episode's visual bible when it exists."""
    locations = vb.get("locations") or []
    characters = vb.get("characters") or []
    place = lambda i, fallback: (locations[i]["descriptor"] if len(locations) > i else fallback)
    person = lambda i, fallback: (characters[i]["descriptor"] if len(characters) > i else fallback)
    return [
        ("establishing 1", f"establishing wide view of {place(0, 'a vast loft with vaulted concrete and a long dark table')}, empty, no people"),
        ("establishing 2", f"establishing wide view of {place(1, 'an endless corridor of closed doors receding into fog')}, empty, no people"),
        ("character 1", f"{person(0, 'a lean man in his late thirties in a charcoal suit')} standing alone at a tall window at night, small against the room"),
        ("character 2", f"{person(1, 'a woman in her mid thirties with a dark bob')} seated at the far end of a long table, face half in shadow"),
        ("object insert", "close-up of a smoked glass decanter catching a single warm glint on a dark oak table"),
        ("thumbnail", f"{person(0, 'a lean man in his late thirties')} turning toward an unseen presence, "
                      "strong focal point on the right, dark negative space on the left"),
    ]


def contact_sheet(grid: list[list[str]], rows: list[str], columns: list[str], destination: Path) -> None:
    thumb, label_w, header = (384, 212), 150, 34
    sheet = Image.new("RGB", (label_w + thumb[0] * len(columns), header + (thumb[1] + 8) * len(rows)),
                      tuple(config.COLORS["background"]))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    gold = tuple(config.COLORS["highlight"])
    for column, name in enumerate(columns):
        draw.text((label_w + column * thumb[0] + 10, 8), f"{column + 1}. {name}", fill=gold, font=font)
    for row, (label, paths) in enumerate(zip(rows, grid)):
        y = header + row * (thumb[1] + 8)
        draw.text((10, y + thumb[1] // 2 - 10), label, fill=tuple(config.COLORS["light"]), font=font)
        for column, path in enumerate(paths):
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize(thumb), (label_w + column * thumb[0], y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, "JPEG", quality=90)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare the dark-illustration style variants.")
    parser.add_argument("--output-dir", default=config.OUTPUT_DIR)
    parser.add_argument("--variants", default=",".join(VARIANTS),
                        help="comma-separated subset, e.g. --variants engraving")
    args = parser.parse_args(argv)
    variants = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = [name for name in variants if name not in VARIANTS]
    if unknown or not variants:
        parser.error(f"unknown variants {unknown}; choose from {', '.join(VARIANTS)}")
    output = Path(args.output_dir)
    vb = load_json(output / vbible.FILE_NAME) or {}
    items = prompts(vb)
    generator = ImageGenerator(output)
    folder = output / "style_calibration"
    grid = [[] for _ in items]
    timings = {variant: [] for variant in variants}
    try:
        for variant in variants:
            suffix = identity.style_suffix("dark_illustration", variant)
            for index, (label, subject) in enumerate(items):
                job = ImageJob(job_id=f"style_{variant}_{index}", seed=VARIANT_SEEDS[variant] + index,
                               prompt=f"An illustration: {subject}. {suffix}",
                               output_path=str(folder / f"{variant}_{index}.jpg"), size=gen.native_size())
                started = time.monotonic()
                generator.generate(job)
                seconds = round(time.monotonic() - started, 1)
                timings[variant].append(seconds)
                grid[index].append(job.output_path)
                print(f"   {variant:<17} {label:<15} {seconds:5.1f}s")
    except ComfyUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    sheet = folder / "contact_sheet.jpg"
    contact_sheet(grid, [label for label, _ in items], variants, sheet)
    previous = load_json(RESULT_FILE) or {}
    results = previous.get("variants", {}) if previous.get("style_version") == identity.STYLE_VERSION else {}
    for variant, values in timings.items():
        results[variant] = {"seconds_per_image": round(sum(values) / len(values), 1), "samples": values,
                            "seed_base": VARIANT_SEEDS[variant]}
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps({
        "date": datetime.now(timezone.utc).date().isoformat(), "style_version": identity.STYLE_VERSION,
        "variants": results,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved {sheet} and {RESULT_FILE}.")
    print("Pick a column and set VISUAL_STYLE_VARIANT in config.py "
          "(engraving | charcoal_gouache | hybrid), then: python -m scripts.generate_style_refs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
