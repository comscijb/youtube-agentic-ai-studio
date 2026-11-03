"""
Narrator — the channel voice through a pluggable TTS backend (audio/tts, Phase 3.9-A).

- The backend is ``TTS_BACKEND`` (edge | local | elevenlabs) with the voice ``TTS_VOICE``; Edge TTS
  remains the default. Word timings come from the backend (Edge WordBoundary, ElevenLabs character
  timings) or from forced alignment against the script (local models).
- The shared pronunciation list (data/pronunciations.json) is applied for every backend.
- Backends that take delivery hints get the scene split into narration and quoted dialogue (quieter
  narration where the scene's emotional tone says so); the others read the scene in one piece.
- Each scene is synthesised separately and cached by hash(text, backend, voice, rate, pitch, engine
  version, pronunciations): re-running narration only re-synthesises scenes whose text changed.
- Measured delivery (words per minute) is recorded per backend + voice for planning, and the voice is
  stored with the narration (a channel-voice change marks it stale).
Uses MoviePy's AudioFileClip for duration — guaranteed to match the video render.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile

import config
from moviepy.editor import AudioFileClip, concatenate_audioclips
from agents.narrative import assert_no_mojibake
from agents.voice_calibration import record as record_calibration
from audio import heard_thoughts, tts
from audio.tts import delivery, pronunciations
from audio.tts.edge import edge_tts_version, supports_word_boundary  # noqa: F401  (render_preflight, tests)
from video.captions import align_words, build_cues, mark_thoughts, proportional_words
from video.timeline import write_shifted_srt, write_timeline

# Voices known to emit WordBoundary events (needed for word-synced captions).
# scripts/render_preflight.py reads this literal without importing the module.
WORD_SYNCED_VOICES = [
    ("en-US-AndrewNeural", "Andrew (US, Warm male)"),
    ("en-GB-LibbyNeural",  "Libby  (UK, Clear female)"),
]


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


def scene_words(text: str, events: list[dict], duration_s: float, thoughts: bool = False) -> list[dict]:
    """Word timings from the events, else character-proportional; heard thoughts flagged when on."""
    words = align_words(text, events) if events else proportional_words(text, duration_s)
    return mark_thoughts(text, words) if thoughts else words


def scene_cues(text: str, events: list[dict], duration_s: float, thoughts: bool = False) -> list[tuple]:
    """Word-timed cues when events exist, else character-proportional timing."""
    return build_cues(scene_words(text, events, duration_s, thoughts), end_limit=duration_s)


# ══════════════════════════════════════════════════════════════
#  TTS SYNTHESIS
# ══════════════════════════════════════════════════════════════

def _concat(parts: list[str], out_path: str) -> None:
    """Join per-line audio pieces into one scene file (no inserted silence)."""
    clips = [AudioFileClip(part) for part in parts]
    try:
        joined = concatenate_audioclips(clips)
        joined.write_audiofile(out_path, codec="libmp3lame", verbose=False, logger=None)
        joined.close()
    finally:
        for clip in clips:
            clip.close()


def synthesise_scene(text: str, audio_path: str, label: str = "", section: dict | None = None) -> list[dict]:
    """One scene in the channel voice; returns word events (ms) for ``text``."""
    backend = tts.get()
    voice = tts.channel_voice(backend.name)
    entries = pronunciations.load()
    common = {"voice": voice, "rate": config.VOICE_RATE, "pitch": config.VOICE_PITCH, "label": label}
    pieces = delivery.lines(text, section) if backend.supports_style else [(text, None)]
    if len(pieces) == 1:
        spoken = pronunciations.apply(pieces[0][0], backend.name, entries)
        return backend.synthesize(spoken, audio_path, style=pieces[0][1], **common)["words"]
    events, parts, offset = [], [], 0.0
    with tempfile.TemporaryDirectory(prefix="private_gravity_lines_") as temp:
        for number, (line, style) in enumerate(pieces):
            part = os.path.join(temp, f"line_{number:03d}.mp3")
            result = backend.synthesize(pronunciations.apply(line, backend.name, entries), part, style=style,
                                        **common)
            clip = AudioFileClip(part)
            duration = float(clip.duration)
            clip.close()
            events += [{**item, "start_ms": item["start_ms"] + offset, "end_ms": item["end_ms"] + offset}
                       for item in result["words"]]
            parts.append(part)
            offset += duration * 1000
        _concat(parts, audio_path)
    return events


def cache_key(text: str) -> str:
    backend = tts.get()
    payload = json.dumps([text, backend.name, tts.channel_voice(backend.name), config.VOICE_RATE,
                          config.VOICE_PITCH, backend.cache_tag(), pronunciations.fingerprint()],
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_scene(text: str, cache_dir: str, scene_audio: str, label: str,
                 section: dict | None = None) -> tuple[list, bool]:
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
    events = synthesise_scene(text, scene_audio, label, section) if section is not None \
        else synthesise_scene(text, scene_audio, label)
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
    timed_words = []                      # narration_words.json (Phase 3.7: Shorts cut on sentences)
    cursor = 0.0
    scenes_without_words = []
    thoughts = heard_thoughts.enabled(output_dir, script)
    with tempfile.TemporaryDirectory(prefix="private_gravity_tts_") as temp_dir:
        try:
            for index, section in enumerate(sections, 1):
                text = section["narration"].strip()
                scene_audio = os.path.join(temp_dir, f"scene_{index:03d}.mp3")
                scene_srt = os.path.join(temp_dir, f"scene_{index:03d}.srt")
                label = f"scene {index}/{len(sections)}"
                events, hit = cached_scene(text, cache_dir, scene_audio, label, section)
                print(f"   → {'Reused' if hit else 'Synthesised'} {label} "
                      f"({len(text.split())} words, {len(events)} word timings)")
                clip = AudioFileClip(scene_audio)
                duration = float(clip.duration)
                clips.append(clip)
                if not events:
                    scenes_without_words.append(index)
                _write_srt_blocks(scene_cues(text, events, duration, thoughts), scene_srt)
                timed_words += [{**word, "start": round(word["start"] + cursor, 3), "end": round(word["end"] + cursor, 3),
                                 "scene_id": section.get("id")}
                                for word in scene_words(text, events, duration, thoughts)]
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
            backend = tts.get()
            if hasattr(backend, "close"):
                backend.close()                     # a local model gives the VRAM back

    if thoughts:                                    # gain and filters only: every timing above stays valid
        treated = heard_thoughts.apply(audio_path, heard_thoughts.intervals(timed_words))
        print(f"   → Heard thoughts: {treated} span(s) delivered quieter than speech")

    if scenes_without_words:
        print(f"   ⚠ No WordBoundary events for scene(s) {scenes_without_words} "
              f"(backend {tts.backend_name()}; edge-tts {edge_tts_version()}; word timings need "
              f"Communicate(boundary=\"WordBoundary\"), supported: {supports_word_boundary()}). "
              "Those captions use proportional timing.")

    # The concatenated file can differ by a few codec milliseconds. Pin the
    # last scene to the exact duration MoviePy will later attach to the video.
    rendered_duration = _get_audio_duration_ms(audio_path) / 1000
    durations[-1] += rendered_duration - sum(durations)
    write_timeline(os.path.join(output_dir, "scene_timeline.json"), sections, durations)
    with open(os.path.join(output_dir, "narration_words.json"), "w", encoding="utf-8") as handle:
        json.dump({"words": timed_words, "total_duration": round(rendered_duration, 3),
                   "voice": tts.identity()}, handle, ensure_ascii=False)

    words = sum(len(section["narration"].split()) for section in sections)
    entry = record_calibration(words, rendered_duration)
    if entry:
        print(f"   → Measured delivery: {entry['wpm']} wpm for {tts.backend_name()}:{tts.channel_voice()} "
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
