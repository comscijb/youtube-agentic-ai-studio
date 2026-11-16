"""Music that follows the story (Phase 4.2-E).

* ``index`` — tags the library (``music library/``) by the mood in the file names and tracks.json (dark / melancholy /
  dramatic) plus an energy analysis (RMS and onset density over the first 90 s), in ``music_index.json``; ``gaps``
  says which moods are thin, so the user can add tracks from the YouTube Audio Library.
* ``build`` — a plan per episode: 2-3 tracks matched to the story's parts (setup → tension → reversal → ending),
  crossfaded at the scene boundaries where the part changes; a **sound drop** (the bed nearly silent) from 1.5 s
  before the reversal's key line until just after it; a **swell** under the final narrated line. The end card stays
  silent (the video appends it after the narration; no logo sting exists yet).
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

import config
from agents.narrative import atomic_write_json, load_json

INDEX_FILE = "music_index.json"
PARTS = {"cold_open": "tension", "opening": "setup", "setup": "setup", "development": "setup", "climax": "tension",
         "reversal": "reversal", "resolution": "ending", "aftermath": "ending"}
WANTED = {"setup": ("melancholy", "low"), "tension": ("dramatic", "high"), "reversal": ("dark", "mid"), "ending": ("melancholy", "low")}
MOOD_ALIASES = {"dark": {"sombrio", "somber", "sombre", "dark"}, "melancholy": {"melancolico", "melancholy", "melancholic"},
                "dramatic": {"dramatic", "dramatico"}}
THIN = 5                       # fewer tracks than this in a mood: the episodes start to repeat
CROSSFADE = 3.0
DROP_LEAD = 1.5


def enabled() -> bool:
    return bool(getattr(config, "MUSIC_PLAN_ENABLED", True))


def library_dir() -> Path:
    return Path(getattr(config, "MUSIC_LIBRARY_DIR", "") or "music library")


def mood_of(track: dict) -> str:
    from video.music import _fold
    words = {_fold(mood) for mood in track.get("moods", [])} | {_fold(track.get("file", "")).rsplit(" - ", 1)[-1].rsplit(".", 1)[0]}
    for mood, aliases in MOOD_ALIASES.items():
        if words & aliases:
            return mood
    return "dark"


def energy(path: str, seconds: float = 90.0) -> dict:
    """RMS (dBFS) and onset density (onsets per second, from the spectral flux) of the first ``seconds``."""
    from video.music import ffmpeg_exe
    rate = 11025
    result = subprocess.run([ffmpeg_exe(), "-v", "error", "-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", str(rate),
                             "-f", "s16le", "-"], capture_output=True, check=False)
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < rate:
        return {"rms_db": -90.0, "onsets_per_second": 0.0}
    rms = float(np.sqrt(np.mean(samples ** 2)))
    frame, hop = 1024, 512
    count = (samples.size - frame) // hop
    window = np.hanning(frame)
    spectra = np.abs(np.fft.rfft(np.stack([samples[i * hop:i * hop + frame] * window for i in range(count)]), axis=1))
    flux = np.maximum(0, np.diff(spectra, axis=0)).sum(axis=1)
    threshold = flux.mean() + 1.5 * flux.std()
    peaks = int(np.sum((flux[1:-1] > threshold) & (flux[1:-1] > flux[:-2]) & (flux[1:-1] >= flux[2:])))
    return {"rms_db": round(20 * math.log10(max(rms, 1e-6)), 1), "onsets_per_second": round(peaks / (samples.size / rate), 2)}


def energy_score(item: dict) -> float:
    return item["rms_db"] + 6 * item["onsets_per_second"]


def classify(tracks: list[dict]) -> list[dict]:
    """Energy by thirds within this library (absolute thresholds put almost every classical track in one class)."""
    ranked = sorted(tracks, key=energy_score)
    for position, item in enumerate(ranked):
        share = position / max(1, len(ranked) - 1)
        item["energy"] = "low" if share < 1 / 3 else "high" if share > 2 / 3 else "mid"
    return tracks


def index(root: Path | None = None, *, analyse=energy) -> dict:
    """music_index.json: every track with its mood, duration and energy (re-analysed only when the file changed)."""
    from video.music import library_tracks, media_duration
    root = Path(root or library_dir())
    path = root / INDEX_FILE
    saved = {item["file"]: item for item in (load_json(path) or {}).get("tracks", [])}
    tracks = []
    for track in library_tracks(str(root)):
        stamp = int(Path(track["path"]).stat().st_mtime)
        previous = saved.get(track["file"])
        if previous and previous.get("mtime") == stamp:
            tracks.append(previous)
            continue
        measured = analyse(track["path"])
        item = {"file": track["file"], "mood": mood_of(track), "duration": round(media_duration(track["path"]), 1),
                "mtime": stamp, **measured}
        tracks.append(item)
    classify(tracks)
    record = {"tracks": tracks, "gaps": gaps(tracks)}
    atomic_write_json(path, record)
    return record


def gaps(tracks: list[dict]) -> list[str]:
    """Moods (and mood + energy pairs the plan needs) with fewer than THIN tracks."""
    found = []
    for mood in MOOD_ALIASES:
        count = sum(1 for item in tracks if item["mood"] == mood)
        if count < THIN:
            found.append(f"{mood}: {count} faixa(s)")
    for part, (mood, level) in WANTED.items():
        if not any(item["mood"] == mood and item.get("energy") == level for item in tracks):
            found.append(f"{part}: nenhuma faixa {mood} de energia {level}")
    return found


# ── The plan for one episode ─────────────────────────────────────────────────

def parts(sections: list[dict], scenes: list[dict]) -> list[dict]:
    """Consecutive scenes grouped by story part: [{"part", "start", "end"}]."""
    found = []
    for section, scene in zip(sections, scenes):
        part = PARTS.get(section.get("section_type", ""), "setup")
        group = "tension" if part == "reversal" else part          # the reversal rides on the tension track
        if found and found[-1]["group"] == group:
            found[-1]["end"] = float(scene["end"])
            found[-1]["parts"].append(part)
        else:
            found.append({"group": group, "parts": [part], "start": float(scene["start"]), "end": float(scene["end"])})
    merged = []
    for item in found:                                   # a short part (the cold open) joins the part after it
        if merged and merged[-1]["end"] - merged[-1]["start"] < 25:
            merged[-1].update(end=item["end"], group=item["group"], parts=merged[-1]["parts"] + item["parts"])
        else:
            merged.append(dict(item))
    while len(merged) > 3:                               # at most 3 tracks: join the two middle parts
        middle = merged.pop(2)
        merged[1].update(end=middle["end"], parts=merged[1]["parts"] + middle["parts"])
    return [{"part": item["group"], "start": item["start"], "end": item["end"]} for item in merged]


def choose(tracks: list[dict], part: str, salt: str, used: set[str]) -> dict | None:
    mood, level = WANTED[part]

    def score(item):
        tiebreak = hashlib.sha256(f"{salt}|{part}|{item['file']}".encode("utf-8")).hexdigest()
        return (item["file"] in used, item["mood"] != mood, item.get("energy") != level, tiebreak)
    return sorted(tracks, key=score)[0] if tracks else None


REFERENCE_RMS_DB = -20.0       # every planned track is levelled to this before the music volume applies


def level_gain(track: dict) -> float:
    """Bring a track's RMS to the reference (the library spans -16 to -30 dB: a loud track would bury the voice)."""
    if track.get("rms_db") is None:
        return 1.0
    return round(min(4.0, max(0.25, 10 ** ((REFERENCE_RMS_DB - float(track["rms_db"])) / 20.0))), 4)


def _sentence_time(words: list[dict], text: str) -> tuple[float, float] | None:
    from video.key_lines import find_span
    span = find_span(words, text)
    return (float(words[span[0]]["start"]), float(words[span[1]]["end"])) if span else None


def build(output_dir, sections: list[dict], *, volume: float | None = None, tracks: list[dict] | None = None) -> dict:
    """{"segments": [{"path", "file", "mood", "start", "end", "offset", "fade_in", "fade_out", "volume"}], "drops", "swells"}."""
    from agents import attention_map
    output = Path(output_dir)
    timeline = load_json(output / "scene_timeline.json") or {}
    scenes = timeline.get("scenes") or []
    words = (load_json(output / "narration_words.json") or {}).get("words", [])
    voiced = [section for section in sections if str(section.get("narration", "")).strip()]
    if not scenes or len(scenes) != len(voiced):
        return {"segments": [], "drops": [], "swells": []}
    library = tracks if tracks is not None else index().get("tracks", [])
    root = library_dir()
    volume = float(volume if volume is not None else getattr(config, "MUSIC_VOLUME", 0.12))
    salt = str(output.name)
    segments, used = [], set()
    groups = parts(voiced, scenes)
    for position, group in enumerate(groups):
        track = choose(library, group["part"], salt, used)
        if track is None:
            break
        used.add(track["file"])
        start = max(0.0, group["start"] - (CROSSFADE / 2 if position else 0.0))
        end = group["end"] + (CROSSFADE / 2 if position < len(groups) - 1 else 0.0)
        segments.append({"path": str(root / track["file"]), "file": track["file"], "mood": track["mood"], "part": group["part"],
                         "start": round(start, 3), "end": round(end, 3), "offset": 0.0,
                         "fade_in": CROSSFADE if position else min(3.0, end * 0.05),
                         "fade_out": CROSSFADE if position < len(groups) - 1 else 4.0,
                         "volume": round(volume * level_gain(track), 4)})
    drops, swells = [], []
    mapped = attention_map.load(output) or {}
    reveal = next((item for item in mapped.get("sentences", []) if item.get("section_type") == "reversal"
                   and {tag["tag"].split(":")[0] for tag in item.get("tags", [])} & {"reveal", "question", "dialogue_turn"}), None)
    reversal = reveal or next((item for item in mapped.get("sentences", []) if item.get("section_type") == "reversal"), None)
    if reversal:
        timing = _sentence_time(words, reversal["text"])
        if timing:
            drops.append((round(max(0.0, timing[0] - DROP_LEAD), 3), round(timing[1] + 0.4, 3)))
    if words:
        ends = [index for index, word in enumerate(words[:-1]) if word.get("sentence_end")]
        start = float(words[ends[-1] + 1]["start"]) if ends else float(words[0]["start"])      # the final line's first word
        swells.append(round((start + float(words[-1]["end"])) / 2, 3))
    return {"segments": segments, "drops": drops, "swells": swells}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)
