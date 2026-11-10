"""Is it the referenced character, or a generic stand-in? (Phase 3.9.8-C, layer 1, deterministic.)

* **Face:** for a shot with a listed character and a face at least ``MIN_FACE_PX`` tall (YuNet), the SFace embedding
  of the face is compared with the approved face sheet and with up to ``MAX_ANCHORS`` of that character's own best
  close-ups (shots that already match the sheet strongly). Below ``THRESHOLD`` the face is a generic one.
* **Hair and clothes,** when the face is too small or hidden (advisory): the dominant colour of the head and torso
  of the figure against the full-body sheet (else the face sheet's hair) and the scene's wardrobe text.

Calibrated on "She Filmed Every Moment" (docs/reports/3.9.8): the close-ups drawn with Hannah's sheet score
0.65-0.92 against it; the six shots the user flagged as a generic woman score 0.11-0.47.
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

import numpy as np

from agents import visual_bible as vbible

THRESHOLD = 0.50
MIN_FACE_PX = 40
ANCHOR_MIN = 0.80             # a shot this close to the sheet can vouch for the character
MAX_ANCHORS = 3
ANCHOR_TYPES = ("close_up", "medium")
COLOUR_WORDS = {"red": ("red", "auburn", "ginger", "copper", "strawberry"), "brown": ("brown", "chestnut", "brunette"),
                "black": ("black", "raven", "jet"), "blond": ("blond", "blonde", "golden", "fair", "flaxen"),
                "grey": ("grey", "gray", "silver", "white"), "cream": ("cream", "ivory", "beige", "oatmeal")}


def colour_name(rgb) -> str:
    """A coarse name for a mean colour: what hair or a sweater would be called."""
    r, g, b = (max(0.0, min(1.0, value / 255.0)) for value in rgb)
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    hue *= 360
    if light < 0.13:
        return "black"
    if sat < 0.16:
        return "grey" if light < 0.75 else "cream"
    if hue < 28 or hue >= 340:
        return "red" if sat >= 0.35 and light >= 0.2 else "brown"
    if hue < 50:
        return "blond" if light >= 0.45 else "brown"
    if hue < 70 and light >= 0.6:
        return "cream"
    return "other"


def expected_colour(text: str) -> str | None:
    words = re.findall(r"[a-z]+", str(text or "").lower())
    for name, synonyms in COLOUR_WORDS.items():
        if any(word in synonyms for word in words):
            return name
    return None


def _dominant(pixels: np.ndarray) -> tuple:
    """Mean of the most saturated tenth of the pixels: ink, shadow and backdrop are not the colour of the hair."""
    flat = pixels.reshape(-1, 3).astype(np.float32)
    if not len(flat):
        return (0, 0, 0)
    saturation = flat.max(axis=1) - flat.min(axis=1)
    keep = flat[saturation >= np.percentile(saturation, 90)]
    return tuple(float(value) for value in keep.mean(axis=0))


def regions(rgb: np.ndarray, box) -> tuple[np.ndarray, np.ndarray]:
    """(head, torso) pixels of a person box (normalized): the top ~13 % and 22-45 % of the figure."""
    height, width = rgb.shape[:2]
    x0, y0, x1, y1 = (int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))
    tall = max(1, y1 - y0)
    inset = max(1, (x1 - x0) // 6)
    head = rgb[y0:y0 + int(tall * 0.13), x0 + inset:x1 - inset]
    torso = rgb[y0 + int(tall * 0.22):y0 + int(tall * 0.45), x0 + inset:x1 - inset]
    return head, torso


class IdentityCheck:
    """Scores shots against approved sheets. ``detector`` is the layer-1 ``Detector`` (loaded lazily)."""

    def __init__(self, detector, vb: dict, output_dir, shots: list[dict] | None = None, threshold: float = THRESHOLD):
        self.detector, self.vb, self.output = detector, vb, Path(output_dir)
        self.shots = shots or []
        self.threshold = threshold
        self._sheets: dict = {}
        self._anchors: dict = {}

    # ── faces ───────────────────────────────────────────────────────────────
    def _faces(self, path) -> tuple[np.ndarray | None, list[dict]]:
        import cv2
        self.detector._load()
        bgr = cv2.imread(str(path))
        if bgr is None:
            return None, []
        height = bgr.shape[0]
        found = []
        for face in self.detector.faces(bgr):
            pixels = (face["box"][3] - face["box"][1]) * height
            found.append({**face, "pixels": round(pixels), "feature": self.detector._recognizer.feature(
                self.detector._recognizer.alignCrop(bgr, face["raw"]))})
        return bgr, sorted(found, key=lambda item: -item["pixels"])

    def similarity(self, first, second) -> float:
        import cv2
        return float(self.detector._recognizer.match(first, second, cv2.FaceRecognizerSF_FR_COSINE))

    def sheet(self, cid: str):
        """(feature, face box, rgb) of the approved face sheet, or None."""
        self.detector._load()
        if cid not in self._sheets:
            path = vbible.approved_reference(self.vb, "character", cid, self.output)
            value = None
            if path:
                bgr, faces = self._faces(path)
                if faces:
                    value = (faces[0]["feature"], faces[0]["box"], bgr[..., ::-1])
            self._sheets[cid] = value
        return self._sheets[cid]

    def anchors(self, cid: str) -> list:
        """Features of the character's own shots that match the sheet strongly (single-character close-ups and mediums)."""
        if cid in self._anchors:
            return self._anchors[cid]
        sheet = self.sheet(cid)
        found = []
        if sheet is not None:
            for shot in self.shots:
                if shot.get("characters") != [cid] or shot.get("shot_type") not in ANCHOR_TYPES or shot.get("reuse_of"):
                    continue
                path = self.output / str(shot.get("image_path", "")).replace("\\", "/")
                if not path.is_file():
                    continue
                _, faces = self._faces(path)
                if faces and faces[0]["pixels"] >= 2 * MIN_FACE_PX:
                    score = self.similarity(sheet[0], faces[0]["feature"])
                    if score >= ANCHOR_MIN:
                        found.append((score, shot["shot_id"], faces[0]["feature"]))
        found.sort(key=lambda item: -item[0])
        self._anchors[cid] = found[:MAX_ANCHORS]
        return self._anchors[cid]

    # ── one shot ────────────────────────────────────────────────────────────
    def shot(self, shot_id: str) -> dict | None:
        return next((item for item in self.shots if item.get("shot_id") == shot_id), None)

    def score(self, path, characters: list[str], *, shot_id: str = "", scene_id: str = "", shot_type: str = "") -> dict:
        """{cid: {"status": match|generic|small|no_face|no_sheet, "similarity", "face_px", "anchors", "hair"?}}"""
        bgr, faces = self._faces(path)
        result = {}
        free = [face for face in faces if face["pixels"] >= MIN_FACE_PX]
        for cid in characters:
            sheet = self.sheet(cid)
            if sheet is None:
                result[cid] = {"status": "no_sheet"}
                continue
            anchors = [item for item in self.anchors(cid) if item[1] != shot_id]
            best = None
            for face in free:
                value = max([self.similarity(sheet[0], face["feature"])] + [self.similarity(item[2], face["feature"]) for item in anchors])
                if best is None or value > best[0]:
                    best = (value, face)
            if best is None:
                status = "small" if faces else "no_face"
                result[cid] = {"status": status, "face_px": faces[0]["pixels"] if faces else 0}
                if bgr is not None and shot_type not in ("hands", "insert"):
                    result[cid]["hair"] = self.hair(bgr[..., ::-1], cid, scene_id)
                continue
            free.remove(best[1])
            result[cid] = {"status": "match" if best[0] >= self.threshold else "generic", "similarity": round(best[0], 3),
                           "face_px": best[1]["pixels"], "anchors": len(anchors)}
        return result

    def hair(self, rgb: np.ndarray, cid: str, scene_id: str = "") -> dict:
        """Advisory: hair and clothing colours of the largest figure against the sheet and the scene's wardrobe."""
        import cv2
        self.detector._load()
        persons = self.detector.persons(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if not persons:
            return {"checked": False}
        head, torso = regions(rgb, persons[0]["box"])
        found_hair, found_clothes = colour_name(_dominant(head)), colour_name(_dominant(torso))
        wanted_hair = self.sheet_hair_colour(cid)
        wanted_clothes = expected_colour(vbible.wardrobe(self.vb, cid, scene_id))
        notes = []
        if wanted_hair and found_hair not in (wanted_hair, "black" if wanted_hair == "brown" else wanted_hair):
            notes.append(f"cabelo {wanted_hair} esperado, {found_hair} encontrado")
        light = {"cream", "blond", "grey"}                 # a cream sweater drawn in ink reads as any of these
        if wanted_clothes and found_clothes != wanted_clothes and not ({wanted_clothes, found_clothes} <= light):
            notes.append(f"roupa {wanted_clothes} esperada, {found_clothes} encontrada")
        return {"checked": True, "hair": found_hair, "expected_hair": wanted_hair, "clothes": found_clothes,
                "expected_clothes": wanted_clothes, "notes": notes}

    def sheet_hair_colour(self, cid: str) -> str | None:
        """Hair colour on the full-body sheet, else the face sheet, read the same way as on a shot."""
        import cv2
        self.detector._load()
        for kind in ("body", "character"):
            path = vbible.approved_reference(self.vb, kind, cid, self.output)
            bgr = cv2.imread(path) if path else None
            if bgr is None:
                continue
            persons = self.detector.persons(bgr)
            if persons:
                return colour_name(_dominant(regions(bgr[..., ::-1], persons[0]["box"])[0]))
        return None


def issues(result: dict, vb: dict, *, regenerated: bool = False) -> list[dict]:
    """QA issues from ``IdentityCheck.score``: a generic face is blocking until the shot has been regenerated once."""
    from video.qa import base
    found = []
    for cid, item in result.items():
        name = (vbible.character(vb, cid) or {}).get("name") or cid.replace("_", " ").split()[0].title()
        if item.get("status") == "generic":
            found.append(base.issue("identity", severity="advisory" if regenerated else "blocking", target=cid, source="deterministic",
                                    note=f"personagem genérico: rosto não confere com a folha de {name} ({item['similarity']:.2f})"))
        for note in (item.get("hair") or {}).get("notes", []):
            found.append(base.issue("identity", severity="advisory", target=cid, source="deterministic", note=f"{name}: {note}"))
    return found
