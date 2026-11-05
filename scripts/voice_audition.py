"""Render the same passage with every available TTS backend/voice and compare them (Phase 3.9-A/3.9.1-B).

Run:  python -m scripts.voice_audition --catalogue          # every catalogue voice, ~20 s each
      python -m scripts.voice_audition --list               # the catalogue voices that exist here
      python -m scripts.voice_audition --text-file briefs/audition.txt --backends edge,local
      python -m scripts.voice_audition --voices edge:en-GB-RyanNeural,local:bm_george

The text defaults to briefs/voice-preview.txt. Writes debug/voice_audition/<backend>_<voice>_<text
hash>.mp3, results.json and index.html (listen side by side; the GUI → Settings → Vozes reads the same
files). A sample that already exists for the same voice and text is reused, not rendered again; a
voice that fails is recorded with its error and the others go on. For each sample: render time,
real-time factor, peak VRAM (nvidia-smi), word-timing coverage and drift (native timings vs forced
alignment when both exist), integrated loudness, the engine licence and, for Kokoro, the stutter-check hits. Pick the
channel voice in the GUI.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import config
from audio import tts
from audio.tts import align, catalogue, pronunciations
from studio.filenames import safe_filename

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "debug" / "voice_audition"
DEFAULT_TEXT = ROOT / "briefs" / "voice-preview.txt"
DEFAULT_VOICES = {
    "edge": ["en-US-AndrewNeural", "en-GB-RyanNeural", "en-US-BrianNeural"],
    "local": ["am_michael", "bm_george", "bm_fable"],
    "elevenlabs": [],
}
LICENCES = {"edge": catalogue.LICENCES["edge"], "local:kokoro": catalogue.LICENCES["local"],
            "local:chatterbox": "Chatterbox MIT (commercial use allowed); clone only own/licensed voices",
            "elevenlabs": catalogue.LICENCES["elevenlabs"]}


class VramSampler:
    """Peak GPU memory used while a sample renders (nvidia-smi every 0.5 s); None without nvidia-smi."""

    def __init__(self):
        self.peak, self.base, self._stop = None, None, threading.Event()

    @staticmethod
    def used() -> int | None:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout
            return int(out.split()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None

    def __enter__(self):
        self.base = self.peak = self.used()
        if self.base is not None:
            threading.Thread(target=self._loop, daemon=True).start()
        return self

    def _loop(self):
        while not self._stop.wait(0.5):
            value = self.used()
            if value is not None:
                self.peak = max(self.peak or 0, value)

    def __exit__(self, *exc):
        self._stop.set()

    @property
    def mib(self) -> int | None:
        return None if self.base is None else max(0, (self.peak or self.base) - self.base)


def loudness(path: Path) -> float | None:
    from video.tape import ffmpeg_exe
    done = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                          capture_output=True, text=True)
    found = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", done.stderr)
    return float(found[-1]) if found else None


def duration(path: Path) -> float:
    from video.music import media_duration
    return float(media_duration(str(path)))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:10]


def sample_name(backend: str, voice: str, text: str) -> str:
    """"_s" on local samples: they are rendered sentence by sentence; earlier files have Kokoro's batch joins inside."""
    suffix = "_s" if backend == "local" else ""
    return f"{safe_filename(backend)}_{safe_filename(voice)}_{text_hash(text)}{suffix}.mp3"


def stutter_hits(result: dict) -> int | None:
    """Stutter-check hits left in a sample; None when the backend has no such check."""
    report = result.get("stutter")
    return None if report is None else sum(len(item.get("hits", [])) for item in report.get("flagged", []))


def load_results(out: Path = OUT) -> dict:
    try:
        data = json.loads((Path(out) / "results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"text": data.get("text", ""), "rows": data.get("rows", [])}


def cached(backend: str, voice: str, text: str, out: Path = OUT) -> dict | None:
    """The earlier result for this voice and text, when its sample file still exists."""
    name = sample_name(backend, voice, text)
    if not (Path(out) / name).is_file():
        return None
    row = next((row for row in load_results(out)["rows"] if row.get("file") == name), None)
    return dict(row or {"backend": backend, "voice": voice, "file": name, "text_hash": text_hash(text)}, cached=True)


def audition(text: str, backend_name: str, voice: str, out: Path) -> dict:
    """``voice`` may carry a Kokoro speed: "bm_lewis@0.88"."""
    from unittest.mock import patch
    name, _, speed = voice.partition("@")
    if speed:
        with patch.object(config, "TTS_LOCAL_SPEED", float(speed)):
            return {**_audition(text, backend_name, name, out, sample=voice), "voice": voice, "speed": float(speed)}
    return _audition(text, backend_name, voice, out, sample=voice)


def _audition(text: str, backend_name: str, voice: str, out: Path, sample: str) -> dict:
    row = {"backend": backend_name, "voice": voice, "text_hash": text_hash(text),
           "licence": LICENCES.get(f"local:{getattr(config, 'TTS_LOCAL_ENGINE', 'kokoro')}" if backend_name == "local"
                                   else backend_name, "")}
    try:
        backend = tts.get(backend_name)
        ok, detail = backend.available()
    except Exception as exc:                             # noqa: BLE001 — one failing voice never stops the rest
        return {**row, "error": str(exc)[:300]}
    row["detail"] = detail
    if not ok:
        return {**row, "error": detail}
    path = Path(out) / sample_name(backend_name, sample, text)
    spoken = pronunciations.apply(text, backend_name)
    started = time.monotonic()
    try:
        with VramSampler() as vram:
            result = backend.synthesize(spoken, str(path), voice=voice, rate=config.VOICE_RATE,
                                        pitch=config.VOICE_PITCH, label=f"{backend_name}:{voice}")
    except Exception as exc:                             # noqa: BLE001 — one failing voice never stops the rest
        path.unlink(missing_ok=True)
        return {**row, "error": str(exc)[:300]}
    finally:
        if hasattr(backend, "close"):
            backend.close()
    seconds = time.monotonic() - started
    row.update(file=path.name, vram_mib=vram.mib)
    try:
        return {**row, **measure(path, text, result, seconds), "stutter_hits": stutter_hits(result)}
    except Exception as exc:                             # noqa: BLE001 — the sample is still playable
        return {**row, "error": f"measuring failed: {str(exc)[:200]}"}


def measure(path: Path, text: str, result: dict, seconds: float) -> dict:
    length = duration(path)
    words = align.script_words(text)
    if result.get("aligned"):
        report = result.get("alignment") or {}
    else:                                                # native timings: coverage, and drift vs the aligner
        events, coverage = align.match(words, result["words"])
        try:
            forced, _ = align.align(str(path), text)
            report = align.validate(events, coverage, truth=forced)
        except align.AlignerUnavailable:
            report = align.validate(events, coverage)
    return {"seconds": round(seconds, 1), "audio_seconds": round(length, 1),
            "rtf": round(seconds / length, 3) if length else None,
            "coverage": report.get("coverage"), "drift_ms": report.get("drift_ms"), "lufs": loudness(path)}


def write_page(rows: list[dict], text: str, out: Path) -> Path:
    def cell(value):
        return "—" if value is None else html.escape(str(value))

    def sample(row):
        if not row.get("file"):
            return cell(row.get("error"))
        return f'<audio controls preload="none" src="{html.escape(row["file"])}"></audio>'
    body = "".join(
        f"<tr><td>{cell(row['backend'])}</td><td>{cell(row['voice'])}</td><td>{sample(row)}</td>"
        f"<td>{cell(row.get('rtf'))}</td><td>{cell(row.get('vram_mib'))}</td><td>{cell(row.get('coverage'))}</td>"
        f"<td>{cell(row.get('drift_ms'))}</td><td>{cell(row.get('lufs'))}</td><td>{cell(row.get('stutter_hits'))}</td><td>{cell(row.get('licence'))}</td></tr>"
        for row in rows)
    page = Path(out) / "index.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'><title>Voice audition</title>"
        "<style>body{font-family:sans-serif;margin:24px;background:#0c1218;color:#f4f0e8}td,th{padding:6px 10px;"
        "border-bottom:1px solid #333;text-align:left}</style><h1>Voice audition</h1>"
        f"<p style='max-width:70ch'>{html.escape(text)}</p><table><tr><th>Backend</th><th>Voice</th><th>Sample</th>"
        "<th>RTF</th><th>VRAM MiB</th><th>Coverage</th><th>Drift ms</th><th>LUFS</th><th>Stutter</th><th>Licence</th></tr>"
        f"{body}</table>", encoding="utf-8")
    return page


def run(pairs: list[tuple[str, str]], text: str, out: Path = OUT, progress=print) -> list[dict]:
    """Audition each (backend, voice), reusing cached samples. results.json keeps the latest row of every
    voice so the GUI can list them all. Returns this run's rows."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, (backend, voice) in enumerate(pairs, 1):
        backend, voice = backend.strip(), voice.strip()
        progress(f"[{number}/{len(pairs)}] {backend}:{voice}")
        row = cached(backend, voice, text, out)
        if row is None:
            try:
                row = audition(text, backend, voice, out)
            except Exception as exc:                     # noqa: BLE001
                row = {"backend": backend, "voice": voice, "text_hash": text_hash(text), "error": str(exc)[:300]}
            progress("   " + (row.get("error") or f"RTF {row.get('rtf')} | VRAM {row.get('vram_mib')} MiB | coverage "
                                                  f"{row.get('coverage')} | drift {row.get('drift_ms')} ms | {row.get('lufs')} LUFS | stutter {row.get('stutter_hits')}"))
        else:
            progress("   cached sample reused")
        rows.append(row)
        merged = {catalogue.key(item["backend"], item["voice"]): item for item in load_results(out)["rows"]}
        merged[catalogue.key(backend, voice)] = {key: value for key, value in row.items() if key != "cached"}
        (out / "results.json").write_text(json.dumps({"text": text, "rows": list(merged.values())}, indent=2,
                                                     ensure_ascii=False), encoding="utf-8")
    write_page(load_results(out)["rows"], text, out)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render one passage with each TTS backend/voice.")
    parser.add_argument("--text-file", default=str(DEFAULT_TEXT))
    parser.add_argument("--backends", default="edge,local,elevenlabs")
    parser.add_argument("--voices", help="backend:voice,... (default: a few per backend)")
    parser.add_argument("--catalogue", action="store_true", help="every catalogue voice installed here")
    parser.add_argument("--list", action="store_true", help="print the catalogue voices and exit")
    args = parser.parse_args(argv)
    say = lambda line: print(str(line).encode("ascii", "replace").decode("ascii"), flush=True)
    if args.list:
        for entry in catalogue.catalogue():
            say(f"{entry['backend']}:{entry['voice']:<26} {entry['label']}")
        return 0
    text = Path(args.text_file).read_text(encoding="utf-8").strip()
    if args.voices:
        pairs = [tuple(item.split(":", 1)) for item in args.voices.split(",") if ":" in item]
    elif args.catalogue:
        pairs = [(entry["backend"], entry["voice"]) for entry in catalogue.catalogue()]
    else:
        pairs = [(backend, voice) for backend in args.backends.split(",") for voice in
                 (DEFAULT_VOICES.get(backend.strip()) or [tts.channel_voice(backend.strip())])]
    run(pairs, text, OUT, progress=say)
    say(f"Listen: {OUT / 'index.html'} (or GUI, Settings, Vozes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
