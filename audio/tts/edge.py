"""Edge TTS backend (moved from video/narrator.py in Phase 3.9-A).

- Requests WordBoundary events (edge-tts >= 7 sends only SentenceBoundary
  unless Communicate(..., boundary="WordBoundary") is passed).
- Bounded retries on transient failures (403 from an outdated client, 5xx, network).
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import re
import time

import edge_tts

import config

RETRY_DELAYS = (2, 5, 10)      # seconds before attempts 2, 3 and 4
_sleep = time.sleep
_upgrade_hint_shown = False


def edge_tts_version() -> str:
    try:
        return importlib.metadata.version("edge-tts")
    except importlib.metadata.PackageNotFoundError:
        return getattr(edge_tts, "__version__", "unknown")


def supports_word_boundary() -> bool:
    """True when this edge-tts accepts Communicate(boundary="WordBoundary")."""
    try:
        return "boundary" in inspect.signature(edge_tts.Communicate.__init__).parameters
    except (TypeError, ValueError):
        return False


def _communicate(text: str, voice: str | None = None, rate: str | None = None, pitch: str | None = None):
    kwargs = {"rate": rate or config.VOICE_RATE, "pitch": pitch or config.VOICE_PITCH}
    if supports_word_boundary():
        kwargs["boundary"] = "WordBoundary"
    return edge_tts.Communicate(text, voice or config.VOICE_ID, **kwargs)


async def _synthesise(text: str, audio_path: str, voice: str | None = None, rate: str | None = None,
                      pitch: str | None = None) -> list[dict]:
    """Stream one scene's audio to ``audio_path``; return WordBoundary events (ms)."""
    audio_chunks = []
    events = []
    async for event in _communicate(text, voice, rate, pitch).stream():
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


def synthesise_scene(text: str, audio_path: str, label: str = "", voice: str | None = None,
                     rate: str | None = None, pitch: str | None = None) -> list[dict]:
    """Synthesise with up to len(RETRY_DELAYS) retries on transient Edge TTS failures."""
    global _upgrade_hint_shown
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            if voice is None and rate is None and pitch is None:
                return asyncio.run(_synthesise(text, audio_path))
            return asyncio.run(_synthesise(text, audio_path, voice, rate, pitch))
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


class EdgeBackend:
    name = "edge"
    audio_ext = "mp3"
    supports_style = False             # edge-tts sends plain text: no per-line styles

    def available(self) -> tuple[bool, str]:
        return True, f"edge-tts {edge_tts_version()} (online, free)"

    def cache_tag(self) -> str:
        return f"edge-tts {edge_tts_version()} wb={supports_word_boundary()}"

    def synthesize(self, text: str, out_path: str, *, voice: str, rate: str, pitch: str,
                   style: str | None = None, label: str = "") -> dict:
        events = synthesise_scene(text, out_path, label, voice, rate, pitch)
        return {"audio": out_path, "words": events, "aligned": False}
