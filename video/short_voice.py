"""Voice and audio for the format Shorts (Phase 4.3-C).

* **Voice** — the channel voice (Kokoro Lewis), synthesised per sentence like the episode (3.9.5) but at the Shorts
  speed (``SHORTS_TTS_SPEED``, 0.95 against the episode's 0.88) with short pauses (``SHORTS_PAUSES``: 0.15 s between
  sentences, no scene pauses). Pronunciations and the heard-thought treatment are applied as in the episode. The voice
  starts after ``LEAD`` seconds: the first frame already has sound (the accent below). Cached per text and speed in
  ``shorts/voice/<id>.wav`` + ``.json`` (word timings in Short time).
* **Mix** — a sound accent at 0.0 s (impact or whoosh from the 4.2 SFX library), music at higher energy than the
  episode (the music index, Dramatic first), ducked under the voice, a sting under the CTA, then loudness normalised to
  −14 LUFS (``video.music.normalize_loudness``). Without registered SFX the mix is music + voice and says so.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np

import config
from agents.narrative import atomic_write_json, load_json

LEAD = 0.3                     # seconds of accent before the first word
END_CARD = 1.8                 # seconds after the last word for the CTA card (not for The Line, which loops)
MUSIC_BOOST = 1.5              # the Shorts bed sits higher than the episode's MUSIC_VOLUME
OPENING_TAGS = ("impact", "whoosh")
STING_TAGS = ("impact", "riser", "whoosh")
FOLDER = Path("shorts") / "voice"


def speed() -> float:
    return round(float(getattr(config, "SHORTS_TTS_SPEED", 0.95)), 3)


def pauses() -> dict:
    from audio import assembly
    return {**assembly.DEFAULT_PAUSES, **{"sentence": 0.15, "paragraph": 0.15, "clause": 0.1},
            **(getattr(config, "SHORTS_PAUSES", None) or {})}


def _key(text: str, backend) -> str:
    from audio.tts import pronunciations
    payload = json.dumps([text, backend.name, backend.cache_tag(), speed(), pauses(), LEAD, pronunciations.fingerprint()],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scene(text: str, backend, output: Path, thoughts: bool) -> dict:
    """One "scene" of the Short in PCM with word timings (scene-relative)."""
    from audio import assembly
    from audio import tts
    from audio.tts import pronunciations
    from video.narrator import scene_words, synthesise_scene
    voice = tts.channel_voice(backend.name)
    if getattr(backend, "sentence_mode", False):
        entries = pronunciations.load()
        synthesis = backend.sentence_synthesis(voice, str(output / ".tts_cache" / "sentences"),
                                               lambda part: pronunciations.apply(part, backend.name, entries), speed=speed())
        made = synthesis.scene(text, "short")
        duration = len(made["samples"]) / assembly.RATE
        return {"id": "short", "text": text, "samples": made["samples"], "cuts": made["cuts"], "section_type": "",
                "words": scene_words(text, made["events"], duration, thoughts)}
    with tempfile.TemporaryDirectory(prefix="private_gravity_short_") as temp:
        path = str(Path(temp) / f"short.{getattr(backend, 'audio_ext', 'mp3')}")
        events = synthesise_scene(text, path, "short")
        samples = assembly.decode(path)
    return {"id": "short", "text": text, "samples": samples, "section_type": "",
            "words": scene_words(text, events, len(samples) / assembly.RATE, thoughts)}


def synthesize(output_dir, item: dict, *, backend=None, script: dict | None = None) -> dict:
    """{"voice", "words", "duration", "lead", "cta_start", "speed"}: the Short's narration (cached)."""
    from audio import assembly, heard_thoughts, tts
    from video.key_lines import find_span
    from video.short_formats import spoken_text
    output = Path(output_dir)
    backend = backend or tts.get()
    text = spoken_text(item)
    key = _key(text, backend)
    folder = output / FOLDER
    audio, notes = folder / f"{item['id']}.wav", folder / f"{item['id']}.json"
    saved = load_json(notes) or {}
    if saved.get("key") == key and audio.is_file():
        return {**saved, "voice": str(audio)}
    script = script or load_json(output / "script.json") or {}
    thoughts = heard_thoughts.enabled(output, script)
    scene = _scene(text, backend, output, thoughts)
    made = assembly.assemble([scene], settings=pauses())
    lead = np.zeros(int(round(LEAD * assembly.RATE)), dtype=np.float32)
    samples = np.concatenate([lead, made["samples"]])
    words = [{**word, "start": round(float(word["start"]) + LEAD, 3), "end": round(float(word["end"]) + LEAD, 3)}
             for word in made["words"]]
    folder.mkdir(parents=True, exist_ok=True)
    assembly.encode(samples, audio)
    if thoughts:
        heard_thoughts.apply(audio, heard_thoughts.intervals(words))
    cta = str(item.get("cta_text", "")).strip()
    span = find_span(words, cta) if cta and spoken_text(item) != str(item.get("script", "")).strip() else None
    record = {"key": key, "words": words, "duration": round(len(samples) / assembly.RATE, 3), "lead": LEAD,
              "cta_start": float(words[span[0]]["start"]) if span else None, "speed": speed(), "text": text}
    atomic_write_json(notes, record)
    return {**record, "voice": str(audio)}


# ── The mix ─────────────────────────────────────────────────────────────────

def _pick(library: dict, tags, salt: str) -> dict | None:
    for tag in tags:
        files = library.get(tag) or []
        if files:
            return sorted(files, key=lambda item: hashlib.sha256(f"{salt}|{item['relative']}".encode()).hexdigest())[0]
    return None


def music_track(salt: str, tracks: list[dict] | None = None) -> dict | None:
    """The highest-energy Dramatic track (the music index), rotating by ``salt``."""
    from video import music_plan
    library = tracks if tracks is not None else music_plan.index().get("tracks", [])
    if not library:
        return None
    rank = {"high": 0, "mid": 1, "low": 2}

    def score(item):
        return (item.get("mood") != "dramatic", rank.get(item.get("energy"), 3),
                hashlib.sha256(f"{salt}|{item['file']}".encode()).hexdigest())
    return sorted(library, key=score)[0]


def plan(item: dict, voice: dict, *, salt: str, sfx_library: dict | None = None, tracks: list[dict] | None = None) -> dict:
    """{"duration", "cues", "music", "swells", "missing"}: what goes under the voice (no file is read but the index)."""
    from video import music_plan, sfx
    library = sfx_library if sfx_library is not None else sfx.library()
    tail = 0.0 if item.get("format") == "line" else END_CARD
    duration = round(float(voice["duration"]) + tail, 3)
    cues, missing = [], []
    opening = _pick(library, OPENING_TAGS, salt)
    if opening:
        cues.append({"path": opening["path"], "start": 0.0, "end": 1.6, "kind": "accent", "role": "opening",
                     "under_voice_db": sfx.UNDER_VOICE_DB["accent"]})
    else:
        missing.append("/".join(OPENING_TAGS))
    sting_at = voice.get("cta_start")
    if sting_at is None and item.get("format") != "line":
        sting_at = float(voice["duration"])               # the CTA card: a sting as it appears
    swells = []
    if sting_at is not None:
        sting = _pick(library, STING_TAGS, salt + "sting")
        if sting:
            cues.append({"path": sting["path"], "start": round(max(0.0, sting_at - 0.1), 3), "end": round(sting_at + 1.6, 3),
                         "kind": "accent", "role": "sting", "under_voice_db": sfx.UNDER_VOICE_DB["accent"]})
        swells.append(round(sting_at + 0.4, 3))
    track = music_track(salt, tracks)
    music = []
    if track:
        volume = float(getattr(config, "MUSIC_VOLUME", 0.12)) * MUSIC_BOOST * music_plan.level_gain(track)
        offset = round(min(max(0.0, float(track.get("duration") or 0) * 0.25), 60.0), 3)   # skip a slow intro
        music.append({"path": str(music_plan.library_dir() / track["file"]), "file": track["file"], "start": 0.0,
                      "end": duration, "offset": offset, "fade_in": 0.2, "fade_out": min(1.2, duration / 4),
                      "volume": round(volume, 4)})
    return {"duration": duration, "cues": cues, "music": music, "swells": swells, "missing": missing}


def mix(output_dir, item: dict, voice: dict, *, sfx_library: dict | None = None, tracks: list[dict] | None = None) -> dict:
    """shorts/<id>.mp3: accent, voice, ducked music, sting, −14 LUFS. Returns the plan and the loudness report."""
    from audio import assembly
    from video import music, sound
    output = Path(output_dir)
    layout = plan(item, voice, salt=f"{output.name}|{item['id']}", sfx_library=sfx_library, tracks=tracks)
    target = output / "shorts" / f"{item['id']}.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="private_gravity_short_mix_") as temp:
        padded = Path(temp) / "voice.wav"
        samples = assembly.decode(voice["voice"])
        tail = int(round((layout["duration"] - len(samples) / assembly.RATE) * assembly.RATE))
        assembly.encode(np.concatenate([samples, np.zeros(max(0, tail), dtype=np.float32)]), padded)
        voice_db = sound.rms_db(str(padded))
        cues = [{**cue, "gain": sound.gain_for(voice_db, sound.rms_db(cue["path"], 10.0), cue["under_voice_db"])}
                for cue in layout["cues"]]
        mixed = Path(temp) / "mixed.wav"
        graph, inputs = sound.graph(layout["duration"], layout["music"], cues, swells=layout["swells"])
        result = music._run(sound.command(str(padded), inputs, graph, str(mixed)))
        if result.returncode != 0:
            raise RuntimeError(f"Short mix failed: {result.stderr[-300:]}")
        loudness = music.normalize_loudness(str(mixed), str(target))
    return {"audio": str(target), "plan": {**layout, "cues": [{key: cue[key] for key in ("path", "start", "end", "role")}
                                                              for cue in layout["cues"]]}, "loudness": loudness}
