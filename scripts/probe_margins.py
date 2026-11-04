"""Does the prompt wording cause paper margins? (Phase 3.9.3-E.4)

Run:  python -m scripts.probe_margins [--seeds 8] [--out docs/reports/3.9.3]

Renders 3 prompts x N seeds twice with ComfyUI: "before" (the old wording, full-bleed clause at the end,
"visible paper grain") and "after" (full-bleed clause first, texture words that suggest no sheet of
paper). Counts margins with video.qa.borders and writes results and one contact sheet per wording.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from agents import identity
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob
from video.qa import borders
from video.shot_generation import native_size

SUBJECTS = (
    "An illustration: establishing wide view of a deserted vast expanse of dark heather and cold grass under a "
    "starlit night sky, ancient granite boundary stones; perfectly still.",
    "An illustration: a tired baker in a flour-dusted apron standing on open moorland at night, ancient stones "
    "behind him, wide shot.",
    "An illustration: establishing wide view of a deserted tidy cottage kitchen, a dark wood stove, ceramic bowls "
    "on a trestle table, flickering lamp light; perfectly still.",
)
OLD_TEXTURE = "visible paper grain"
NEW_TEXTURE = "fine grain and ink texture across the whole picture"


def wording(subject: str, which: str) -> str:
    suffix = identity.style_suffix(figures="baker" in subject)
    if which == "before":
        return f"{subject} {suffix.replace(NEW_TEXTURE, OLD_TEXTURE)}"
    body = suffix.replace(OLD_TEXTURE, NEW_TEXTURE).replace(identity.FULL_BLEED + ". ", "", 1)
    return f"{identity.FULL_BLEED}. {subject} {body}"


def sheet(paths: list[tuple[Path, bool]], target: Path, columns: int = 8) -> None:
    width, height = 240, 135
    rows = (len(paths) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * width, rows * height), "black")
    draw = ImageDraw.Draw(canvas)
    for index, (path, margin) in enumerate(paths):
        with Image.open(path) as image:
            x, y = (index % columns) * width, (index // columns) * height
            canvas.paste(image.convert("RGB").resize((width, height)), (x, y))
            if margin:
                draw.rectangle((x, y, x + width - 1, y + height - 1), outline=(255, 60, 60), width=2)
    canvas.save(target, quality=88)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--out", default="docs/reports/3.9.3")
    args = parser.parse_args(argv)
    out = Path(args.out)
    work = out / "probe_margins"
    work.mkdir(parents=True, exist_ok=True)
    generator = ImageGenerator(str(work), ComfyClient())
    results = {"before": [], "after": []}
    for which in ("before", "after"):
        rendered = []
        for number, subject in enumerate(SUBJECTS):
            for seed in range(args.seeds):
                job = ImageJob(job_id=f"probe_{which}_{number}_{seed}", prompt=wording(subject, which),
                               seed=4200 + 97 * number + seed, output_path=str(work / f"{which}_{number}_{seed}.jpg"),
                               size=native_size())
                path = Path(job.output_path)
                if not path.is_file():
                    image, _ = generator._render(job, job.seed, [])
                    image.save(path, quality=93)
                with Image.open(path) as image:
                    report = borders.detect(image.convert("RGB"))
                results[which].append({"prompt": number, "seed": job.seed, "margin": report["margin"],
                                       "sides": report["marked"], "max_share": report["max_share"]})
                rendered.append((path, report["margin"]))
                print(f"{which} {number} {seed}: margin={report['margin']}", flush=True)
                (out / "probe_margins.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
        sheet(rendered, out / f"probe_margins_{which}.jpg", args.seeds)
    for which, rows in results.items():
        print(f"{which}: {sum(row['margin'] for row in rows)}/{len(rows)} with a margin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
