"""Kokoro-82M (ONNX) worker, run by a separate interpreter (Phase 3.9-A).

The project pins numpy < 2 (moviepy 1.0.3) while kokoro / kokoro-onnx need numpy 2, so Kokoro runs in
its own venv (``TTS_LOCAL_PYTHON``, by default ``.venv-tts``) with ``pip install kokoro-onnx``.
This file imports nothing from the project. Protocol: one JSON request per stdin line, one JSON reply per stdout line:

* ``{"text", "voice", "speed", "out"}`` -> ``{"ok", "out", "phonemes"|"error"}``
* ``{"op": "phonemes", "text", "voice"}`` -> ``{"ok", "phonemes"}``, measured with the model's own tokenizer

``Kokoro.create`` silently cuts anything over 510 phonemes into batches with no context between them, so a request at
or over ``MAX_PHONEMES`` is refused: the caller splits at a clause boundary instead (Phase 3.9.5-A). ``trim=False``:
kokoro-onnx's own trim can clip onsets and tails; the caller trims with ``assembly.trim_bounds``.

    <venv python> audio/tts/kokoro_worker.py <model.onnx> <voices.bin>
"""
import json
import sys
import wave

MAX_PHONEMES = 500


def _lang(voice):
    return "en-gb" if voice.startswith("b") else "en-us"


def handle(model, request):
    """One request; raises on anything the caller must hear about."""
    voice = request["voice"]
    phonemes = model.tokenizer.phonemize(request["text"], _lang(voice))
    if request.get("op") == "phonemes":
        return {"ok": True, "phonemes": len(phonemes)}
    if len(phonemes) >= MAX_PHONEMES:
        print(f"kokoro_worker: refused {len(phonemes)} phonemes (limit {MAX_PHONEMES}): would be batched", file=sys.stderr, flush=True)
        raise ValueError(f"{len(phonemes)} phonemes, limit {MAX_PHONEMES}: split it at a clause boundary")
    samples, rate = model.create(request["text"], voice=voice, speed=float(request.get("speed", 1.0)), lang=_lang(voice), trim=False)
    write_wav(request["out"], samples, rate)
    return {"ok": True, "out": request["out"], "phonemes": len(phonemes)}


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
            reply = handle(model, json.loads(line))
        except Exception as exc:                         # noqa: BLE001 — reported to the caller
            reply = {"ok": False, "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}
        print(json.dumps(reply), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
