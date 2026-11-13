"""Localized titles and descriptions (Phase 4.0-B).

At upload, Gemini translates the title and the description into each of ``LOCALIZE_LANGUAGES`` (Settings) at low
temperature, contract first; ``validate`` keeps what YouTube and the viewer need:

* the title within 100 characters, ``#Shorts`` kept for a Short;
* every chapter timestamp unchanged and in order (the chapter titles are translated);
* no line left in English except proper names, URLs and hashtags;
* never a new claim: the translation has as many lines as the source, give or take the blank ones.

The result goes into ``snippet.defaultLanguage = "en"`` plus the ``localizations`` object (videos.insert/update with
``part=snippet,localizations``). ``localizations.json`` in the run folder holds them for the review gate's
"Traduções" panel, editable per language, Portuguese first. Auto-dubbing has no API: the post-upload summary
reminds the user to switch it on in Studio → Subtitles and to review the Portuguese track.
"""
from __future__ import annotations

import re
from pathlib import Path

import config
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json

FILE_NAME = "localizations.json"
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
NAMES = {"es": "espanhol", "pt": "português", "fr": "francês", "de": "alemão", "hi": "hindi", "it": "italiano",
         "ja": "japonês", "ko": "coreano", "id": "indonésio", "ru": "russo", "tr": "turco", "ar": "árabe"}
TIMESTAMP = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2})\b")
ENGLISH = {"the", "and", "you", "with", "for", "this", "that", "who", "what", "when", "his", "her", "they", "is",
           "was", "are", "of", "to", "in", "it", "on", "be", "have", "from", "every", "would", "tell", "us", "about",
           "comments", "share", "like", "subscribe", "more", "stories", "someone", "video", "episode", "full"}
DUBBING_REMINDER = ("Dublagem automática: no YouTube Studio → Legendas, ative a dublagem automática deste vídeo "
                    "(a API não faz isso) e revise a faixa em português antes de publicar.")


def languages() -> list[str]:
    from uploader import discovery_settings
    return [code for code in discovery_settings.get("localize_languages") if code != getattr(config, "VIDEO_LANGUAGE", "en")]


def ordered(codes) -> list[str]:
    """Portuguese first (the user reviews it), then the others in their order."""
    codes = list(codes)
    return (["pt"] if "pt" in codes else []) + [code for code in codes if code != "pt"]


def contract(codes: list[str], is_short: bool) -> str:
    names = ", ".join(codes)
    return f"""You translate one YouTube video's title and description from English into: {names}.
Rules:
- keep the meaning and the hook; never add a claim, a promise or a detail that is not in the English
- the title stays within {TITLE_MAX} characters{', and keeps "#Shorts"' if is_short else ''}
- keep every chapter timestamp exactly as it is (e.g. "1:16"), at the start of its line, in the same order;
  translate the chapter title after it
- keep proper names, URLs, "@handles" and hashtags unchanged; translate everything else, including the
  call to action
- keep the same line structure (one translated line per English line)
- natural, idiomatic wording a native viewer would use; no transliteration
Return ONLY JSON {{"translations": {{"<code>": {{"title": "...", "description": "..."}}, ...}}}} with every code."""


def timestamps(text: str) -> list[str]:
    return [match.group(1) for line in str(text or "").splitlines() if (match := TIMESTAMP.match(line))]


def _untranslated(line: str, source_lines: set[str]) -> bool:
    """A line copied from the English that reads as English (not a name, URL, hashtag or timestamp line)."""
    clean = re.sub(r"https?://\S+|(?<!\S)[#@]\w+|^\s*(?:\d+:)?\d{1,2}:\d{2}", "", line).strip()
    if not clean or line.strip() not in source_lines:
        return False
    words = re.findall(r"[a-z']+", clean.lower())
    return len(words) >= 3 and sum(word in ENGLISH for word in words) >= 2


def problems(code: str, item: dict, title: str, description: str, *, is_short: bool) -> list[str]:
    found = []
    translated_title = str((item or {}).get("title", "")).strip()
    translated = str((item or {}).get("description", "")).strip()
    if not translated_title or len(translated_title) > TITLE_MAX:
        found.append(f"{code}: o título precisa ter 1-{TITLE_MAX} caracteres")
    if is_short and "#shorts" in title.lower() and "#shorts" not in translated_title.lower() + translated.lower():
        found.append(f"{code}: #Shorts sumiu")
    if description.strip() and not translated:
        found.append(f"{code}: descrição vazia")
    if len(translated) > DESCRIPTION_MAX:
        found.append(f"{code}: descrição com mais de {DESCRIPTION_MAX} caracteres")
    if timestamps(translated) != timestamps(description):
        found.append(f"{code}: os horários dos capítulos mudaram")
    source = {line.strip() for line in description.splitlines() if line.strip()}
    copied = [line for line in translated.splitlines() if _untranslated(line, source)]
    if code != "en" and (copied or (translated_title == title.strip() and len(title.split()) > 3)):
        found.append(f"{code}: ficou texto em inglês sem traduzir ({(copied or [translated_title])[0][:60]!r})")
    source_count = sum(1 for line in description.splitlines() if line.strip())
    count = sum(1 for line in translated.splitlines() if line.strip())
    if source_count and abs(count - source_count) > max(2, source_count // 5):
        found.append(f"{code}: {count} linhas para {source_count} no original (algo foi acrescentado ou cortado)")
    return found


def validate(value: dict, codes: list[str], title: str, description: str, *, is_short: bool = False) -> dict:
    translations = (value or {}).get("translations") if isinstance(value, dict) else None
    if not isinstance(translations, dict):
        raise NarrativeValidationError("translations must be an object keyed by language code")
    found, clean = [], {}
    for code in codes:
        item = translations.get(code)
        if not isinstance(item, dict):
            found.append(f"{code}: missing")
            continue
        found += problems(code, item, title, description, is_short=is_short)
        clean[code] = {"title": str(item.get("title", "")).strip(), "description": str(item.get("description", "")).strip()}
    if found:
        raise NarrativeValidationError("translations: " + "; ".join(found))
    return clean


def translate(title: str, description: str, codes=None, *, is_short: bool = False, generator=None, run_dir=None) -> dict:
    """{code: {title, description}} for every language, validated (one Gemini call)."""
    from agents.gemini_client import generate
    from agents.structured import compact_json, generate_json
    codes = list(codes if codes is not None else languages())
    if not codes:
        return {}
    return generate_json(contract(codes, is_short), "translations",
                         lambda value: validate(value, codes, title, description, is_short=is_short),
                         generator=generator or generate, context=f"ENGLISH: {compact_json({'title': title, 'description': description})}",
                         stage="localize", temperature_key="translation", run_dir=str(run_dir) if run_dir else None)


def payload(translations: dict) -> dict:
    """The ``localizations`` object YouTube expects."""
    return {code: {"title": item["title"], "description": item["description"]} for code, item in translations.items()
            if item.get("title")}


# ── The run folder's translations (review gate) ──────────────────────────────

def load(run_dir) -> dict:
    return load_json(Path(run_dir) / FILE_NAME) or {}


def current(run_dir, title: str, description: str) -> dict | None:
    """The saved translations when they were made from this exact title and description."""
    saved = load(run_dir)
    if saved.get("source") == {"title": title, "description": description}:
        return saved.get("translations") or {}
    return None


def ensure(run_dir, title: str, description: str, *, generator=None, is_short: bool = False) -> dict:
    found = current(run_dir, title, description)
    if found is not None and set(found) >= set(languages()):
        return found
    translations = translate(title, description, is_short=is_short, generator=generator, run_dir=run_dir)
    save(run_dir, title, description, translations, edited=False)
    return translations


def save(run_dir, title: str, description: str, translations: dict, *, edited: bool = True) -> dict:
    """Store translations (an edit in the panel is validated the same way, language by language)."""
    found = []
    for code, item in translations.items():
        found += problems(code, item, title, description, is_short="#shorts" in title.lower())
    if found and edited:
        raise ValueError("; ".join(found))
    record = {"source": {"title": title, "description": description}, "translations": translations,
              "languages": ordered(translations), "edited": edited}
    atomic_write_json(Path(run_dir) / FILE_NAME, record)
    return record


def update_video(video_id: str, translations: dict, *, service) -> dict:
    """Add the translations to a published video: defaultLanguage stays "en", the snippet is sent back unchanged."""
    items = service.videos().list(part="snippet,localizations", id=video_id).execute().get("items", [])
    if not items:
        raise ValueError(f"video {video_id} not found on this channel")
    current_snippet = items[0]["snippet"]
    snippet = {"title": current_snippet["title"], "description": current_snippet.get("description", ""),
               "categoryId": current_snippet.get("categoryId", config.VIDEO_CATEGORY_ID),
               "tags": current_snippet.get("tags", []), "defaultLanguage": getattr(config, "VIDEO_LANGUAGE", "en")}
    if current_snippet.get("defaultAudioLanguage"):
        snippet["defaultAudioLanguage"] = current_snippet["defaultAudioLanguage"]
    localizations = {**(items[0].get("localizations") or {}), **payload(translations)}
    body = {"id": video_id, "snippet": snippet, "localizations": localizations}
    service.videos().update(part="snippet,localizations", body=body).execute()
    return body
