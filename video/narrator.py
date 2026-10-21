"""
Narrator — Microsoft Edge TTS (100% free)
Generates broadcast-quality voiceover + word-timed subtitle (SRT) file.

- Requests WordBoundary events (edge-tts >= 7 sends only SentenceBoundary
  unless Communicate(..., boundary="WordBoundary") is passed).
- Each scene is synthesised separately with bounded retries and cached by
  hash(text, voice, rate, pitch, edge-tts version): re-running narration only
  re-synthesises scenes whose text changed.
- Measured delivery (words per minute) is recorded for planning.
Uses MoviePy's AudioFileClip for duration — guaranteed to match the video render.
"""
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import shutil
import tempfile
import time

import edge_tts
import config
from moviepy.editor import AudioFileClip, concatenate_audioclips
from agents.narrative import assert_no_mojibake
from agents.voice_calibration import record as record_calibration
from video.captions import align_words, build_cues, proportional_words
from video.timeline import write_shifted_srt, write_timeline

# Voices known to emit WordBoundary events (needed for word-synced captions).
# scripts/render_preflight.py reads this literal without importing the module.
WORD_SYNCED_VOICES = [
    ("en-US-AndrewNeural", "Andrew (US, Warm male)"),
    ("en-GB-LibbyNeural",  "Libby  (UK, Clear female)"),
]
RETRY_DELAYS = (2, 5, 10)      # seconds before attempts 2, 3 and 4
_sleep = time.sleep
_upgrade_hint_shown = False


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _format_srt_time(ms: float) -> str:
    """Convert milliseconds → SRT timestamp  HH:MM:SS,mmm"""
    ms = max(0, int(ms))
    h  = ms // 3_600_000;  ms %= 3_600_000
    m  = ms // 60_000;     ms %= 60_000
    s  = ms // 1_000;      ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _get_audio_duration_ms(audio_path: str) -> float:
    """
    Return the exact audio duration in milliseconds using MoviePy's
    AudioFileClip — the same engine the video renderer uses, so the number
    is guaranteed to match what creator.py reports.

    Falls back to a rough file-size estimate only if MoviePy itself fails.
    """
    try:
        clip = AudioFileClip(audio_path)
        dur  = clip.duration          # seconds, same value creator.py gets
        clip.close()
        return dur * 1000
    except Exception as e:
        print(f"   ⚠ AudioFileClip duration failed ({e}); using file-size estimate")

    # Rough fallback: 128 kbps assumption
    try:
        return (os.path.getsize(audio_path) / (128 * 1024 / 8)) * 1000
    except Exception:
        return 60_000


def edge_tts_version() -> str:
    try:
        return importlib.metadata.version("edge-tts")
    except importlib.metadata.PackageNotFoundError:
        return getattr(edge_tts, "__version__", "unknown")


def supports_word_boundary() -> bool:
    """edge-tts >= 7 exposes Communicate(boundary=...); older versions always send words."""
    try:
        return "boundary" in inspect.signature(edge_tts.Communicate.__init__).parameters
    except (TypeError, ValueError):
        return False


# ══════════════════════════════════════════════════════════════
#  SRT WRITER
# ══════════════════════════════════════════════════════════════

def _write_srt_blocks(blocks: list[tuple[float, float, str]], srt_path: str) -> int:
    """
    Write a list of (start_ms, end_ms, text) tuples as a valid SRT file.
    Returns the number of cues written.
    """
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, (start_ms, end_ms, text) in enumerate(blocks, 1):
            f.write(f"{idx}\n")
            f.write(f"{_format_srt_time(start_ms)} --> {_format_srt_time(end_ms)}\n")
            f.write(f"{text}\n")
            f.write("\n")
    return len(blocks)


def scene_cues(text: str, events: list[dict], duration_s: float) -> list[tuple]:
    """Word-timed cues when events exist, else character-proportional timing."""
    words = align_words(text, events) if events else proportional_words(text, duration_s)
    return build_cues(words, end_limit=duration_s)


# ══════════════════════════════════════════════════════════════
#  TTS SYNTHESIS
# ══════════════════════════════════════════════════════════════

def _communicate(text: str):
    kwargs = {"rate": config.VOICE_RATE, "pitch": config.VOICE_PITCH}
    if supports_word_boundary():
        kwargs["boundary"] = "WordBoundary"
    return edge_tts.Communicate(text, config.VOICE_ID, **kwargs)


async def _synthesise(text: str, audio_path: str) -> list[dict]:
    """Stream one scene's audio to ``audio_path``; return WordBoundary events (ms)."""
    audio_chunks = []
    events = []
    async for event in _communicate(text).stream():
        if event["type"] == "audio":
            audio_chunks.append(event["data"])
        elif event["type"] == "WordBoundary":
            offset_ms = event["offset"] / 10_000          # 100-ns ticks → ms
            events.append({
                "text": event["text"],
                "start_ms": offset_ms,
                "end_ms": offset_ms + event["duration"] / 10_000,
            })
    if not audio_chunks:
        raise edge_tts.exceptions.NoAudioReceived("No audio was received for this scene.")
    with open(audio_path, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)
    return events


def _http_status(exc) -> int | None:
    for name in ("status", "status_code", "code"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    match = re.search(r"\b(403|5\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def _retryable(exc) -> bool:
    exceptions = getattr(edge_tts, "exceptions", None)
    edge_errors = tuple(
        getattr(exceptions, name) for name in
        ("NoAudioReceived", "WebSocketError", "UnexpectedResponse", "UnknownResponse")
        if exceptions is not None and hasattr(exceptions, name)
    )
    status = _http_status(exc)
    return (isinstance(exc, edge_errors + (asyncio.TimeoutError, ConnectionError))
            or status == 403 or (status is not None and 500 <= status < 600)
            or exc.__class__.__module__.startswith("aiohttp"))


def synthesise_scene(text: str, audio_path: str, label: str = "") -> list[dict]:
    """Synthesise with up to len(RETRY_DELAYS) retries on transient Edge TTS failures."""
    global _upgrade_hint_shown
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return asyncio.run(_synthesise(text, audio_path))
        except Exception as exc:
            if not _retryable(exc) or attempt == len(RETRY_DELAYS):
                raise
            if _http_status(exc) == 403 and not _upgrade_hint_shown:
                _upgrade_hint_shown = True
                print(f"   ⚠ Edge TTS answered 403. Outdated clients are rejected: "
                      f"pip install -U edge-tts (installed {edge_tts_version()}).")
            delay = RETRY_DELAYS[attempt]
            print(f"   ⚠ {label or 'scene'}: {exc.__class__.__name__}; retry "
                  f"{attempt + 1}/{len(RETRY_DELAYS)} in {delay}s")
            _sleep(delay)
    raise RuntimeError("unreachable")


def cache_key(text: str) -> str:
    payload = json.dumps([text, config.VOICE_ID, config.VOICE_RATE, config.VOICE_PITCH,
                          edge_tts_version(), supports_word_boundary()], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_scene(text: str, cache_dir: str, scene_audio: str, label: str) -> tuple[list, bool]:
    """Copy a cached synthesis into ``scene_audio`` or synthesise and cache it."""
    key = cache_key(text)
    cached_audio = os.path.join(cache_dir, f"{key}.mp3")
    cached_events = os.path.join(cache_dir, f"{key}.json")
    if os.path.isfile(cached_audio) and os.path.isfile(cached_events):
        try:
            with open(cached_events, encoding="utf-8") as f:
                events = json.load(f)
            shutil.copyfile(cached_audio, scene_audio)
            return events, True
        except (OSError, ValueError):
            pass
    events = synthesise_scene(text, scene_audio, label)
    os.makedirs(cache_dir, exist_ok=True)
    shutil.copyfile(scene_audio, cached_audio)
    with open(cached_events, "w", encoding="utf-8") as f:
        json.dump(events, f)
    return events, False


# ══════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════

def generate_narration(script: dict, output_dir: str) -> str:
    """
    Synthesises each scene independently (cached), then concatenates without
    inserting silence. Exact scene durations drive both the SRT offsets and
    video timeline. Returns path to the saved audio file.
    """
    assert_no_mojibake(script, "speech synthesis")
    os.makedirs(output_dir, exist_ok=True)
    sections = [section for section in script["sections"]
                if section.get("narration", "").strip()]
    if not sections:
        raise ValueError("Script contains no narratable scenes")
    audio_path = os.path.join(output_dir, "narration.mp3")
    srt_path   = os.path.join(output_dir, "narration.srt")
    cache_dir  = os.path.join(output_dir, ".tts_cache")
    clips = []
    durations = []
    scene_srts = []
    cursor = 0.0
    scenes_without_words = []
    with tempfile.TemporaryDirectory(prefix="private_gravity_tts_") as temp_dir:
        try:
            for index, section in enumerate(sections, 1):
                text = section["narration"].strip()
                scene_audio = os.path.join(temp_dir, f"scene_{index:03d}.mp3")
                scene_srt = os.path.join(temp_dir, f"scene_{index:03d}.srt")
                label = f"scene {index}/{len(sections)}"
                events, hit = cached_scene(text, cache_dir, scene_audio, label)
                print(f"   → {'Reused' if hit else 'Synthesised'} {label} "
                      f"({len(text.split())} words, {len(events)} word timings)")
                clip = AudioFileClip(scene_audio)
                duration = float(clip.duration)
                clips.append(clip)
                if not events:
                    scenes_without_words.append(index)
                _write_srt_blocks(scene_cues(text, events, duration), scene_srt)
                durations.append(duration)
                scene_srts.append((scene_srt, cursor))
                cursor += duration
            combined = concatenate_audioclips(clips)
            combined.write_audiofile(audio_path, codec="libmp3lame", verbose=False, logger=None)
            combined.close()
            write_shifted_srt(srt_path, scene_srts)
        finally:
            for clip in clips:
                clip.close()

    if scenes_without_words:
        print(f"   ⚠ No WordBoundary events for scene(s) {scenes_without_words} "
              f"(edge-tts {edge_tts_version()}; word timings need "
              f"Communicate(boundary=\"WordBoundary\"), supported: {supports_word_boundary()}). "
              "Those captions use proportional timing.")

    # The concatenated file can differ by a few codec milliseconds. Pin the
    # last scene to the exact duration MoviePy will later attach to the video.
    rendered_duration = _get_audio_duration_ms(audio_path) / 1000
    durations[-1] += rendered_duration - sum(durations)
    write_timeline(os.path.join(output_dir, "scene_timeline.json"), sections, durations)

    words = sum(len(section["narration"].split()) for section in sections)
    entry = record_calibration(words, rendered_duration)
    if entry:
        print(f"   → Measured delivery: {entry['wpm']} wpm for {config.VOICE_ID} "
              f"{config.VOICE_RATE} (saved for planning)")

    # ── Post-run report ──────────────────────────────────────────────────────
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    timestamps = re.findall(r"--> (\d+):(\d+):(\d+),(\d+)", content)
    cap_end_s = max((int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
                     for h, m, s, ms in timestamps), default=0.0)
    coverage = (cap_end_s / rendered_duration * 100) if rendered_duration > 0 else 0
    print(f"   → Audio : {os.path.getsize(audio_path) // 1024} KB  |  {rendered_duration:.1f}s")
    print(f"   → SRT   : {content.count(' --> ')} cues  |  covers 0s → {cap_end_s:.1f}s "
          f"({coverage:.0f}%)")
    if coverage < 85:
        print(f"   ⚠ Caption coverage is {coverage:.0f}%; check the scenes listed above.")
    return audio_path
