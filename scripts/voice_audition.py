"""Render the same passage with every available TTS backend/voice and compare them (Phase 3.9-A).

Run:  python -m scripts.voice_audition --text-file briefs/audition.txt
      python -m scripts.voice_audition --text-file briefs/audition.txt --backends edge,local
      python -m scripts.voice_audition --text-file briefs/audition.txt --voices edge:en-GB-RyanNeural,local:bm_george

Writes debug/voice_audition/<backend>_<voice>.mp3, results.json and index.html (listen side by side;
the GUI → Settings → Vozes reads the same files). For each sample: render time, real-time factor,
peak VRAM (nvidia-smi), word-timing coverage and drift (native timings vs forced alignment when
both exist), integrated loudness and the engine licence. Pick the channel voice in the GUI.
"""
from __future__ import annotations

import argparse
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
from audio.tts import align, pronunciations
from studio.filenames import safe_filename

OUT = Path(__file__).resolve().parent.parent / "debug" / "voice_audition"
DEFAULT_VOICES = {
    "edge": ["en-US-AndrewNeural", "en-GB-RyanNeural", "en-US-BrianNeural"],
    "local": ["am_michael", "bm_george", "bm_fable"],
    "elevenlabs": [],
}
LICENCES = {"edge": "Microsoft Edge read-aloud service (online; stock voice)",
            "local:kokoro": "Kokoro-82M weights Apache-2.0 (commercial use allowed)",
            "local:chatterbox": "Chatterbox MIT (commercial use allowed); clone only own/licensed voices",
            "elevenlabs": "ElevenLabs paid plan terms (commercial use on paid tiers)"}


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


def audition(text: str, backend_name: str, voice: str, out: Path) -> dict:
    backend = tts.get(backend_name)
    ok, detail = backend.available()
    row = {"backend": backend_name, "voice": voice, "detail": detail,
           "licence": LICENCES.get(f"local:{getattr(config, 'TTS_LOCAL_ENGINE', 'kokoro')}" if backend_name == "local"
                                   else backend_name, "")}
    if not ok:
        return {**row, "error": detail}
    path = out / f"{safe_filename(backend_name)}_{safe_filename(voice)}.mp3"
    spoken = pronunciations.apply(text, backend_name)
    started = time.monotonic()
    try:
        with VramSampler() as vram:
            result = backend.synthesize(spoken, str(path), voice=voice, rate=config.VOICE_RATE,
                                        pitch=config.VOICE_PITCH, label=f"{backend_name}:{voice}")
    except Exception as exc:                             # noqa: BLE001 — one failing voice never stops the rest
        return {**row, "error": str(exc)[:300]}
    finally:
        if hasattr(backend, "close"):
            backend.close()
    seconds = time.monotonic() - started
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
    return {**row, "file": path.name, "seconds": round(seconds, 1), "audio_seconds": round(length, 1),
            "rtf": round(seconds / length, 3) if length else None, "vram_mib": vram.mib,
            "coverage": report.get("coverage"), "drift_ms": report.get("drift_ms"),
            "lufs": loudness(path)}


def write_page(rows: list[dict], text: str, out: Path) -> Path:
    def cell(value):
        return "—" if value is None else html.escape(str(value))

    def sample(row):
        if not row.get("file"):
            return cell(row.get("error"))
        return f'<audio controls src="{html.escape(row["file"])}"></audio>'
    body = "".join(
        f"<tr><td>{cell(row['backend'])}</td><td>{cell(row['voice'])}</td><td>{sample(row)}</td>"
        f"<td>{cell(row.get('rtf'))}</td><td>{cell(row.get('vram_mib'))}</td><td>{cell(row.get('coverage'))}</td>"
        f"<td>{cell(row.get('drift_ms'))}</td><td>{cell(row.get('lufs'))}</td><td>{cell(row.get('licence'))}</td></tr>"
        for row in rows)
    page = out / "index.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'><title>Voice audition</title>"
        "<style>body{font-family:sans-serif;margin:24px;background:#0c1218;color:#f4f0e8}td,th{padding:6px 10px;"
        "border-bottom:1px solid #333;text-align:left}</style><h1>Voice audition</h1>"
        f"<p style='max-width:70ch'>{html.escape(text)}</p><table><tr><th>Backend</th><th>Voice</th><th>Sample</th>"
        "<th>RTF</th><th>VRAM MiB</th><th>Coverage</th><th>Drift ms</th><th>LUFS</th><th>Licence</th></tr>"
        f"{body}</table>", encoding="utf-8")
    return page


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render one passage with each TTS backend/voice.")
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--backends", default="edge,local,elevenlabs")
    parser.add_argument("--voices", help="backend:voice,… (default: a few per backend)")
    args = parser.parse_args(argv)
    text = Path(args.text_file).read_text(encoding="utf-8").strip()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.voices:
        pairs = [tuple(item.split(":", 1)) for item in args.voices.split(",") if ":" in item]
    else:
        pairs = [(backend, voice) for backend in args.backends.split(",") for voice in
                 (DEFAULT_VOICES.get(backend.strip()) or [tts.channel_voice(backend.strip())])]
    rows = []
    for backend, voice in pairs:
        print(f"-> {backend}:{voice}", flush=True)
        row = audition(text, backend.strip(), voice.strip(), OUT)
        rows.append(row)
        print("   " + (row.get("error") or f"RTF {row['rtf']} · VRAM {row['vram_mib']} MiB · coverage "
                                           f"{row['coverage']} · drift {row['drift_ms']} ms · {row['lufs']} LUFS"))
    (OUT / "results.json").write_text(json.dumps({"text": text, "rows": rows}, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    print(f"Listen: {write_page(rows, text, OUT)} (or GUI, Settings, Vozes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
