"""Shared Gemini text-generation client with capability-aware fallback.

The public ``generate(prompt, starting_model=None)`` interface is intentionally
kept stable for the researcher and scriptwriter agents.
"""
from __future__ import annotations

import hashlib
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
except ImportError:  # pragma: no cover - google-genai currently installs httpx
    _NETWORK_ERRORS = (requests.RequestException, OSError)


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
                _client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(
                        timeout=30_000,
                        retry_options=types.HttpRetryOptions(
                            attempts=2,
                            initial_delay=0.5,
                            max_delay=2.0,
                            exp_base=2.0,
                            jitter=0.25,
                            http_status_codes=[408, 429, 500, 502, 503, 504],
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


def generate(prompt: str, starting_model: str | None = None) -> str:
    """Generate text using discovered models and category-aware fallback."""
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

    attempted = []
    fallback_attempted = False
    transient_fallbacks = 0
    last_info = None
    last_model = ""

    for model in chain:
        attempted.append(model)
        last_model = model
        print(f"   -> Gemini model: {model}")
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.9),
            )
            text = _response_text(response)
            if text:
                return text
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

        last_info = info
        if not info.fallback_allowed:
            raise GeminiGenerationError(
                info, model=model, fallback_attempted=fallback_attempted,
                attempted_models=attempted,
            ) from None

        remaining = len(chain) - len(attempted)
        if remaining <= 0:
            break
        if info.category in {"temporary_server", "network_failure"}:
            if transient_fallbacks >= 1:
                break
            transient_fallbacks += 1
        fallback_attempted = True
        print(f"   WARNING: {model} failed ({info.category}); trying a verified fallback model")

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
            return GeminiErrorInfo("quota_exhausted", code, False, True,
                                   "Review current project rate limits/billing or retry after the reported reset.", detail)
        if code in {408, 500, 502, 503, 504}:
            return GeminiErrorInfo("temporary_server", code, True, True,
                                   "Retry later if the verified fallback also fails.", detail)
        return GeminiErrorInfo("api_error", code, False, False,
                               "Review the Gemini diagnostic output and API configuration.", detail)
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
            text = generate("Reply with exactly OK.", starting_model=chain[0])
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
