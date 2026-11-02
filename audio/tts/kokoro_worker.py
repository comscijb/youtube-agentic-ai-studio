"""Kokoro-82M (ONNX) worker, run by a separate interpreter (Phase 3.9-A).

The project pins numpy < 2 (moviepy 1.0.3) while kokoro / kokoro-onnx need numpy 2, so Kokoro runs in
its own venv (``TTS_LOCAL_PYTHON``, by default ``.venv-tts``) with ``pip install kokoro-onnx``.
This file imports nothing from the project. Protocol: one JSON request per stdin line
``{"text", "voice", "speed", "out"}`` → one JSON reply per stdout line ``{"ok", "out"|"error"}``.

    <venv python> audio/tts/kokoro_worker.py <model.onnx> <voices.bin>
"""
import json
import sys
import wave


def write_wav(path, samples, rate):
    import numpy as np
    data = (np.clip(np.asarray(samples, dtype="float32").reshape(-1), -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())


def main() -> int:
    from kokoro_onnx import Kokoro
    model = Kokoro(sys.argv[1], sys.argv[2])
    print(json.dumps({"ok": True, "ready": True}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            voice = request["voice"]
            samples, rate = model.create(request["text"], voice=voice, speed=float(request.get("speed", 1.0)),
                                         lang="en-gb" if voice.startswith("b") else "en-us")
            write_wav(request["out"], samples, rate)
            reply = {"ok": True, "out": request["out"]}
        except Exception as exc:                         # noqa: BLE001 — reported to the caller
            reply = {"ok": False, "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}
        print(json.dumps(reply), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
