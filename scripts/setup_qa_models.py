"""Download the layer-1 visual QA models (OpenCV DNN, CPU) into models/qa/.

Run:  python -m scripts.setup_qa_models            # downloads what is missing (~75 MB)
      python -m scripts.setup_qa_models --check    # only reports

Models (opencv_zoo, github.com/opencv/opencv_zoo): YuNet face detector (MIT),
YOLOX-s object detector, used for "person" (Apache-2.0), SFace face recognizer
(Apache-2.0). Also needs ``pip install opencv-python-headless==4.11.0.86``.
"""
from __future__ import annotations

import argparse
import sys

import requests

from video.qa import deterministic as det

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models/"
SOURCES = {
    det.FACE_MODEL: (BASE + "face_detection_yunet/" + det.FACE_MODEL, 232_589),
    det.PERSON_MODEL: (BASE + "object_detection_yolox/" + det.PERSON_MODEL, 35_858_002),
    det.RECOGNIZER_MODEL: (BASE + "face_recognition_sface/" + det.RECOGNIZER_MODEL, 38_696_353),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Download the local visual QA detector models.")
    parser.add_argument("--check", action="store_true", help="only report what is missing")
    args = parser.parse_args(argv)
    folder = det.models_dir()
    missing = det.missing_models()
    for name in SOURCES:
        print(f"{'ok     ' if name not in missing else 'missing'} {folder / name}")
    if args.check or not missing:
        return 0 if not missing else 1
    folder.mkdir(parents=True, exist_ok=True)
    for name in missing:
        url, size = SOURCES[name]
        print(f"-> {name} ({size / 1e6:.1f} MB) from {url}")
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        if len(response.content) != size:
            print(f"error: {name}: got {len(response.content)} bytes, expected {size}", file=sys.stderr)
            return 1
        (folder / name).write_bytes(response.content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
