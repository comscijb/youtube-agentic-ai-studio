"""Offline TTS with a commercial-use licence (Phase 3.9-A).

``TTS_LOCAL_ENGINE``:

* ``kokoro`` — Kokoro-82M (weights Apache-2.0), 82 M parameters: fast on CPU, a set of stock voices
  (``TTS_VOICE``, e.g. ``am_michael``, ``bm_george``), no cloning. Kokoro needs numpy 2 and the
  project pins numpy < 2, so unless ``kokoro`` imports here it runs in a separate venv
  (``TTS_LOCAL_PYTHON``, default ``.venv-tts``, with ``pip install kokoro-onnx``) through
  ``audio/tts/kokoro_worker.py`` and the ONNX weights in ``models/tts/``;
* ``chatterbox`` — Resemble AI Chatterbox (MIT), zero-shot cloning from a reference clip in
  ``assets/voice/`` (provenance note required) and an ``exaggeration`` control used for delivery
  hints; runs on the GPU in ~5-7 GB.

Neither returns word boundaries, so the audio goes through forced alignment (``audio.tts.align``). Kokoro gets one
sentence per call (``audio.tts.sentences``): whole scenes turn into hidden batch joins inside its output.
On the GPU the model runs in this process, never inside ComfyUI: ComfyUI's models are freed first
(``/free``) and the TTS model is unloaded by ``close()`` when the narration is done.
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import config
from audio.tts import TTSUnavailable

ENGINES = {"kokoro": "Kokoro-82M (Apache-2.0)", "chatterbox": "Chatterbox (MIT)"}
# Delivery hints → Chatterbox exaggeration (0.5 is neutral); only from the screenplay's own signals.
EXAGGERATION = {"narration": 0.5, "dialogue": 0.6, "quiet": 0.35}
_loaded: dict = {}
ROOT = Path(__file__).resolve().parent.parent.parent


def engine() -> str:
    name = str(getattr(config, "TTS_LOCAL_ENGINE", "kokoro")).strip().lower()
    return name if name in ENGINES else "kokoro"


def device() -> str:
    wanted = str(getattr(config, "TTS_LOCAL_DEVICE", "auto")).lower()
    if wanted in ("cpu", "cuda"):
        return wanted
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def free_comfyui() -> None:
    """Free ComfyUI's VRAM before a local TTS model goes on the GPU (8 GB cards hold one model)."""
    try:
        from video.imagegen_comfyui import ComfyClient
        client = ComfyClient(timeout=5)
        if client.available():
            client.free_memory()
    except Exception:                                    # noqa: BLE001 — ComfyUI may simply not run
        pass


def write_wav(path, samples, rate: int) -> str:
    """float samples in [-1, 1] → 16-bit mono WAV (stdlib only)."""
    import numpy as np
    data = (np.clip(np.asarray(samples, dtype="float32").reshape(-1), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
    return str(path)


def _kokoro(voice: str):
    try:
        from kokoro import KPipeline
    except ImportError as exc:
        raise TTSUnavailable("Kokoro is not installed: pip install kokoro soundfile") from exc
    key = ("kokoro", voice[:1])
    if key not in _loaded:
        _loaded[key] = KPipeline(lang_code="b" if voice.startswith("b") else "a", device=device())
    return _loaded[key]


def worker_python() -> Path | None:
    """The interpreter that runs Kokoro out of process (``TTS_LOCAL_PYTHON``, else ``.venv-tts``)."""
    wanted = getattr(config, "TTS_LOCAL_PYTHON", "")
    for candidate in ([Path(wanted)] if wanted else [ROOT / ".venv-tts" / "Scripts" / "python.exe",
                                                     ROOT / ".venv-tts" / "bin" / "python"]):
        if candidate.is_file():
            return candidate
    return None


def kokoro_files() -> tuple[Path, Path]:
    model = Path(getattr(config, "TTS_KOKORO_MODEL", "models/tts/kokoro-v1.0.onnx"))
    model = model if model.is_absolute() else ROOT / model
    return model, model.with_name("voices-v1.0.bin")


class KokoroWorker:
    """A persistent ``kokoro_worker.py`` process: one JSON line in, one out."""

    def __init__(self, python: Path):
        model, voices = kokoro_files()
        if not model.is_file() or not voices.is_file():
            raise TTSUnavailable(f"Kokoro ONNX weights missing ({model.name}, {voices.name} in {model.parent})")
        self.process = subprocess.Popen(
            [str(python), str(Path(__file__).with_name("kokoro_worker.py")), str(model), str(voices)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        if not self._reply().get("ready"):
            raise TTSUnavailable("the Kokoro worker did not start")

    def _reply(self) -> dict:
        line = self.process.stdout.readline()
        if not line:
            raise TTSUnavailable("the Kokoro worker exited")
        return json.loads(line)

    def synthesize(self, text: str, voice: str, speed_factor: float, out_path: str) -> None:
        self.process.stdin.write(json.dumps({"text": text, "voice": voice, "speed": speed_factor,
                                             "out": str(out_path)}) + "\n")
        self.process.stdin.flush()
        reply = self._reply()
        if not reply.get("ok"):
            raise TTSUnavailable(f"Kokoro failed: {reply.get('error')}")

    def phonemes(self, text: str, voice: str) -> int:
        """Length by the model's own tokenizer."""
        self.process.stdin.write(json.dumps({"op": "phonemes", "text": text, "voice": voice}) + "\n")
        self.process.stdin.flush()
        reply = self._reply()
        if not reply.get("ok"):
            raise TTSUnavailable(f"Kokoro failed: {reply.get('error')}")
        return int(reply["phonemes"])

    def close(self) -> None:
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except Exception:                                # noqa: BLE001
            self.process.kill()


def _kokoro_worker() -> KokoroWorker:
    if "kokoro_worker" not in _loaded:
        python = worker_python()
        if python is None:
            raise TTSUnavailable("Kokoro is not installed: pip install kokoro, or create .venv-tts with kokoro-onnx")
        _loaded["kokoro_worker"] = KokoroWorker(python)
    return _loaded["kokoro_worker"]


def _kokoro_in_process() -> bool:
    import importlib.util
    return importlib.util.find_spec("kokoro") is not None


def _chatterbox():
    try:
        from chatterbox.tts import ChatterboxTTS
    except ImportError as exc:
        raise TTSUnavailable("Chatterbox is not installed: pip install chatterbox-tts") from exc
    if "chatterbox" not in _loaded:
        if device() == "cuda":
            free_comfyui()
        _loaded["chatterbox"] = ChatterboxTTS.from_pretrained(device=device())
    return _loaded["chatterbox"]


def phoneme_count(text: str, voice: str) -> int:
    """Phonemes of ``text``. In process there is no tokenizer to ask: KPipeline chunks by itself, characters are close."""
    return _kokoro_worker().phonemes(text, voice) if not _kokoro_in_process() else len(text)


def kokoro_clip(text: str, voice: str, speed_factor: float, out_path: str) -> None:
    """One sentence to a WAV."""
    if not _kokoro_in_process():
        _kokoro_worker().synthesize(text, voice, speed_factor, out_path)
        return
    import numpy as np
    pieces = [audio for _, _, audio in _kokoro(voice)(text, voice=voice, speed=speed_factor)]
    write_wav(out_path, np.concatenate([np.asarray(piece) for piece in pieces]) if pieces else [], 24000)


def kokoro_scene(voice: str, cache_dir=None, apply=None, progress=print):
    """The per-sentence synthesis of one channel voice."""
    from audio.tts.sentences import SceneSynthesis
    return SceneSynthesis(voice=voice, speed=channel_speed(), synth=kokoro_clip, count=lambda text: phoneme_count(text, voice), cache_dir=cache_dir,
                          model=kokoro_files()[0].name, apply=apply, check=bool(getattr(config, "TTS_STUTTER_CHECK", True)), progress=progress)


def speed(rate: str) -> float:
    """Edge-style "+10%" / "-5%" → a speed factor."""
    try:
        return round(1 + float(str(rate).strip().rstrip("%")) / 100, 3)
    except ValueError:
        return 1.0


def channel_speed() -> float:
    """Kokoro's native ``speed`` for the channel voice (Phase 3.9.4-G): explicit, instead of VOICE_RATE, and
    never a post-process atempo."""
    return round(float(getattr(config, "TTS_LOCAL_SPEED", 1.0)), 3)


class LocalBackend:
    name = "local"
    audio_ext = "wav"

    @property
    def supports_style(self) -> bool:
        return engine() == "chatterbox"

    @property
    def sentence_mode(self) -> bool:
        return engine() == "kokoro"

    def sentence_synthesis(self, voice: str, cache_dir=None, apply=None, progress=print):
        return kokoro_scene(voice, cache_dir, apply, progress)

    def available(self) -> tuple[bool, str]:
        import importlib.util
        if engine() == "kokoro" and not _kokoro_in_process():
            if worker_python() is None or not all(path.is_file() for path in kokoro_files()):
                return False, f"{ENGINES['kokoro']} not installed (no kokoro, no .venv-tts with weights)"
        elif importlib.util.find_spec(engine()) is None:
            return False, f"{ENGINES[engine()]} not installed"
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "forced alignment needs faster-whisper"
        return True, f"{ENGINES[engine()]} on {device()}"

    def cache_tag(self) -> str:
        reference = getattr(config, "TTS_REFERENCE_CLIP", "") if engine() == "chatterbox" else ""
        return f"local {engine()} ref={Path(reference).name if reference else ''} speed={channel_speed()}" \
            + (" sentences" if engine() == "kokoro" else "")

    def synthesize(self, text: str, out_path: str, *, voice: str, rate: str, pitch: str,
                   style: str | None = None, label: str = "") -> dict:
        from audio.tts import align
        if engine() == "kokoro":
            return self._by_sentence(text, str(out_path), voice, label)
        target = str(out_path)
        out_path = target if target.lower().endswith(".wav") else target + ".wav"
        if True:
            from audio.tts import voices
            reference = getattr(config, "TTS_REFERENCE_CLIP", "")
            model = _chatterbox()
            kwargs = {"exaggeration": EXAGGERATION.get(style or "narration", 0.5)}
            if reference:
                kwargs["audio_prompt_path"] = str(voices.check_reference(reference))
            wav = model.generate(text, **kwargs)
            write_wav(out_path, wav.squeeze().cpu().numpy(), model.sr)
        events, report = align.align(out_path, text)
        if not report["ok"]:
            print(f"   ⚠ {label or 'scene'}: alignment coverage {report['coverage']:.0%}, "
                  f"monotonic {report['monotonic']} (captions may drift)")
        if out_path != target:                           # the narrator asked for another format (mp3)
            import subprocess
            from video.tape import ffmpeg_exe
            subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", out_path, "-c:a", "libmp3lame",
                            "-q:a", "2", target], check=True)
            Path(out_path).unlink(missing_ok=True)
        return {"audio": target, "words": events, "aligned": True, "alignment": report}

    def _by_sentence(self, text: str, target: str, voice: str, label: str) -> dict:
        """Auditions and previews: the same per-sentence path as a narration, placed with the same pauses."""
        from audio import assembly
        from video.captions import align_words
        scene = kokoro_scene(voice).scene(text, label)
        words = align_words(text, scene["events"])
        built = assembly.assemble([{"samples": scene["samples"], "words": words, "text": text, "cuts": scene["cuts"]}])
        assembly.encode(built["samples"], target)
        events = [{"text": word["text"], "start_ms": word["start"] * 1000, "end_ms": word["end"] * 1000} for word in built["words"]]
        coverage = scene["stats"]["coverage"]
        report = {"coverage": coverage, "monotonic": True, "drift_ms": None, "ok": coverage >= align.MIN_COVERAGE} if False else None
        from audio.tts import align as aligner
        report = {"coverage": coverage, "monotonic": True, "drift_ms": None, "ok": coverage >= aligner.MIN_COVERAGE}
        return {"audio": target, "words": events, "aligned": True, "alignment": report, "stutter": scene["stats"]}

    def close(self) -> None:
        """Unload the model (and give the VRAM back to ComfyUI)."""
        worker = _loaded.pop("kokoro_worker", None)
        if worker is not None:
            worker.close()
        _loaded.clear()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
