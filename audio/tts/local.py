"""Offline TTS with a commercial-use licence (Phase 3.9-A).

``TTS_LOCAL_ENGINE``:

* ``kokoro`` — Kokoro-82M (weights Apache-2.0), 82 M parameters: fast on CPU, a set of stock voices
  (``TTS_VOICE``, e.g. ``am_michael``, ``bm_george``), no cloning;
* ``chatterbox`` — Resemble AI Chatterbox (MIT), zero-shot cloning from a reference clip in
  ``assets/voice/`` (provenance note required) and an ``exaggeration`` control used for delivery
  hints; runs on the GPU in ~5-7 GB.

Neither returns word boundaries, so the audio goes through forced alignment (``audio.tts.align``).
On the GPU the model runs in this process, never inside ComfyUI: ComfyUI's models are freed first
(``/free``) and the TTS model is unloaded by ``close()`` when the narration is done.
"""
from __future__ import annotations

import wave
from pathlib import Path

import config
from audio.tts import TTSUnavailable

ENGINES = {"kokoro": "Kokoro-82M (Apache-2.0)", "chatterbox": "Chatterbox (MIT)"}
# Delivery hints → Chatterbox exaggeration (0.5 is neutral); only from the screenplay's own signals.
EXAGGERATION = {"narration": 0.5, "dialogue": 0.6, "quiet": 0.35}
_loaded: dict = {}


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


def speed(rate: str) -> float:
    """Edge-style "+10%" / "-5%" → a speed factor."""
    try:
        return round(1 + float(str(rate).strip().rstrip("%")) / 100, 3)
    except ValueError:
        return 1.0


class LocalBackend:
    name = "local"
    audio_ext = "wav"

    @property
    def supports_style(self) -> bool:
        return engine() == "chatterbox"

    def available(self) -> tuple[bool, str]:
        import importlib.util
        module = {"kokoro": "kokoro", "chatterbox": "chatterbox"}[engine()]
        if importlib.util.find_spec(module) is None:
            return False, f"{ENGINES[engine()]} not installed"
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "forced alignment needs faster-whisper"
        return True, f"{ENGINES[engine()]} on {device()}"

    def cache_tag(self) -> str:
        reference = getattr(config, "TTS_REFERENCE_CLIP", "") if engine() == "chatterbox" else ""
        return f"local {engine()} ref={Path(reference).name if reference else ''}"

    def synthesize(self, text: str, out_path: str, *, voice: str, rate: str, pitch: str,
                   style: str | None = None, label: str = "") -> dict:
        from audio.tts import align
        target = str(out_path)
        out_path = target if target.lower().endswith(".wav") else target + ".wav"
        if engine() == "kokoro":
            pipeline = _kokoro(voice)
            import numpy as np
            pieces = [audio for _, _, audio in pipeline(text, voice=voice, speed=speed(rate))]
            write_wav(out_path, np.concatenate([np.asarray(piece) for piece in pieces]) if pieces else [], 24000)
        else:
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

    def close(self) -> None:
        """Unload the model (and give the VRAM back to ComfyUI)."""
        _loaded.clear()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
