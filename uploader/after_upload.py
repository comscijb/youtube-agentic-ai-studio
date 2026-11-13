"""The "Depois do envio" checklist (Phase 4.0-E) and the Community drafts it shows (Phase 4.0-G).

The API can't set end screens, cards, the Shorts "related video" or auto-dubbing, so they become a checklist the
user can't miss, with ready-to-copy values:

* **end screen** — the latest other episode of the same type (ID and title) plus subscribe;
* **card** at the curiosity peak — by default the start of the reversal;
* **related video** of each Short — its episode;
* **pinned comment** — the text from the description parts;
* **auto-dubbing** — switched on in Studio → Subtitles;
* **playlist** — added automatically (``playlistItems.insert``) to the type's playlist from Settings;
* **Community drafts** (G) — a poll from the dilemma and a teaser for the day before; never posted (no API).

Ticks are stored in the ledger record (``after_upload``); Desempenho lists the unticked items of videos older than
24 h.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.narrative import load_json

ITEMS = ("end_screen", "card", "related_video", "pinned_comment", "auto_dubbing", "playlist", "community_poll",
         "community_teaser")
LABELS = {"end_screen": "Tela final", "card": "Card no pico de curiosidade", "related_video": "Vídeo relacionado dos Shorts",
          "pinned_comment": "Comentário fixado", "auto_dubbing": "Dublagem automática", "playlist": "Playlist do tipo",
          "community_poll": "Enquete na Comunidade", "community_teaser": "Post de teaser (véspera)"}
OVERDUE_HOURS = 24
POLL_QUESTIONS = {"philosophical_thought_experiment": "What would you do?", "realistic_psychological_fiction": "Who was right?",
                  "dark_human_parable": "Which part of this village are you?"}


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _project(slug: str, name: str) -> dict:
    from uploader.packaging_swap import project_file
    found = project_file(slug or "", name)
    return (load_json(found) or {}) if found else {}


def narrative_type(record: dict) -> str:
    if record.get("narrative_type"):
        return record["narrative_type"]
    script = _project(record.get("project"), "script.json")
    return script.get("narrative_type") or _project(record.get("project"), "story_brief.json").get("narrative_type", "")


def episodes(ledger: dict) -> list[dict]:
    return [item for item in ledger.get("publications", []) if item.get("video_id") and not item.get("parent_video_id")
            and item.get("kind", "episode") != "short" and item.get("state") in ("published", "uploaded_pending_publication")]


def end_screen_target(record: dict, ledger: dict) -> dict | None:
    """The latest other episode of the same type (the next one does not exist yet), else the latest other episode."""
    others = [item for item in episodes(ledger) if item["video_id"] != record["video_id"]]
    others.sort(key=lambda item: str(item.get("published_at") or item.get("uploaded_at") or ""), reverse=True)
    kind = narrative_type(record)
    same = [item for item in others if narrative_type(item) == kind] if kind else []
    chosen = (same or others or [None])[0]
    return {"video_id": chosen["video_id"], "title": chosen.get("title", ""), "same_type": bool(same)} if chosen else None


def curiosity_peak(record: dict) -> dict:
    """The reversal's start time (scene timeline + script section types); 60 % of the video without a reversal."""
    timeline = _project(record.get("project"), "scene_timeline.json")
    script = _project(record.get("project"), "script.json")
    sections = [item for item in script.get("sections", []) if str(item.get("narration", "")).strip()] or script.get("sections", [])
    scenes = timeline.get("scenes") or []
    for index, section in enumerate(sections):
        if section.get("section_type") == "reversal" and index < len(scenes):
            return {"seconds": float(scenes[index]["start"]), "at": _clock(scenes[index]["start"]), "source": "reversal",
                    "scene": scenes[index].get("title", "")}
    total = float(timeline.get("total_duration") or 0)
    return {"seconds": round(total * 0.6, 1), "at": _clock(total * 0.6), "source": "60 %", "scene": ""}


def add_to_playlist(service, video_id: str, playlist_id: str) -> dict:
    """playlistItems.insert (youtube.force-ssl scope)."""
    response = service.playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    return {"action": "added", "playlist_id": playlist_id, "id": response.get("id")}


def playlist_for(kind: str) -> str:
    from uploader import discovery_settings
    return str((discovery_settings.get("playlists") or {}).get(kind) or "")


def add_episode_to_playlist(service, video_id: str, kind: str) -> dict:
    """After an upload: the type's playlist from Settings; never fails the upload."""
    playlist = playlist_for(kind)
    if not playlist:
        return {"action": "not_configured", "type": kind}
    try:
        return add_to_playlist(service, video_id, playlist)
    except Exception as exc:                             # noqa: BLE001
        return {"action": "failed", "playlist_id": playlist, "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}


# ── Community drafts (Phase 4.0-G) ───────────────────────────────────────────

def community_drafts(record: dict, *, generator=None) -> dict:
    """A poll from the episode's dilemma (2-4 options) and a teaser for the day before; cached in the project
    as community_drafts.json when Gemini writes them, else a plain fallback. Never posted."""
    from uploader.packaging_swap import project_file
    cached = _project(record.get("project"), "community_drafts.json")
    if cached.get("poll"):
        return cached
    kind = narrative_type(record)
    brief = _project(record.get("project"), "story_brief.json")
    question = POLL_QUESTIONS.get(kind, "What would you do?")
    drafts = None
    if generator is not None:
        try:
            drafts = generate_drafts(record, brief, kind, generator=generator)
        except Exception:                                # noqa: BLE001 — the fallback below still helps
            drafts = None
    if drafts is None:
        dilemma = str(brief.get("central_dramatic_question") or brief.get("philosophical_question") or "").strip()
        drafts = {"poll": {"question": f"{question} {dilemma}".strip()[:120],
                           "options": ["I'd do the same", "I'd refuse", "I'm not sure"]},
                  "teaser": {"text": str(brief.get("logline") or record.get("title", "")).strip()[:200] +
                             " Tomorrow on Private Gravity.", "image": "thumbnail"}, "source": "fallback"}
    folder = project_file(record.get("project") or "", "script.json")
    if folder and drafts.get("source") != "fallback":
        from agents.narrative import atomic_write_json
        atomic_write_json(folder.parent / "community_drafts.json", drafts)
    return drafts


def drafts_contract(question: str) -> str:
    return f"""You write two Community-tab DRAFTS for one Private Gravity episode (a person posts them by hand).
- "poll": {{"question": starts with "{question}" and names the episode's dilemma in plain words (max 120
  characters), "options": 2-4 short answers (max 65 characters each), none of them a spoiler}}
- "teaser": {{"text": one or two lines for the day before the episode, posted with the thumbnail; no spoiler,
  no ending, no question bait, max 200 characters}}
Return ONLY JSON {{"poll": {{...}}, "teaser": {{...}}}}."""


def validate_drafts(value: dict, question: str) -> dict:
    from agents.narrative import NarrativeValidationError
    poll = (value or {}).get("poll") or {}
    teaser = (value or {}).get("teaser") or {}
    options = [str(item).strip() for item in poll.get("options") or [] if str(item).strip()]
    problems = []
    if not str(poll.get("question", "")).startswith(question.rstrip("?").split()[0]) or len(str(poll.get("question", ""))) > 120:
        problems.append(f"poll question must start like \"{question}\" and stay within 120 characters")
    if not 2 <= len(options) <= 4 or any(len(item) > 65 for item in options):
        problems.append("poll needs 2-4 options of at most 65 characters")
    if not str(teaser.get("text", "")).strip() or len(str(teaser.get("text", ""))) > 200:
        problems.append("teaser text must be 1-200 characters")
    if problems:
        raise NarrativeValidationError("community drafts: " + "; ".join(problems))
    return {"poll": {"question": str(poll["question"]).strip(), "options": options},
            "teaser": {"text": str(teaser["text"]).strip(), "image": "thumbnail"}, "source": "gemini"}


def generate_drafts(record: dict, brief: dict, kind: str, *, generator) -> dict:
    from agents.structured import compact_json, generate_json
    question = POLL_QUESTIONS.get(kind, "What would you do?")
    story = {key: brief.get(key, "") for key in ("logline", "central_dramatic_question", "philosophical_question")}
    story["title"] = record.get("title", "")
    return generate_json(drafts_contract(question), "community drafts", lambda value: validate_drafts(value, question),
                         generator=generator, context=f"EPISODE: {compact_json(story)}", stage="community_drafts",
                         temperature_key="packaging")


# ── The checklist ───────────────────────────────────────────────────────────

def checklist(video_id: str, *, ledger: dict | None = None, generator=None) -> dict:
    """Every item with its ready-to-copy value and whether it is ticked."""
    from uploader import localize, publications
    ledger = ledger or publications.load_ledger()
    record = next((item for item in ledger.get("publications", []) if item.get("video_id") == video_id), None)
    if record is None:
        raise LookupError(f"{video_id} is not in the ledger")
    ticks = record.get("after_upload") or {}
    target = end_screen_target(record, ledger)
    peak = curiosity_peak(record)
    shorts = [item for item in ledger.get("publications", []) if item.get("parent_video_id") == video_id]
    parts = _project(record.get("project"), "description_parts.json")
    kind = narrative_type(record)
    playlist = record.get("playlist") or {"action": "not_configured" if not playlist_for(kind) else "pending"}
    drafts = community_drafts(record, generator=generator)
    url = f"https://www.youtube.com/watch?v={video_id}"
    values = {
        "end_screen": (f"Elemento 1: vídeo {target['video_id']} — \"{target['title']}\""
                       f"{'' if target['same_type'] else ' (outro tipo: ainda não há episódio do mesmo tipo)'}. "
                       "Elemento 2: botão Inscrever-se.") if target else "Elemento: botão Inscrever-se (ainda não há outro episódio).",
        "card": f"Card aos {peak['at']} ({'início da virada' if peak['source'] == 'reversal' else '60 % do vídeo'}"
                f"{': ' + peak['scene'] if peak['scene'] else ''}), apontando para "
                + (f"\"{target['title']}\" ({target['video_id']})" if target else "a playlist do tipo"),
        "related_video": "\n".join(f"{short.get('title', '')} ({short['video_id']}) → vídeo relacionado: {url}" for short in shorts)
                         or "Sem Shorts deste episódio.",
        "pinned_comment": parts.get("pinned_comment", "") or "(sem texto de comentário fixado no projeto)",
        "auto_dubbing": localize.DUBBING_REMINDER,
        "playlist": {"added": f"Adicionado à playlist {playlist.get('playlist_id', '')}.",
                     "not_configured": "Configure os IDs das 3 playlists em Configurações (uma vez).",
                     "failed": f"Não foi possível adicionar ({playlist.get('error', '')}); adicione à mão."}.get(
                         playlist.get("action"), "Será adicionado no próximo envio."),
        "community_poll": f"{drafts['poll']['question']}\n" + "\n".join(f"○ {item}" for item in drafts["poll"]["options"]),
        "community_teaser": drafts["teaser"]["text"] + "\n(publicar com a miniatura, na véspera do episódio)",
    }
    items = [{"id": key, "label": LABELS[key], "value": values[key], "ticked_at": ticks.get(key)} for key in ITEMS]
    if playlist.get("action") == "added" and not ticks.get("playlist"):
        items[ITEMS.index("playlist")]["ticked_at"] = playlist.get("at") or "auto"
    return {"video_id": video_id, "title": record.get("title", ""), "url": url, "items": items,
            "end_screen": target, "card": peak, "drafts": drafts}


def tick(video_id: str, item: str, ticked: bool = True, *, now: datetime | None = None) -> dict:
    from uploader import publications
    if item not in ITEMS:
        raise ValueError(f"unknown checklist item: {item}")

    def change(record):
        ticks = record.setdefault("after_upload", {})
        if ticked:
            ticks[item] = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
        else:
            ticks.pop(item, None)
    return publications.update_record(video_id, change)


def overdue(*, ledger: dict | None = None, now: datetime | None = None) -> list[dict]:
    """Episodes older than 24 h with unticked items (for Desempenho)."""
    from uploader import publications
    ledger = ledger or publications.load_ledger()
    now = now or datetime.now(timezone.utc)
    found = []
    for record in episodes(ledger):
        stamp = record.get("published_at") or record.get("uploaded_at")
        try:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if now - when < timedelta(hours=OVERDUE_HOURS):
            continue
        ticks = record.get("after_upload") or {}
        auto = {"playlist"} if (record.get("playlist") or {}).get("action") == "added" else set()
        missing = [LABELS[key] for key in ITEMS if key not in ticks and key not in auto]
        if missing:
            found.append({"video_id": record["video_id"], "title": record.get("title", ""), "missing": missing})
    return found
