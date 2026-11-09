"""Should faceless shots send the character's portrait? (Phase 3.9.4-A.2)

Run:  python -m scripts.probe_faceless --project <slug> [--seeds 3] [--out docs/reports/3.9.4]

For each shot flagged extra_person: A keeps the portrait reference, B sends none (text identity, wardrobe
and the location reference). Both use the rewritten wording. Counts people with the layer-1 detector and
measures identity (SFace, against the portrait) when a face is visible. Writes results and contact sheets.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import config
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import load_json
from studio import projects
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob
from video.qa import fixes
from video.shot_generation import native_size

FLAGGED = ("s02_01", "s02_03", "s04_03", "s06_03", "s06_05", "s07_03", "s09_03")
ARMS = {"A": "keep", "B": "none"}


def identity_score(detector, image_path: Path, portrait: str | None) -> float | None:
    """Cosine similarity of the largest face in the image and in the portrait; None without a face."""
    if not portrait or detector is None:
        return None
    import cv2
    detector._load()

    def feature(path):
        bgr = cv2.imread(str(path))
        faces = detector.faces(bgr)
        if not faces:
            return None
        best = max(faces, key=lambda face: face["score"])
        return detector._recognizer.feature(detector._recognizer.alignCrop(bgr, best["raw"]))
    first, second = feature(image_path), feature(portrait)
    if first is None or second is None:
        return None
    return round(float(detector._recognizer.match(first, second, cv2.FaceRecognizerSF_FR_COSINE)), 3)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--out", default="docs/reports/3.9.4")
    args = parser.parse_args(argv)
    project = projects.path_for(args.project)
    out = Path(args.out)
    work = out / "probe_faceless"
    work.mkdir(parents=True, exist_ok=True)
    shot_list = load_json(project / shots_module.FILE_NAME)
    vb = load_json(project / vbible.FILE_NAME)
    generator = ImageGenerator(str(work), ComfyClient())
    detector = fixes._detector()
    rows = []
    for shot_id in FLAGGED:
        shot = dict(next(item for item in shot_list["shots"] if item["shot_id"] == shot_id))
        shot["action"] = shots_module.rewrite_action(shot["action"], shot.get("characters") or [], vb, bool(shot.get("others_present")))
        expected = len(shot.get("characters") or [])
        portrait = vbible.approved_reference(vb, "character", shot["characters"][0], project) if expected else None
        for arm, choice in ARMS.items():
            if arm == "B" and not expected:
                continue                                    # no character: nothing differs
            for seed in range(args.seeds):
                path = work / f"{shot_id}_{arm}_{seed}.jpg"
                with patch.object(config, "FACELESS_PORTRAIT_REFERENCE",
                                  {key: choice for key in ("wide", "silhouette", "over_the_shoulder", "faceless")}):
                    composed = shots_module.compose_shot(shot, vb, project)
                if not path.is_file():
                    job = ImageJob(job_id=f"probe_{shot_id}_{arm}_{seed}", prompt=composed["prompt"],
                                   seed=int(shot["seed"]) + 7919 * (seed + 1), output_path=str(path),
                                   kind=composed["kind"], references=composed["references"], size=native_size())
                    generator.generate(job)
                people = len(detector.detect(path)["persons"]) if detector else None
                rows.append({"shot": shot_id, "type": shot.get("shot_type"), "face": shot.get("face_visibility"),
                             "arm": arm, "seed": seed, "expected": expected, "people": people,
                             "extra": bool(people is not None and people > expected),
                             "refs": len(composed["references"]), "identity": identity_score(detector, path, portrait)})
                print(json.dumps(rows[-1]), flush=True)
                (out / "probe_faceless.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    width, height = 240, 135
    columns = args.seeds * 2
    canvas = Image.new("RGB", (width * columns, (height + 14) * len(FLAGGED)), "black")
    draw = ImageDraw.Draw(canvas)
    for row, shot_id in enumerate(FLAGGED):
        for index, (arm, seed) in enumerate((a, s) for a in ARMS for s in range(args.seeds)):
            path = work / f"{shot_id}_{arm}_{seed}.jpg"
            if path.is_file():
                with Image.open(path) as image:
                    x, y = index * width, row * (height + 14)
                    canvas.paste(image.convert("RGB").resize((width, height)), (x, y + 14))
                    draw.text((x + 3, y + 1), f"{shot_id} {arm}{seed}", fill="white")
    canvas.save(out / "probe_faceless.jpg", quality=88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
