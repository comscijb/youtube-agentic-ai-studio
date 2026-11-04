"""
Music Manager — Local Library, ducking and loudness
===================================================
Builds the final episode audio with ffmpeg:

  • picks a track from  music library/  by mood (tracks.json metadata:
    file, moods, license, source_url, attribution), looping it with a
    crossfade when it is shorter than the episode;
  • ducks the music under the narration (sidechain compression driven by
    the narration level; MUSIC_DUCKING_RATIO sets the depth), with fades;
  • normalises the final mix to LOUDNESS_TARGET_LUFS (-14 LUFS integrated)
    and LOUDNESS_TRUE_PEAK (-1 dBTP) with a two-pass ffmpeg loudnorm;
  • records the chosen track, license and measured loudness in
    asset_manifest.json; attribution text is added to the YouTube
    description by the description builders.

If the library is empty the episode is rendered narration-only, and the log
says so explicitly.

Public API
----------
  prepare_final_audio(narration_path, output_path, ...) -> str
  mix_music_with_narration(narration_path, output_path, music_volume, library_dir) -> str
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import config

SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
METADATA_FILE = "tracks.json"
NO_MUSIC_MESSAGE = "no music track found — rendering narration only"
CROSSFADE_SECONDS = 3.0
_STOPWORDS = {"and", "or", "of", "the", "a", "an", "to", "with", "in", "on", "for", "its"}


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depends on the machine
        raise RuntimeError("ffmpeg was not found on PATH or via imageio-ffmpeg") from exc


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def media_duration(path: str) -> float:
    """Duration in seconds from ffmpeg's stream header."""
    result = _run(["-i", str(path)])
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError(f"could not read the duration of {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


# ══════════════════════════════════════════════════════════════
#  LIBRARY AND TRACK SELECTION
# ══════════════════════════════════════════════════════════════

def _stem(word: str) -> str:
    for suffix in ("ness", "ment", "ing", "ed", "es", "s", "y"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


# Episode vocabulary and library mood labels (including the Portuguese labels
# used in the library's file names) mapped onto a few canonical moods.
MOOD_SYNONYMS = {
    "dark": {"dark", "sombrio", "sombria", "somber", "sombre", "dread", "unease", "menace", "ominous", "sinister",
             "envy", "obsession", "paranoia", "fear", "control", "cold", "claustrophobia",
             "claustrophobic", "chilling", "hollow", "suffocating", "isolation"},
    "melancholy": {"melancholy", "melancholic", "melancolico", "melancolica", "sad", "sadness",
                   "grief", "regret", "loneliness", "lonely", "sorrow", "loss", "longing",
                   "mortality", "memory", "pity", "vulnerability", "estrangement"},
    "dramatic": {"dramatic", "dramatico", "tension", "confrontation", "betrayal", "reversal",
                 "conflict", "ambition", "status", "rivalry", "resentment", "humiliation"},
}


def _fold(text: str) -> str:
    import unicodedata
    return "".join(char for char in unicodedata.normalize("NFKD", str(text or "").lower())
                   if not unicodedata.combining(char))


def mood_terms(*texts: str) -> set[str]:
    words = {word for text in texts for word in re.findall(r"[a-z]+", _fold(text))
             if len(word) > 2 and word not in _STOPWORDS}
    terms = {_stem(word) for word in words}
    for mood, synonyms in MOOD_SYNONYMS.items():
        if words & synonyms or terms & {_stem(item) for item in synonyms}:
            terms.add(mood)
    return terms


def filename_moods(name: str) -> list[str]:
    """'Title - Artist - Genre - Mood.mp3' (YouTube Audio Library style) -> ['mood']."""
    parts = [part.strip() for part in Path(name).stem.split(" - ")]
    return [_fold(parts[-1])] if len(parts) >= 3 and parts[-1] else []


def library_tracks(library_dir: str) -> list[dict]:
    """Every audio file in the library, merged with tracks.json metadata when present."""
    root = Path(library_dir or "")
    if not root.is_dir():
        return []
    metadata = {}
    meta_path = root / METADATA_FILE
    if meta_path.is_file():
        try:
            entries = json.loads(meta_path.read_text(encoding="utf-8"))
            # Accept a bare list or {"tracks": [...]}, and "file"/"filename" keys.
            if isinstance(entries, dict):
                entries = entries.get("tracks", [])
            for entry in entries if isinstance(entries, list) else []:
                name = entry.get("file") or entry.get("filename") if isinstance(entry, dict) else None
                if name:
                    metadata[unicodedata.normalize("NFC", str(name))] = entry
        except (OSError, ValueError) as exc:
            print(f"   ⚠ Could not read {meta_path}: {exc.__class__.__name__}")
    tracks = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
            entry = metadata.get(unicodedata.normalize("NFC", path.name), {})
            moods = entry.get("moods", entry.get("mood", []))
            moods = [moods] if isinstance(moods, str) else list(moods or [])
            tracks.append({
                "file": path.name, "path": str(path),
                "moods": [str(mood).lower() for mood in moods] or filename_moods(path.name),
                "license": str(entry.get("license", "") or ""),
                "source_url": str(entry.get("source_url", entry.get("url", "")) or ""),
                "attribution": str(entry.get("attribution", "") or ""),
            })
    return tracks


def choose_track(tracks: list[dict], episode: dict | None = None) -> dict | None:
    """Best mood overlap with the episode; ties broken deterministically per episode."""
    if not tracks:
        return None
    episode = episode or {}
    wanted = mood_terms(episode.get("psychological_theme", ""),
                        episode.get("intended_emotional_experience", ""))
    salt = str(episode.get("title") or episode.get("video_title") or "")

    def score(track):
        overlap = len(wanted & mood_terms(*track["moods"]))
        tiebreak = hashlib.sha256(f"{salt}|{track['file']}".encode("utf-8")).hexdigest()
        return (-overlap, tiebreak)

    return sorted(tracks, key=score)[0]


# ══════════════════════════════════════════════════════════════
#  FILTER GRAPH AND LOUDNESS
# ══════════════════════════════════════════════════════════════

PAUSE_KEY_LEVEL = 0.085    # amplitude of the key tone: -24.6 dB RMS, ~5 dB of reduction at threshold 0.03, ratio 8


def music_filter(duration: float, track_duration: float, volume: float) -> tuple[str, int]:
    """ffmpeg filter_complex: looped/crossfaded bed, fades, ducking, mix.

    Input 0 is the narration, input 1 the music track. Returns (graph, loops).
    """
    loops = 1
    if track_duration > 0 and track_duration < duration:
        usable = max(track_duration - CROSSFADE_SECONDS, 1.0)
        loops = int((duration - track_duration) // usable) + 2
    parts = []
    if loops > 1:
        labels = "".join(f"[m{i}]" for i in range(loops))
        parts.append(f"[1:a]asplit={loops}{labels}")
        previous = "m0"
        for index in range(1, loops):
            out = f"x{index}"
            parts.append(f"[{previous}][m{index}]acrossfade=d={CROSSFADE_SECONDS}:c1=tri:c2=tri[{out}]")
            previous = out
        source = f"[{previous}]"
    else:
        source = "[1:a]"
    fade_in = min(3.0, duration * 0.05)
    fade_out = min(4.0, duration * 0.08)
    parts.append(
        f"{source}atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume={volume},"
        f"afade=t=in:st=0:d={fade_in:.2f},afade=t=out:st={max(0.0, duration - fade_out):.2f}:"
        f"d={fade_out:.2f}[bed]"
    )
    ratio = float(getattr(config, "MUSIC_DUCKING_RATIO", 8))
    threshold = float(getattr(config, "MUSIC_DUCKING_THRESHOLD", 0.03))
    parts.append("[0:a]asplit=2[narr][speech]")
    # Phase 3.9.4-G: in a pause the music comes up about 4 dB and no further, like a breath, not a dropout: a
    # low steady tone in the key alone keeps the compressor partly closed. Speech is far above it.
    parts.append(f"sine=frequency=110:sample_rate=48000:duration={duration:.3f},volume={PAUSE_KEY_LEVEL}[tone]")
    parts.append("[speech][tone]amix=inputs=2:duration=first:normalize=0[key]")
    parts.append(f"[bed][key]sidechaincompress=threshold={threshold}:ratio={ratio}:"
                 f"attack=20:release=900[ducked]")
    parts.append("[narr][ducked]amix=inputs=2:duration=first:normalize=0[mix]")
    return ";".join(parts), loops


def _loudnorm_filter(measured: dict | None = None, peak: float | None = None) -> str:
    target = float(getattr(config, "LOUDNESS_TARGET_LUFS", -14.0))
    peak = float(getattr(config, "LOUDNESS_TRUE_PEAK", -1.5) if peak is None else peak)
    base = f"loudnorm=I={target}:TP={peak}:LRA=11"
    if not measured:
        return base + ":print_format=json"
    return (base + f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true:print_format=json")


def _loudnorm_json(stderr: str) -> dict:
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not blocks:
        raise RuntimeError("ffmpeg loudnorm produced no measurement")
    return json.loads(blocks[-1])


def measure_loudness(path: str) -> dict:
    """Integrated loudness (LUFS) and true peak (dBTP) of an existing file."""
    data = _loudnorm_json(_run(["-i", str(path), "-af", _loudnorm_filter(), "-f", "null", "-"]).stderr)
    return {"integrated_lufs": float(data["input_i"]), "true_peak_dbtp": float(data["input_tp"])}


def normalize_loudness(source: str, destination: str) -> dict:
    """Two-pass loudnorm to the configured target; returns before/after measurements."""
    first = _loudnorm_json(_run(["-i", source, "-af", _loudnorm_filter(), "-f", "null", "-"]).stderr)
    result = _run(["-y", "-i", source, "-af", _loudnorm_filter(first), "-ar", "44100",
                   "-c:a", "libmp3lame", "-b:a", "192k", destination])
    if result.returncode != 0:
        raise RuntimeError(f"loudnorm pass 2 failed: {result.stderr[-300:]}")
    second = _loudnorm_json(result.stderr)
    return {
        "input_integrated_lufs": float(first["input_i"]),
        "input_true_peak_dbtp": float(first["input_tp"]),
        "output_integrated_lufs": float(second.get("output_i", second["input_i"])),
        "output_true_peak_dbtp": float(second.get("output_tp", second["input_tp"])),
        "target_lufs": float(getattr(config, "LOUDNESS_TARGET_LUFS", -14.0)),
    }


def limit_final_true_peak(video_path: str) -> dict:
    """Measure the true peak on the FINAL MP4; above FINAL_TRUE_PEAK_MAX (-1.0 dBTP) the audio
    is limited again at LOUDNESS_TRUE_PEAK (-1.5 dBTP) and remuxed (the video is copied).

    The mix may meet its target and the AAC encode of the render still push the peak up,
    which is why the check runs on the delivered file.
    """
    limit = float(getattr(config, "FINAL_TRUE_PEAK_MAX", -1.0))
    peak = float(getattr(config, "LOUDNESS_TRUE_PEAK", -1.5))
    before = measure_loudness(video_path)
    if before["true_peak_dbtp"] <= limit:
        return {"action": "none", **before}
    first = _loudnorm_json(_run(["-i", str(video_path), "-vn", "-af", _loudnorm_filter(None, peak),
                                 "-f", "null", "-"]).stderr)
    temporary = str(Path(video_path).with_suffix(".limited.mp4"))
    result = _run(["-y", "-i", str(video_path), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                   "-af", _loudnorm_filter(first, peak), "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
                   "-movflags", "+faststart", temporary])
    if result.returncode != 0:
        Path(temporary).unlink(missing_ok=True)
        raise RuntimeError(f"true-peak remux failed: {result.stderr[-300:]}")
    os.replace(temporary, video_path)
    after = measure_loudness(video_path)
    return {"action": "limited_and_remuxed", "before_true_peak_dbtp": before["true_peak_dbtp"],
            **after}


def _update_manifest(output_dir: str, music: dict) -> None:
    path = Path(output_dir) / "asset_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        manifest = {}
    manifest["music"] = music
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def music_attribution(output_dir: str) -> str:
    """Attribution text required by the chosen track's license, if any."""
    try:
        manifest = json.loads((Path(output_dir) / "asset_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    music = manifest.get("music") or {}
    return str(music.get("attribution") or "").strip() if music.get("track") else ""


# ══════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════

def prepare_final_audio(narration_path: str, output_path: str, *, episode: dict | None = None,
                        music_enabled: bool | None = None, music_volume: float | None = None,
                        library_dir: str | None = None, output_dir: str | None = None) -> str:
    """Mix music (if any) under the narration, then loudness-normalise the result.

    Always returns a playable file: on any ffmpeg failure the narration is
    copied unchanged and the reason is logged.
    """
    enabled = getattr(config, "MUSIC_ENABLED", True) if music_enabled is None else music_enabled
    volume = getattr(config, "MUSIC_VOLUME", 0.12) if music_volume is None else music_volume
    library = library_dir if library_dir is not None else getattr(config, "MUSIC_LIBRARY_DIR", "")
    output_dir = output_dir or os.path.dirname(output_path) or "."
    record = {"track": None, "loudness": None}
    try:
        with tempfile.TemporaryDirectory(prefix="private_gravity_mix_") as temp:
            mixed = narration_path
            track = choose_track(library_tracks(library), episode) if enabled else None
            if enabled and track is None:
                print(f"   ⚠ {NO_MUSIC_MESSAGE} (library: {library})")
            if track:
                duration = media_duration(narration_path)
                graph, loops = music_filter(duration, media_duration(track["path"]), volume)
                mixed = os.path.join(temp, "mix.wav")
                result = _run(["-y", "-i", narration_path, "-i", track["path"],
                               "-filter_complex", graph, "-map", "[mix]", mixed])
                if result.returncode != 0:
                    raise RuntimeError(f"music mix failed: {result.stderr[-300:]}")
                record.update({
                    "track": track["file"], "moods": track["moods"], "license": track["license"],
                    "source_url": track["source_url"], "attribution": track["attribution"],
                    "volume": volume, "loops": loops,
                    "ducking_ratio": float(getattr(config, "MUSIC_DUCKING_RATIO", 8)),
                })
                if not track["license"]:
                    print(f"   ⚠ {track['file']} has no license in {METADATA_FILE}; "
                          "record it before publishing")
                print(f"   → Music: {track['file']} (moods {', '.join(track['moods']) or 'none'}; "
                      f"{loops} pass(es), ducked under narration)")
            loudness = normalize_loudness(mixed, output_path)
            record["loudness"] = loudness
            print(f"   → Loudness: {loudness['input_integrated_lufs']:.1f} → "
                  f"{loudness['output_integrated_lufs']:.1f} LUFS, true peak "
                  f"{loudness['output_true_peak_dbtp']:.1f} dBTP")
    except Exception as exc:
        print(f"   ⚠ Final audio processing failed ({exc}); using the narration unchanged")
        shutil.copy2(narration_path, output_path)
        record["error"] = str(exc)
    _update_manifest(output_dir, record)
    return output_path


def mix_music_with_narration(narration_path: str,
                              output_path: str,
                              music_volume: float = 0.12,
                              library_dir: str = "",
                              episode: dict | None = None) -> str:
    """Backwards-compatible entry point used by the GUI."""
    return prepare_final_audio(narration_path, output_path, episode=episode,
                               music_volume=music_volume, library_dir=library_dir)
