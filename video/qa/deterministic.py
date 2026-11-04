"""Layer 1 of the visual QA: deterministic checks on the CPU, every shot (Phase 3.5).

* People: YOLOX-s (COCO "person", OpenCV DNN) counts whole bodies, so figures
  seen from the back and small background figures count too.
* Faces: YuNet finds faces, each one is attached to the person box around it,
  and SFace embeddings compare them: two faces above ``DUPLICATE_SIM`` cosine
  similarity are the same character drawn twice.
* Headcount from the detector: the largest ``expected people`` boxes are the
  main characters, background figures the shot allows come next, anything
  beyond is an extra person (advisory when the scene has unnamed guests).
* Borders and comic panels (``image_checks``) and, when Tesseract is installed,
  OCR text with boxes.

Every box in a QA result comes from here; the VLM of layer 2 only answers
yes/no questions about crops. Thresholds were calibrated on the 8-frame probe
set (tests/fixtures/qa_probe_expected.json): with ``PERSON_MIN`` 0.5 the person
count matched all 8 frames; duplicated faces scored 0.44 and 0.68, distinct
faces at most 0.32.

The models live in ``models/qa/`` (downloaded from opencv_zoo, not in git).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image

import config
from video import image_checks
from video.qa import base

ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = ROOT / "models" / "qa"
PERSON_MODEL = "object_detection_yolox_2022nov.onnx"
FACE_MODEL = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "face_recognition_sface_2021dec.onnx"
INPUT_SIZE = 640
PERSON_CLASS = 0
PERSON_MIN = 0.5            # detector score for a sure person
PERSON_UNSURE = 0.3         # between this and PERSON_MIN, layer 2 confirms
FACE_MIN = 0.6
DUPLICATE_SIM = 0.40        # SFace cosine similarity: same face
DUPLICATE_UNSURE = 0.30     # between this and DUPLICATE_SIM, layer 2 compares the two crops
NMS_IOU = 0.45


class DetectorUnavailable(RuntimeError):
    """OpenCV or the ONNX models are missing."""


def models_dir() -> Path:
    return Path(getattr(config, "IMAGEGEN_QA_MODELS_DIR", "") or MODELS_DIR)


def missing_models() -> list[str]:
    return [name for name in (PERSON_MODEL, FACE_MODEL, RECOGNIZER_MODEL) if not (models_dir() / name).is_file()]


def _box(x0, y0, x1, y1, width, height) -> list[float]:
    clamp = lambda value: min(max(value, 0.0), 1.0)
    return [round(clamp(x0 / width), 4), round(clamp(y0 / height), 4),
            round(clamp(x1 / width), 4), round(clamp(y1 / height), 4)]


def _contains(box, point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


class Detector:
    """Lazy-loaded OpenCV detectors (CPU)."""

    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder or models_dir())
        self._person = self._face = self._recognizer = None

    def available(self) -> bool:
        try:
            import cv2  # noqa: F401
        except ImportError:
            return False
        return not [name for name in (PERSON_MODEL, FACE_MODEL, RECOGNIZER_MODEL)
                    if not (self.folder / name).is_file()]

    def _load(self):
        if self._person is not None:
            return
        try:
            import cv2
        except ImportError:
            raise DetectorUnavailable("opencv-python-headless is not installed") from None
        missing = [name for name in (PERSON_MODEL, FACE_MODEL, RECOGNIZER_MODEL) if not (self.folder / name).is_file()]
        if missing:
            raise DetectorUnavailable(f"QA detector models missing in {self.folder}: {', '.join(missing)}")
        self._person = cv2.dnn.readNet(str(self.folder / PERSON_MODEL))
        self._face = cv2.FaceDetectorYN.create(str(self.folder / FACE_MODEL), "", (320, 320), FACE_MIN, 0.3, 50)
        self._recognizer = cv2.FaceRecognizerSF.create(str(self.folder / RECOGNIZER_MODEL), "")

    def persons(self, bgr: np.ndarray) -> list[dict]:
        """YOLOX person boxes (score >= PERSON_UNSURE), normalized, best first."""
        import cv2
        height, width = bgr.shape[:2]
        scale = min(INPUT_SIZE / height, INPUT_SIZE / width)
        padded = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, np.uint8)
        resized = cv2.resize(bgr, (int(width * scale), int(height * scale)))
        padded[:resized.shape[0], :resized.shape[1]] = resized
        self._person.setInput(cv2.dnn.blobFromImage(cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)))
        output = self._person.forward()[0]
        grids, strides = [], []
        for stride in (8, 16, 32):
            cells = INPUT_SIZE // stride
            xs, ys = np.meshgrid(np.arange(cells), np.arange(cells))
            grid = np.stack((xs, ys), 2).reshape(-1, 2)
            grids.append(grid)
            strides.append(np.full((grid.shape[0], 1), stride))
        grids, strides = np.concatenate(grids), np.concatenate(strides)
        output[:, :2] = (output[:, :2] + grids) * strides
        output[:, 2:4] = np.exp(output[:, 2:4]) * strides
        scores = output[:, 4] * output[:, 5 + PERSON_CLASS]
        keep = scores >= PERSON_UNSURE
        rects = [[float(cx - w / 2) / scale, float(cy - h / 2) / scale, float(w) / scale, float(h) / scale]
                 for cx, cy, w, h in output[keep, :4]]
        kept = scores[keep].tolist()
        indices = np.array(cv2.dnn.NMSBoxes(rects, kept, PERSON_UNSURE, NMS_IOU)).flatten() if rects else []
        found = [{"box": _box(rects[i][0], rects[i][1], rects[i][0] + rects[i][2], rects[i][1] + rects[i][3],
                              width, height), "score": round(float(kept[i]), 3)} for i in indices]
        return sorted(found, key=lambda item: -item["score"])

    def faces(self, bgr: np.ndarray) -> list[dict]:
        height, width = bgr.shape[:2]
        self._face.setInputSize((width, height))
        _, faces = self._face.detect(bgr)
        found = []
        for row in faces if faces is not None else []:
            x, y, w, h = (float(value) for value in row[:4])
            found.append({"box": _box(x, y, x + w, y + h, width, height), "score": round(float(row[-1]), 3),
                          "raw": row})
        return found

    def detect(self, path) -> dict:
        """People with their faces, uncertain figures and face similarities of one image."""
        import cv2
        self._load()
        started = time.monotonic()
        bgr = cv2.imread(str(path))
        if bgr is None:
            raise DetectorUnavailable(f"cannot read {path}")
        candidates = self.persons(bgr)
        persons = [dict(item, face=None) for item in candidates if item["score"] >= PERSON_MIN]
        unsure = [item for item in candidates if item["score"] < PERSON_MIN]
        features = {}
        for face in sorted(self.faces(bgr), key=lambda item: -item["score"]):
            center = ((face["box"][0] + face["box"][2]) / 2, (face["box"][1] + face["box"][3]) / 2)
            holders = [index for index, person in enumerate(persons) if _contains(person["box"], center)]
            if not holders:              # a face the body detector missed (tight close-up)
                persons.append({"box": face["box"], "score": face["score"], "face": None})
                holders = [len(persons) - 1]
            owner = min(holders, key=lambda index: base.area(persons[index]["box"]))
            if persons[owner]["face"] is not None:
                continue                 # a second "face" in one body is a hand or a pattern
            persons[owner]["face"] = {"box": face["box"], "score": face["score"]}
            features[owner] = self._recognizer.feature(self._recognizer.alignCrop(bgr, face["raw"]))
        pairs = []
        owners = sorted(features)
        for position, first in enumerate(owners):
            for second in owners[position + 1:]:
                similarity = float(self._recognizer.match(features[first], features[second],
                                                          cv2.FaceRecognizerSF_FR_COSINE))
                pairs.append({"persons": [first, second], "similarity": round(similarity, 3)})
        height, width = bgr.shape[:2]
        return {"size": [width, height], "persons": persons, "unsure": unsure, "face_pairs": pairs,
                "seconds": round(time.monotonic() - started, 2)}


def roles(detection: dict, expected: dict) -> dict:
    """Which detected persons are main characters, allowed background figures, duplicates or extras."""
    persons = detection["persons"]
    order = sorted(range(len(persons)), key=lambda index: -base.area(persons[index]["box"]))
    duplicates = {}
    for pair in detection["face_pairs"]:
        if pair["similarity"] >= DUPLICATE_SIM:
            first, second = pair["persons"]
            copy = min((first, second), key=lambda index: base.area(persons[index]["box"]))
            original = second if copy == first else first
            duplicates.setdefault(copy, (original, pair["similarity"]))
    ranked = [index for index in order if index not in duplicates]
    main = ranked[:expected["people"]]
    background = ranked[expected["people"]:expected["people"] + expected.get("background", 0)]
    extras = ranked[expected["people"] + expected.get("background", 0):]
    return {"main": main, "background": background, "extras": extras, "duplicates": duplicates}


def layer1(detection: dict, expected: dict, image: Image.Image | None = None) -> list[dict]:
    """Issues layer 1 can decide alone: duplicates, extra people, missing people, borders, OCR text."""
    persons = detection["persons"]
    found = roles(detection, expected)
    issues = []
    for copy, (original, similarity) in sorted(found["duplicates"].items()):
        issues.append(base.issue("duplicate_person", box=persons[copy]["box"],
                                 note=f"the same face appears twice (similarity {similarity:.2f}); "
                                      f"this is the smaller copy", source="deterministic"))
    severity = "advisory" if expected.get("others_present") else "blocking"
    for index in found["extras"]:
        issues.append(base.issue("extra_person", severity=severity, box=persons[index]["box"],
                                 note=f"{len(persons)} people detected, {expected['people']} expected",
                                 source="deterministic"))
    if len(persons) < expected["people"]:
        issues.append(base.issue("other", severity="advisory",
                                 note=f"{len(persons)} people detected, {expected['people']} expected "
                                      "(the detector may miss hands-only or very stylized figures)",
                                 source="deterministic"))
    if image is not None:
        issues += red_patch_issues(image, persons)
        issues += blank_issues(image)
        report = image_checks.inspect(image)
        if report["bordered"] or report["panels"]:
            issues.append(base.issue("border", note="paper margins or comic panels", source="deterministic"))
        issues += ocr_issues(image)
    return issues


# Calibrated on episodes 2-3: Thomas's shoulder patches (s03_01, s02_06, s01_09) are 0.7-1 % of his box; a wine
# glass taken for a small "person" is not a figure. Dusty Crimson is muted, hence the low saturation floor.
RED_PATCH_SHARE = 0.006    # of the person's box
RED_PATCH_MIN_BOX = 0.05   # of the frame: only real figures


def red_patch_issues(image: Image.Image, persons: list[dict]) -> list[dict]:
    """Advisory, never auto-fixed (Phase 3.9.3-G): a crimson patch on a person may read as blood, against the
    art direction. A hint for the reviewer."""
    import cv2
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    crimson = ((hsv[..., 0] <= 6) | (hsv[..., 0] >= 172)) & (hsv[..., 1] >= 70) & (hsv[..., 2] >= 45)
    issues = []
    for person in persons:
        x0, y0, x1, y1 = person["box"]
        if base.area(person["box"]) < RED_PATCH_MIN_BOX:
            continue
        left, top, right, bottom = int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)
        region = crimson[top:bottom, left:right].astype(np.uint8)
        if not region.size:
            continue
        count, _, stats, _ = cv2.connectedComponentsWithStats(region, connectivity=8)
        largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
        if largest / region.size > RED_PATCH_SHARE:
            found = base.issue("other", severity="advisory", box=person["box"],
                               note="mancha vermelha na roupa (parece sangue?)", source="deterministic")
            found["hint"] = True
            found["red_patch"] = round(largest / region.size, 4)
            issues.append(found)
    return issues


BLANK_STD = 3.0     # grey-level standard deviation below which a frame shows nothing


def blank_issues(image: Image.Image) -> list[dict]:
    """A (nearly) uniform frame: the sampler produced nothing."""
    from PIL import ImageStat
    small = image.convert("L").resize((64, 36))
    if ImageStat.Stat(small).stddev[0] < BLANK_STD:
        return [base.issue("blank", note="the frame is (nearly) uniform", source="deterministic")]
    return []


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_issues(image: Image.Image, min_confidence: float = 70.0) -> list[dict]:
    """Words Tesseract reads with confidence, as blocking ``text`` issues with boxes (when installed)."""
    if not ocr_available():
        return []
    import pytesseract
    data = pytesseract.image_to_data(image.convert("L"), output_type=pytesseract.Output.DICT)
    width, height = image.size
    issues = []
    for index, word in enumerate(data.get("text", [])):
        word = str(word or "").strip()
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if len([c for c in word if c.isalnum()]) >= 3 and confidence >= min_confidence:
            left, top = data["left"][index], data["top"][index]
            box = _box(left, top, left + data["width"][index], top + data["height"][index], width, height)
            issues.append(base.issue("text", box=box, note=f"lettering {word!r}", source="ocr"))
    return issues
