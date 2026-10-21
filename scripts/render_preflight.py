"""Offline render-readiness check for output/script.json.

Run:  python -m scripts.render_preflight [--script output/script.json]

No TTS, no downloads, no rendering and no network access. Reports estimated
narration timing per scene, image coverage under the renderer's current
pacing (one image per stock query, cycling every BROLL_INTERVAL seconds),
the toolchain (moviepy 1.0.3, numpy < 2, ffmpeg, fonts, music, disk space),
Edge TTS and the configured voice, and whether stock keys are configured
(presence only, never values). Exits non-zero when any check FAILs.
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import importlib.util
import math
import re
import shutil
import sys
from pathlib import Path

import config
from agents import voice_calibration

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
DEFAULT_LONG_IMAGE_SECONDS = 20.0
IMAGES_PER_SCENE = 3          # video/stock.py keeps at most 3 queries per regular scene
MIN_FREE_BYTES_WARN = 2 * 1024 ** 3
MIN_FREE_BYTES_FAIL = 500 * 1024 ** 2
_FALLBACK_WORD_SYNCED = ("en-US-AndrewNeural", "en-GB-LibbyNeural")
ROOT = Path(__file__).resolve().parent.parent


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def importable(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def word_synced_voices() -> tuple[str, ...]:
    """Read WORD_SYNCED_VOICES from video/narrator.py without importing it."""
    try:
        tree = ast.parse((ROOT / "video" / "narrator.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(target, "id", "") == "WORD_SYNCED_VOICES" for target in node.targets):
                return tuple(voice for voice, _ in ast.literal_eval(node.value))
    except (OSError, SyntaxError, ValueError):
        pass
    return _FALLBACK_WORD_SYNCED


def rate_factor(voice_rate: str) -> float:
    """Edge TTS rate such as "-5%" -> 0.95."""
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", str(voice_rate or ""))
    return 1 + float(match.group(1)) / 100 if match else 1.0


def scene_rows(script: dict, *, wpm: float, voice_rate: str, interval: float,
               long_image_seconds: float) -> list[dict]:
    """Estimated duration and image coverage per scene (renderer pacing unchanged)."""
    effective_wpm = max(1.0, wpm * rate_factor(voice_rate))
    rows = []
    for scene in script.get("sections", []):
        words = len(str(scene.get("narration", "")).split())
        seconds = words / effective_wpm * 60
        queries = [scene.get(key) for key in ("image_query", "image_query_2", "image_query_3")]
        images = min(IMAGES_PER_SCENE, len([query for query in queries if str(query or "").strip()]))
        slots = max(1, math.ceil(seconds / interval))
        per_image = seconds / images if images else seconds
        status = PASS
        if not images:
            status = FAIL
        elif per_image > long_image_seconds:
            status = WARN
        rows.append({
            "scene_id": scene.get("id"), "words": words, "seconds": round(seconds, 1),
            "images": images, "slots": slots, "seconds_per_image": round(per_image, 1),
            "status": status,
        })
    return rows


def environment_checks(output_dir: Path) -> list[tuple[str, str, str]]:
    checks = []
    moviepy = package_version("moviepy")
    checks.append(("moviepy", PASS if moviepy == "1.0.3" else FAIL,
                   f"installed {moviepy or 'missing'}; 1.0.3 required"))
    numpy = package_version("numpy")
    major = int(numpy.split(".")[0]) if numpy and numpy.split(".")[0].isdigit() else None
    checks.append(("numpy", PASS if major is not None and major < 2 else FAIL,
                   f"installed {numpy or 'missing'}; < 2 required"))
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        checks.append(("ffmpeg", PASS, f"on PATH: {ffmpeg}"))
    elif importable("imageio_ffmpeg"):
        checks.append(("ffmpeg", WARN, "not on PATH; moviepy will use the imageio-ffmpeg binary"))
    else:
        checks.append(("ffmpeg", FAIL, "not on PATH and imageio-ffmpeg is not installed"))
    font_paths = getattr(config, "FONT_PATHS", {}) or {}
    missing_variants = [variant for variant, paths in font_paths.items()
                        if not any(Path(path).is_file() for path in paths)]
    checks.append(("fonts", WARN if missing_variants else PASS,
                   "no font file for: " + ", ".join(missing_variants) + " (default font fallback)"
                   if missing_variants else f"{len(font_paths)} variants available"))
    if getattr(config, "MUSIC_ENABLED", False):
        library = Path(getattr(config, "MUSIC_LIBRARY_DIR", "music library"))
        tracks = [path for path in library.glob("*") if path.suffix.lower() in (".mp3", ".wav")] \
            if library.is_dir() else []
        checks.append(("music", PASS if tracks else WARN,
                       f"{len(tracks)} track(s) in {library}" if tracks
                       else f"MUSIC_ENABLED but no .mp3/.wav in {library}; video will have no music"))
    else:
        checks.append(("music", PASS, "music disabled"))
    target = output_dir if output_dir.exists() else output_dir.parent
    free = shutil.disk_usage(target).free
    status = FAIL if free < MIN_FREE_BYTES_FAIL else WARN if free < MIN_FREE_BYTES_WARN else PASS
    checks.append(("disk space", status, f"{free / 1024 ** 3:.1f} GB free in {target}"))
    edge = importable("edge_tts")
    checks.append(("edge-tts", PASS if edge else FAIL,
                   f"version {package_version('edge-tts')}" if edge else "not importable"))
    voice = str(getattr(config, "VOICE_ID", ""))
    synced = word_synced_voices()
    checks.append(("voice", PASS if voice in synced else WARN,
                   f"{voice} is in the word-timing list" if voice in synced
                   else f"{voice} is not in the word-timing list {list(synced)}; "
                        "captions may fall back to sentence timing"))
    pixabay = str(getattr(config, "PIXABAY_API_KEY", "") or "").strip()
    has_pixabay = bool(pixabay) and not pixabay.upper().startswith("YOUR_")
    provider = str(getattr(config, "STOCK_PROVIDER", "auto"))
    checks.append(("stock keys", PASS if has_pixabay else WARN,
                   f"Pixabay key configured; provider={provider}; Openverse needs no key"
                   if has_pixabay else
                   f"Pixabay key not configured; provider={provider}; only Openverse (CC0/PDM) will be used"))
    return checks


def estimate_wpm() -> tuple[float, str, str]:
    """(effective wpm, VOICE_RATE to apply, description).

    A calibrated value is measured delivery at the configured rate, so the rate
    is not applied twice.
    """
    calibrated = voice_calibration.calibrated_wpm()
    configured = voice_calibration.configured_wpm()
    rate = getattr(config, "VOICE_RATE", "+0%")
    if calibrated:
        return calibrated, "+0%", (f"{calibrated} wpm measured for {getattr(config, 'VOICE_ID', '')} "
                                   f"{rate} (configured {configured:g})")
    return configured, rate, f"{configured:g} wpm configured x VOICE_RATE {rate} (not calibrated)"


def live_tts_check() -> tuple[str, str, str]:
    """Synthesise one short sentence and confirm audio and WordBoundary events."""
    import os
    import tempfile
    try:
        from video import narrator
    except Exception as exc:          # moviepy/edge-tts missing
        return ("live TTS", FAIL, f"cannot import the narrator: {exc.__class__.__name__}")
    with tempfile.TemporaryDirectory() as temp:
        path = os.path.join(temp, "probe.mp3")
        try:
            events = narrator.synthesise_scene(
                "The quiet house was waiting for him.", path, "live probe")
        except Exception as exc:
            return ("live TTS", FAIL, f"{exc.__class__.__name__}: {exc}")
        size = os.path.getsize(path) if os.path.exists(path) else 0
    if not size:
        return ("live TTS", FAIL, "no audio received")
    if not events:
        return ("live TTS", WARN, f"audio OK ({size} bytes) but no WordBoundary events; "
                                  f"captions will use proportional timing")
    return ("live TTS", PASS, f"audio OK ({size} bytes), {len(events)} word timings")


def run(script_path: Path, *, long_image_seconds: float = DEFAULT_LONG_IMAGE_SECONDS,
        live: bool = False) -> int:
    import json

    rows, checks = [], []
    wpm, rate, wpm_note = estimate_wpm()
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
        rows = scene_rows(
            script, wpm=wpm,
            voice_rate=rate,
            interval=float(getattr(config, "BROLL_INTERVAL", 10.0)),
            long_image_seconds=long_image_seconds,
        )
        checks.append(("script", PASS if rows else FAIL,
                       f"{script.get('title', '')!r}: {len(rows)} scenes"))
    except (OSError, ValueError) as exc:
        checks.append(("script", FAIL, f"cannot read {script_path}: {exc.__class__.__name__}"))
    checks += environment_checks(script_path.parent)
    checks.append(("narration wpm", PASS, wpm_note))
    if live:
        checks.append(live_tts_check())

    if rows:
        effective = wpm * rate_factor(rate)
        print(f"Narration estimate: {wpm_note} = {effective:.1f} wpm; images cycle every "
              f"{getattr(config, 'BROLL_INTERVAL', 10.0):g}s; flag > {long_image_seconds:g}s per image\n")
        print(f"  {'scene':>5}  {'words':>5}  {'est_s':>6}  {'images':>6}  {'slots':>5}  "
              f"{'s/image':>7}  status")
        for row in rows:
            print(f"  {row['scene_id']!s:>5}  {row['words']:>5}  {row['seconds']:>6.1f}  "
                  f"{row['images']:>6}  {row['slots']:>5}  {row['seconds_per_image']:>7.1f}  "
                  f"{row['status']}")
        total = sum(row["seconds"] for row in rows)
        print(f"  total: {sum(row['words'] for row in rows)} words, {total:.0f}s "
              f"({total / 60:.1f} min)\n")
        long_scenes = [str(row["scene_id"]) for row in rows if row["status"] == WARN]
        empty = [str(row["scene_id"]) for row in rows if row["status"] == FAIL]
        checks.append(("image pacing", FAIL if empty else WARN if long_scenes else PASS,
                       f"no image queries: {', '.join(empty)}" if empty else
                       f"one image stays > {long_image_seconds:g}s in scenes {', '.join(long_scenes)}"
                       if long_scenes else "every image stays on screen within the threshold"))

    width = max(len(name) for name, _, _ in checks)
    for name, status, detail in checks:
        print(f"  [{status}] {name:<{width}}  {detail}")
    failed = [name for name, status, _ in checks if status == FAIL]
    print(f"\nResult: {'FAIL (' + ', '.join(failed) + ')' if failed else 'ready to render'}")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline render-readiness check.")
    parser.add_argument("--script", default=str(Path(config.OUTPUT_DIR) / "script.json"))
    parser.add_argument("--long-image-seconds", type=float, default=DEFAULT_LONG_IMAGE_SECONDS)
    parser.add_argument("--live", action="store_true",
                        help="Also synthesise one short sentence with Edge TTS (network).")
    args = parser.parse_args(argv)
    return run(Path(args.script), long_image_seconds=args.long_image_seconds, live=args.live)


if __name__ == "__main__":
    sys.exit(main())
