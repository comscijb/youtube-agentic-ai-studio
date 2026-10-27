"""Shared Gemini text-generation client with capability-aware fallback.

The public ``generate(prompt, starting_model=None)`` interface is intentionally
kept stable for the researcher and scriptwriter agents; stage settings
(deadline, temperature, JSON mode, thinking) are optional keyword arguments.
"""
from __future__ import annotations

import collections
import hashlib
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterable

import requests
from google import genai
from google.genai import errors, types

import config

try:
    import httpx
    _NETWORK_ERRORS = (requests.RequestException, httpx.HTTPError)
    _TIMEOUT_ERRORS = (httpx.TimeoutException, requests.Timeout)
except ImportError:  # pragma: no cover - google-genai currently installs httpx
    _NETWORK_ERRORS = (requests.RequestException, OSError)
    _TIMEOUT_ERRORS = (requests.Timeout,)


MODEL_CANDIDATES = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)

RETIRED_MODELS = {
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-1.5-flash",
    "gemini-1.5-flash-001",
    "gemini-1.5-pro",
    "gemini-1.5-pro-001",
}

MODEL_CACHE_TTL_SECONDS = 5 * 60
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 600.0
DEADLINE_RETRY_BASE_DELAY = 2.0
_TRANSIENT_CATEGORIES = {"temporary_server", "network_failure", "deadline_exceeded"}
# Overload spikes last minutes, not seconds: wait on the same model before falling back.
_BACKOFF_CATEGORIES = {"overloaded", "rate_limited"}
DEFAULT_OVERLOAD_RETRIES = 4
DEFAULT_OVERLOAD_BASE_DELAY = 10.0
DEFAULT_OVERLOAD_MAX_DELAY = 90.0
DEFAULT_MAX_CALL_WALLCLOCK = 600.0
_OVERLOAD_PHRASES = ("high demand", "overloaded", "try again later")
_NON_TEXT_MARKERS = (
    "-tts", "live", "image", "embedding", "veo", "imagen", "robotics", "transcribe"
)


@dataclass(frozen=True)
class GeminiErrorInfo:
    category: str
    status: int | None
    retryable: bool
    fallback_allowed: bool
    action: str
    detail: str
    retry_after: float | None = None     # server-suggested wait (Retry-After / RetryInfo)


class GeminiGenerationError(RuntimeError):
    """Sanitized, actionable failure suitable for CLI and GUI display."""

    def __init__(self, info: GeminiErrorInfo, *, model: str = "",
                 fallback_attempted: bool = False, attempted_models: Iterable[str] = ()):
        self.info = info
        self.model = model
        self.fallback_attempted = fallback_attempted
        self.attempted_models = tuple(attempted_models)
        status = str(info.status) if info.status is not None else "n/a"
        attempted = ", ".join(self.attempted_models) or "none"
        message = (
            f"Gemini generation failed: model={model or 'none'}; category={info.category}; "
            f"HTTP={status}; fallback_attempted={'yes' if fallback_attempted else 'no'}; "
            f"attempted_models={attempted}. {info.detail} Next action: {info.action}"
        )
        super().__init__(_sanitize(message))


_client = None
_client_key_hash = ""
_model_cache: list[str] = []
_model_cache_key_hash = ""
_model_cache_expires = 0.0
_lock = threading.RLock()
_call_log: collections.deque = collections.deque(maxlen=500)
_usage = collections.Counter()
_sleep = time.sleep
_clock = time.monotonic


def _configured_key() -> str:
    return str(getattr(config, "GEMINI_API_KEY", "") or "").strip()


def credentials_configured() -> bool:
    key = _configured_key()
    return bool(key and not key.startswith("YOUR_") and "your_" not in key.lower())


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""


def reset_client() -> None:
    """Invalidate the SDK client and model cache after a credential change."""
    global _client, _client_key_hash, _model_cache, _model_cache_key_hash, _model_cache_expires
    with _lock:
        old_client = _client
        _client = None
        _client_key_hash = ""
        _model_cache = []
        _model_cache_key_hash = ""
        _model_cache_expires = 0.0
        close = getattr(old_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _get_client() -> genai.Client:
    global _client, _client_key_hash
    key = _configured_key()
    if not credentials_configured():
        raise _failure(
            "missing_credentials", None,
            "No Gemini API key is configured.",
            "Set GEMINI_API_KEY in the process environment or local .env file.",
        )
    fingerprint = _key_hash(key)
    with _lock:
        if _client is None or _client_key_hash != fingerprint:
            old_client = _client
            try:
                # The client-level timeout only governs short calls (model
                # discovery); generation passes its own per-request deadline.
                # 504 is excluded from SDK retries: repeating an expired
                # deadline unchanged cannot succeed, so generate() retries it
                # with a longer one instead.
                _client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(
                        timeout=int(_discovery_timeout() * 1000),
                        retry_options=types.HttpRetryOptions(
                            attempts=2,
                            initial_delay=0.5,
                            max_delay=2.0,
                            exp_base=2.0,
                            jitter=0.25,
                            # 429/503/504 are handled in generate() with longer,
                            # visible backoff; SDK retries would compound unpredictably.
                            http_status_codes=[408, 500, 502],
                        ),
                    ),
                )
            except Exception as exc:
                info = classify_error(exc, context="client initialization")
                raise GeminiGenerationError(info) from None
            _client_key_hash = fingerprint
            _clear_model_cache_locked()
            close = getattr(old_client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return _client


def _clear_model_cache_locked():
    global _model_cache, _model_cache_key_hash, _model_cache_expires
    _model_cache = []
    _model_cache_key_hash = ""
    _model_cache_expires = 0.0


def discover_models(client=None, *, force: bool = False) -> list[str]:
    """Return accessible text models that explicitly support generateContent."""
    global _model_cache, _model_cache_key_hash, _model_cache_expires
    client = client or _get_client()
    fingerprint = _key_hash(_configured_key())
    with _lock:
        if (not force and _model_cache and _model_cache_key_hash == fingerprint
                and time.monotonic() < _model_cache_expires):
            return list(_model_cache)

    try:
        discovered = []
        for model in client.models.list():
            name = _normalize_model_name(getattr(model, "name", ""))
            actions = {str(action).lower() for action in (getattr(model, "supported_actions", None) or [])}
            if (name and "generatecontent" in actions and _is_text_model(name)
                    and name not in RETIRED_MODELS and name not in discovered):
                discovered.append(name)
    except Exception as exc:
        info = classify_error(exc, context="model discovery")
        raise GeminiGenerationError(info) from None

    if not discovered:
        raise _failure(
            "no_available_models", None,
            "The project returned no accessible text models with generateContent support.",
            "Check the API key's project access in Google AI Studio, then run the diagnostic command.",
        )

    with _lock:
        _model_cache = list(discovered)
        _model_cache_key_hash = fingerprint
        _model_cache_expires = time.monotonic() + MODEL_CACHE_TTL_SECONDS
    return discovered


def build_chain(starting_model: str | None = None,
                available_models: Iterable[str] | None = None) -> list[str]:
    """Order only verified models, preferring the configured candidate."""
    available = []
    for name in (available_models if available_models is not None else MODEL_CANDIDATES):
        normalized = _normalize_model_name(name)
        if normalized and normalized not in RETIRED_MODELS and normalized not in available:
            available.append(normalized)

    preferred = _normalize_model_name(starting_model or "")
    chain = []
    if preferred in available:
        chain.append(preferred)
    for candidate in MODEL_CANDIDATES:
        if candidate in available and candidate not in chain:
            chain.append(candidate)
    for model in available:
        if model not in chain:
            chain.append(model)
    return chain


def generate(prompt: str, starting_model: str | None = None, *,
             stage: str = "generation", temperature: float | None = None,
             response_mime_type: str | None = None,
             max_output_tokens: int | None = None,
             thinking: str | int | None = None,
             timeout_seconds: float | None = None,
             images: list | None = None) -> str:
    """Generate text using discovered models and category-aware fallback.

    ``images`` (optional) are ``(bytes, mime_type)`` pairs sent before the prompt
    (multimodal, e.g. the visual QA of a generated shot).

    ``timeout_seconds`` is the per-request deadline (default
    ``config.GEMINI_TIMEOUT_SECONDS``). A ``deadline_exceeded`` failure is
    retried once on the same model with twice the deadline before falling back.
    """
    if not str(prompt or "").strip():
        raise ValueError("Gemini prompt must not be empty")
    client = _get_client()
    available = discover_models(client)
    preferred = starting_model or getattr(config, "GEMINI_MODEL", MODEL_CANDIDATES[0])
    chain = build_chain(preferred, available)
    if not chain:
        raise _failure(
            "preferred_model_unavailable", 404,
            f"No verified generation model matches the configured preference '{_sanitize(preferred)}'.",
            "Choose one of the models reported by the Gemini diagnostic.",
        )
    base_timeout = _clamp_timeout(
        timeout_seconds if timeout_seconds is not None
        else getattr(config, "GEMINI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    )
    settings = {
        "temperature": 0.9 if temperature is None else float(temperature),
        "response_mime_type": response_mime_type,
        "max_output_tokens": _max_output_tokens(max_output_tokens),
        "thinking_config": _thinking_config(thinking),
    }

    attempted = []
    fallback_attempted = False
    transient_fallbacks = 0
    last_info = None
    last_model = ""
    started = _clock()
    wallclock = float(getattr(config, "GEMINI_MAX_CALL_WALLCLOCK", DEFAULT_MAX_CALL_WALLCLOCK))
    retries = int(getattr(config, "GEMINI_OVERLOAD_RETRIES", DEFAULT_OVERLOAD_RETRIES))

    for model in chain:
        attempted.append(model)
        last_model = model
        if len(attempted) > 1:
            _usage["fallbacks"] += 1
        print(f"   -> Gemini model: {model}")
        deadline = base_timeout
        deadline_retries = 0
        backoff_retries = 0
        while True:
            info, text = _attempt(client, model, prompt, stage, deadline, settings, images)
            if text:
                return text
            if info.category == "deadline_exceeded" and deadline_retries < 1:
                deadline_retries += 1
                deadline = min(deadline * 2, MAX_TIMEOUT_SECONDS)
                delay = DEADLINE_RETRY_BASE_DELAY * (2 ** (deadline_retries - 1))
                delay += random.uniform(0, delay / 2)
                print(f"   WARNING: {model} exceeded its deadline; retrying once with "
                      f"{deadline:g}s after {delay:.1f}s")
                _wait(delay, stage, model, "deadline")
                continue
            if info.category in _BACKOFF_CATEGORIES and backoff_retries < retries:
                delay = _backoff_delay(info, backoff_retries)
                if _clock() - started + delay > wallclock:
                    print(f"   WARNING: {model} still {info.category}; the {wallclock:g}s per-call "
                          "limit (GEMINI_MAX_CALL_WALLCLOCK) leaves no time for another wait")
                    break
                backoff_retries += 1
                reason = "model overloaded" if info.category == "overloaded" else "rate limited"
                print(f"   waiting {delay:.0f}s before retry {backoff_retries}/{retries} ({reason})")
                _wait(delay, stage, model, info.category)
                continue
            break

        last_info = info
        if not info.fallback_allowed:
            _usage["failures"] += 1
            raise GeminiGenerationError(
                info, model=model, fallback_attempted=fallback_attempted,
                attempted_models=attempted,
            ) from None

        remaining = len(chain) - len(attempted)
        if remaining <= 0:
            break
        if _clock() - started >= wallclock:
            break
        if info.category in _TRANSIENT_CATEGORIES:
            if transient_fallbacks >= 1:
                break
            transient_fallbacks += 1
        fallback_attempted = True
        print(f"   WARNING: {model} failed ({info.category}); trying a verified fallback model")

    _usage["failures"] += 1
    raise GeminiGenerationError(
        last_info or GeminiErrorInfo(
            "provider_unavailable", None, False, False,
            "Run the Gemini diagnostic and verify project access.",
            "All verified Gemini models were unavailable.",
        ),
        model=last_model,
        fallback_attempted=fallback_attempted,
        attempted_models=attempted,
    )


def _attempt(client, model: str, prompt: str, stage: str, deadline: float,
             settings: dict, images: list | None = None) -> tuple[GeminiErrorInfo | None, str]:
    """Run one request; return ``(None, text)`` or ``(failure info, "")``."""
    started = time.monotonic()
    info = None
    text = ""
    seconds = int(round(deadline))
    try:
        contents = prompt if not images else [
            *(types.Part.from_bytes(data=data, mime_type=mime) for data, mime in images), prompt]
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                # The explicit header overrides any deadline left in the
                # client's shared headers by an earlier default-timeout call.
                http_options=types.HttpOptions(
                    timeout=seconds * 1000,
                    headers={"X-Server-Timeout": str(seconds)},
                ),
                **{key: value for key, value in settings.items() if value is not None},
            ),
        )
        text = _response_text(response)
        if not text:
            blocked_reason = _blocked_reason(response)
            if blocked_reason:
                info = GeminiErrorInfo(
                    "blocked_response", None, False, False,
                    "Revise the prompt or review the project's safety policy.",
                    f"Generation was blocked ({blocked_reason}).",
                )
            else:
                info = GeminiErrorInfo(
                    "empty_response", None, False, True,
                    "Retry later or inspect the response in the diagnostic command.",
                    "The model returned no text content.",
                )
    except GeminiGenerationError:
        raise
    except Exception as exc:
        info = classify_error(exc, context="generation")
    _record_call(stage, model, len(prompt), time.monotonic() - started, seconds,
                 info.category if info else "success")
    return info, text


def _backoff_delay(info: GeminiErrorInfo, attempt: int) -> float:
    """Exponential backoff with jitter (~10/20/40/80 s), or the server's own delay."""
    base = float(getattr(config, "GEMINI_OVERLOAD_BASE_DELAY", DEFAULT_OVERLOAD_BASE_DELAY))
    cap = float(getattr(config, "GEMINI_OVERLOAD_MAX_DELAY", DEFAULT_OVERLOAD_MAX_DELAY))
    if info.retry_after:
        return min(max(float(info.retry_after), 1.0), max(cap, float(info.retry_after)))
    delay = min(cap, base * (2 ** attempt))
    return min(cap, delay + random.uniform(0, delay * 0.1))


def _wait(seconds: float, stage: str, model: str, reason: str) -> None:
    """Sleep between retries (Ctrl+C interrupts it) and account for it."""
    _usage["retries"] += 1
    _usage["wait_seconds"] += seconds
    print(f"   [gemini] stage={stage} model={model} wait={seconds:.0f}s reason={reason}")
    _sleep(seconds)


def usage_summary() -> dict:
    with _lock:
        calls = len(_call_log)
    return {"calls": calls, "retries": int(_usage["retries"]),
            "wait_seconds": round(float(_usage["wait_seconds"]), 1),
            "fallbacks": int(_usage["fallbacks"]), "failures": int(_usage["failures"])}


def stage_summary(prefix: str) -> dict:
    """Calls and seconds spent in stages starting with ``prefix`` (e.g. "visual_qa")."""
    with _lock:
        records = [record for record in _call_log if str(record["stage"]).startswith(prefix)]
    return {"calls": len(records), "seconds": round(sum(record["elapsed_seconds"] for record in records), 1)}


def usage_line() -> str:
    summary = usage_summary()
    line = (f"Gemini usage: {summary['calls']} calls, {summary['retries']} retries, "
            f"{summary['wait_seconds']:.0f}s waiting, {summary['fallbacks']} fallbacks, "
            f"{summary['failures']} failures")
    qa = stage_summary("visual_qa")
    if qa["calls"]:
        line += f" (visual QA: {qa['calls']} calls, {qa['seconds']:.0f}s)"
    return line


def _record_call(stage: str, model: str, prompt_chars: int, elapsed: float,
                 deadline: int, outcome: str) -> None:
    record = {
        "stage": stage, "model": model, "prompt_chars": prompt_chars,
        "elapsed_seconds": round(elapsed, 2), "deadline_seconds": deadline,
        "outcome": outcome,
    }
    with _lock:
        _call_log.append(record)
    # Sizes and timings only: prompt content is never logged.
    print(f"   [gemini] stage={stage} model={model} prompt_chars={prompt_chars} "
          f"deadline={deadline}s elapsed={elapsed:.1f}s outcome={outcome}")


def call_records() -> list[dict]:
    """Return copies of recent per-call timing records (no prompt content)."""
    with _lock:
        return [dict(record) for record in _call_log]


def clear_call_records() -> None:
    """Reset per-run call records and usage counters."""
    with _lock:
        _call_log.clear()
        _usage.clear()


def _clamp_timeout(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(seconds, MAX_TIMEOUT_SECONDS))


def _discovery_timeout() -> float:
    return _clamp_timeout(getattr(
        config, "GEMINI_DISCOVERY_TIMEOUT_SECONDS", DEFAULT_DISCOVERY_TIMEOUT_SECONDS
    ))


def _max_output_tokens(value) -> int | None:
    if value is None:
        value = getattr(config, "GEMINI_MAX_OUTPUT_TOKENS", 0)
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return None
    return tokens if tokens > 0 else None


def _thinking_config(value) -> types.ThinkingConfig | None:
    """Map a level name or an integer token budget onto the SDK ThinkingConfig."""
    if value is None:
        value = getattr(config, "GEMINI_THINKING_LEVEL", "")
    if isinstance(value, bool):
        raise ValueError("Gemini thinking setting must be a level name or a token budget")
    if isinstance(value, int):
        return types.ThinkingConfig(thinking_budget=value)
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+", text):
        return types.ThinkingConfig(thinking_budget=int(text))
    level = text.upper()
    if level not in {"MINIMAL", "LOW", "MEDIUM", "HIGH"}:
        raise ValueError(
            f"Unsupported GEMINI_THINKING_LEVEL '{text}'; "
            "use minimal, low, medium, high or an integer token budget"
        )
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel[level])


def classify_error(exc: Exception, *, context: str = "generation") -> GeminiErrorInfo:
    """Classify SDK errors using structured status codes, never string routing."""
    if isinstance(exc, GeminiGenerationError):
        return exc.info
    if isinstance(exc, errors.APIError):
        code = _status_code(getattr(exc, "code", None))
        detail = _sanitize(getattr(exc, "message", "") or "Gemini API request failed.")
        if code == 400:
            return GeminiErrorInfo("invalid_request", code, False, False,
                                   "Check the selected model and request configuration.", detail)
        if code == 401:
            return GeminiErrorInfo("invalid_credentials", code, False, False,
                                   "Replace the Gemini API key in Settings or .env.", detail)
        if code == 403:
            return GeminiErrorInfo("permission_denied", code, False, False,
                                   "Verify that the key's project can use the Gemini API and selected model.", detail)
        if code == 404:
            return GeminiErrorInfo("model_unavailable", code, False, True,
                                   "Refresh available models or choose another accessible model.", detail)
        if code == 429:
            return GeminiErrorInfo("rate_limited", code, True, True,
                                   "Review current project rate limits/billing or retry after the reported reset.",
                                   detail, _retry_after(exc))
        status = str(getattr(exc, "status", "") or "").upper()
        if code == 503 or status == "UNAVAILABLE" or any(
                phrase in detail.lower() for phrase in _OVERLOAD_PHRASES) and code in {500, 503}:
            return GeminiErrorInfo("overloaded", code, True, True,
                                   "The model is under high demand; retry in a few minutes.",
                                   detail, _retry_after(exc))
        if code == 504 or str(getattr(exc, "status", "") or "").upper() == "DEADLINE_EXCEEDED":
            return GeminiErrorInfo("deadline_exceeded", code, True, True,
                                   "Raise GEMINI_TIMEOUT_SECONDS or retry later if the fallback also fails.",
                                   detail)
        if code in {408, 500, 502}:
            return GeminiErrorInfo("temporary_server", code, True, True,
                                   "Retry later if the verified fallback also fails.", detail)
        return GeminiErrorInfo("api_error", code, False, False,
                               "Review the Gemini diagnostic output and API configuration.", detail)
    if isinstance(exc, _TIMEOUT_ERRORS):
        return GeminiErrorInfo(
            "deadline_exceeded", None, True, True,
            "Raise GEMINI_TIMEOUT_SECONDS or retry later if the fallback also fails.",
            f"Gemini {context} did not finish before the request deadline.",
        )
    if isinstance(exc, _NETWORK_ERRORS) or isinstance(exc, (TimeoutError, ConnectionError)):
        return GeminiErrorInfo(
            "network_failure", None, True, False,
            "Check network connectivity, proxy/firewall settings, then retry.",
            f"Gemini {context} could not reach the API.",
        )
    return GeminiErrorInfo(
        "client_failure", None, False, False,
        "Update google-genai if necessary and inspect the sanitized diagnostic output.",
        f"Unexpected Gemini SDK failure during {context}: {exc.__class__.__name__}.",
    )


def _retry_after(exc) -> float | None:
    """Seconds from a Retry-After header or a google.rpc.RetryInfo detail."""
    response = getattr(exc, "response", None)
    header = None
    try:
        header = response.headers.get("Retry-After") if response is not None else None
    except AttributeError:
        header = None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    details = getattr(exc, "details", None)
    error = details.get("error", details) if isinstance(details, dict) else {}
    for item in (error.get("details") or []) if isinstance(error, dict) else []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", str(delay or ""))
        if match:
            return float(match.group(1))
    return None


def diagnostic_snapshot(*, run_generation: bool = True) -> dict:
    """Perform an explicitly invoked, low-cost connectivity diagnostic."""
    result = {
        "credentials_configured": credentials_configured(),
        "credential_source": getattr(config, "GEMINI_API_KEY_SOURCE", "unknown"),
        "connectivity": False,
        "available_models": [],
        "selected_model": None,
        "generation": "not_run",
    }
    if not result["credentials_configured"]:
        result["error"] = str(_failure(
            "missing_credentials", None, "No Gemini API key is configured.",
            "Set GEMINI_API_KEY and run this diagnostic again.",
        ))
        return result
    try:
        client = _get_client()
        models = discover_models(client, force=True)
        chain = build_chain(getattr(config, "GEMINI_MODEL", None), models)
        result.update({
            "connectivity": True,
            "available_models": models,
            "selected_model": chain[0] if chain else None,
        })
        if run_generation and chain:
            text = generate("Reply with exactly OK.", starting_model=chain[0],
                            stage="diagnostic", timeout_seconds=_discovery_timeout())
            result["generation"] = "success" if text else "empty"
    except Exception as exc:
        result["error"] = _sanitize(str(exc))
        result["generation"] = "failed" if run_generation else "not_run"
    return result


def _response_text(response) -> str:
    try:
        return str(getattr(response, "text", "") or "").strip()
    except (ValueError, AttributeError):
        return ""


def _blocked_reason(response) -> str:
    feedback = getattr(response, "prompt_feedback", None)
    reason = getattr(feedback, "block_reason", None)
    if reason:
        reason_name = str(getattr(reason, "name", reason))
        if reason_name.upper() not in {"UNSPECIFIED", "BLOCK_REASON_UNSPECIFIED", "NONE"}:
            return reason_name
    for candidate in (getattr(response, "candidates", None) or []):
        finish = getattr(candidate, "finish_reason", None)
        finish_name = str(getattr(finish, "name", finish) or "")
        if finish_name.upper() in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION"}:
            return finish_name
    return ""


def _normalize_model_name(value) -> str:
    name = str(value or "").strip()
    for prefix in ("models/", "publishers/google/models/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def _is_text_model(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("gemini-") and not any(marker in lowered for marker in _NON_TEXT_MARKERS)


def _status_code(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sanitize(value) -> str:
    text = str(value or "")
    key = _configured_key()
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(?i)([?&](?:key|api_key|token)=)[^&\s]+", r"\1[REDACTED]", text)
    return text.replace("\n", " ").strip()


def _failure(category: str, status: int | None, detail: str, action: str) -> GeminiGenerationError:
    return GeminiGenerationError(GeminiErrorInfo(category, status, False, False, action, detail))
