"""
YouTube Uploader
The single upload path used by both the GUI and pipeline.py: OAuth, video
insert (optionally scheduled with publishAt), thumbnail, progress reporting,
and the publication-ledger record written right after a successful upload.
"""
import os
import pickle
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google_auth_oauthlib.flow import InstalledAppFlow

import config


TOKEN_FILE = "youtube_token.pickle"


class YouTubeAuthError(RuntimeError):
    """Credentials are missing or lack a scope and interactive consent is not allowed."""


def _required_scopes() -> list[str]:
    return list(getattr(config, "YOUTUBE_SCOPES", []))


def _missing_scopes(creds) -> list[str]:
    required = _required_scopes()
    try:
        if creds.has_scopes(required):
            return []
    except Exception:
        pass
    granted = set(getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None) or [])
    return [scope for scope in required if scope not in granted]


def load_credentials(interactive: bool = True):
    """Return valid OAuth credentials carrying every scope in YOUTUBE_SCOPES.

    A stored token that lacks a required scope (for example one created before
    youtube.readonly was added) is discarded and consent is requested again.
    With ``interactive=False`` no browser is opened; YouTubeAuthError is raised
    instead, so background sync never blocks. Tokens are never printed.
    """
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if creds is not None:
        missing = _missing_scopes(creds)
        if missing:
            print("   WARNING: the stored YouTube authorization lacks required scope(s): "
                  + ", ".join(missing) + ". Re-consent is required.")
            creds = None
        elif not creds.valid and getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
            try:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                with open(TOKEN_FILE, "wb") as f:
                    pickle.dump(creds, f)
            except Exception as exc:
                print(f"   WARNING: YouTube token refresh failed ({exc.__class__.__name__}).")
                creds = None

    if creds is None or not creds.valid:
        if not interactive:
            raise YouTubeAuthError(
                "YouTube authorization is missing, expired or lacks a required scope "
                "(captions and analytics need new ones since Phase 3.6). Run once: "
                "python -m scripts.sync_publications — the browser asks for consent again; the OAuth app "
                "is in Testing, so sign in with the channel's account (a listed test user)."
            )
        if not os.path.exists(config.YOUTUBE_CLIENT_SECRET):
            raise FileNotFoundError(
                f"YouTube OAuth file not found: {config.YOUTUBE_CLIENT_SECRET}\n"
                "Follow the setup guide to create it in Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            config.YOUTUBE_CLIENT_SECRET, _required_scopes()
        )
        creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return creds


def get_service(interactive: bool = True):
    """Return an authenticated YouTube service object."""
    return build("youtube", "v3", credentials=load_credentials(interactive))


# Backwards-compatible private name.
_get_service = get_service


def build_upload_description(script: dict, output_dir: str | None = None) -> str:
    """The description pipeline.py uploads: the validated template (Phase 3.6-C) when it can be
    filled, else the legacy story text, music credit, footer and hashtags."""
    from agents import packaging
    from agents.narrative import load_json
    from video import description
    from video.music import music_attribution
    folder = output_dir or config.OUTPUT_DIR
    try:
        brief = load_json(os.path.join(folder, "story_brief.json")) or load_json(os.path.join(folder, "research.json")) or {}
        return description.build(folder, script, brief, load_json(os.path.join(folder, packaging.DESCRIPTION_PARTS_FILE)))
    except (description.DescriptionError, OSError, KeyError, ValueError):
        pass

    credit = music_attribution(output_dir or config.OUTPUT_DIR)
    return (
        script.get("description", "") + "\n\n"
        + (f"Music: {credit}\n\n" if credit else "")
        + "─────────────────────────────\n"
        + f"{config.CHANNEL_NAME} — original cinematic psychological fiction.\n"
        + "─────────────────────────────\n"
        + " ".join(f"#{t}" for t in script.get("tags", []))
    )


def upload_video(video_path: str, *, title: str, description: str, tags=None,
                 privacy: str | None = None, publish_at: str = "",
                 thumbnail_path: str | None = None, progress=None, service=None,
                 contains_synthetic_media: bool = False, localizations: dict | None = None) -> str:
    """Upload one video and return its YouTube video_id.

    ``privacy`` defaults to config.VIDEO_PRIVACY ("public"). A ``publish_at``
    RFC 3339 time uploads the video as private and YouTube publishes it then.
    ``progress`` receives an integer percentage.
    """
    youtube = service or get_service()
    body = {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        list(tags or []),
            "categoryId":  config.VIDEO_CATEGORY_ID,
            # English language signals (Phase 3.6-D): metadata and spoken audio.
            "defaultLanguage":      getattr(config, "VIDEO_LANGUAGE", "en"),
            "defaultAudioLanguage": getattr(config, "VIDEO_LANGUAGE", "en"),
        },
        "status": {
            # If scheduling: upload as private, YouTube publishes at publishAt
            "privacyStatus":            "private" if publish_at else (privacy or config.VIDEO_PRIVACY),
            "selfDeclaredMadeForKids":  False,
            # YouTube's altered/synthetic content disclosure (VideoStatus field
            # verified in the installed discovery document).
            "containsSyntheticMedia":   bool(contains_synthetic_media),
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at
    if localizations:                                    # Phase 4.0-B: translated titles and descriptions
        body["localizations"] = localizations

    # Own the file handles so they close after the upload (Windows keeps
    # files opened by MediaFileUpload locked until garbage collection).
    with open(video_path, "rb") as handle:
        media = MediaIoBaseUpload(
            handle,
            mimetype="video/mp4",
            resumable=True,
            chunksize=5 * 1024 * 1024,   # 5 MB chunks
        )
        insert_req = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = insert_req.next_chunk()
            if status and progress:
                progress(int(status.progress() * 100))
    video_id = response["id"]

    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            with open(thumbnail_path, "rb") as handle:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaIoBaseUpload(handle, mimetype="image/jpeg"),
                ).execute()
        except Exception as exc:
            print(f"   WARNING: thumbnail upload failed ({exc.__class__.__name__}); video kept.")
    if progress:
        progress(100)
    return video_id


def upload_captions(video_id: str, srt_path, *, service=None, language: str | None = None,
                    name: str | None = None, replace: bool = False) -> dict:
    """Upload ``srt_path`` as the video's caption track (captions.insert, youtube.force-ssl scope).

    An existing track with the same language and name (not YouTube's automatic one) is kept,
    or replaced with ``replace=True``. Returns {"action": "inserted"|"updated"|"kept", "id"}."""
    youtube = service or get_service()
    language = language or getattr(config, "VIDEO_LANGUAGE", "en")
    name = name or getattr(config, "CAPTION_TRACK_NAME", "English")
    if not Path(srt_path).is_file():
        raise FileNotFoundError(f"no caption file at {srt_path}")
    existing = youtube.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    same = next((item for item in existing if item["snippet"].get("language") == language
                 and item["snippet"].get("name", "") == name
                 and item["snippet"].get("trackKind") != "asr"), None)
    if same and not replace:
        return {"action": "kept", "id": same["id"]}
    with open(srt_path, "rb") as handle:
        media = MediaIoBaseUpload(handle, mimetype="application/octet-stream", resumable=False)
        if same:
            response = youtube.captions().update(part="snippet", body={"id": same["id"], "snippet": {
                "isDraft": False}}, media_body=media).execute()
            return {"action": "updated", "id": response.get("id", same["id"])}
        response = youtube.captions().insert(part="snippet", body={"snippet": {
            "videoId": video_id, "language": language, "name": name, "isDraft": False}},
            media_body=media).execute()
    return {"action": "inserted", "id": response.get("id")}


def update_snippet(video_id: str, *, service=None, description: str | None = None,
                   language: bool = False) -> dict:
    """videos.update of the snippet: the description and/or the English language fields. The
    current title, category and tags are read first and sent back unchanged (the API requires them)."""
    youtube = service or get_service()
    items = youtube.videos().list(part="snippet", id=video_id).execute().get("items", [])
    if not items:
        raise ValueError(f"video {video_id} not found on this channel")
    current = items[0]["snippet"]
    snippet = {"title": current["title"], "categoryId": current.get("categoryId", config.VIDEO_CATEGORY_ID),
               "description": current.get("description", ""), "tags": current.get("tags", [])}
    for key in ("defaultLanguage", "defaultAudioLanguage"):
        if current.get(key):
            snippet[key] = current[key]
    if description is not None:
        snippet["description"] = description
    if language:
        snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = getattr(config, "VIDEO_LANGUAGE", "en")
    youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
    return snippet


def upload_run(run_dir: str, *, video_path: str, title: str, description: str, tags=None,
               privacy: str | None = None, publish_at: str = "",
               thumbnail_path: str | None = None, progress=None, service=None) -> dict:
    """Upload a generated run and record it in the publication ledger.

    Refused (UploadNotApproved) unless ``<run_dir>/review_approval.json`` is a
    complete approval of exactly these artifacts. Returns the ledger record
    (state ``uploaded_pending_publication``); the sync later marks it
    published and bans its topic.
    """
    from uploader import publications
    from uploader.approval import episode_disclosure, require_approval

    approval = require_approval(
        run_dir, video_path=video_path, title=title, description=description, tags=tags,
        thumbnail_path=thumbnail_path,
    )
    disclosure = episode_disclosure(run_dir, approval)  # Phase 3.9-F: one decision for the episode and its Shorts
    localizations = episode_localizations(run_dir, title, description)
    video_id = upload_video(
        video_path, title=title, description=description, tags=tags, privacy=privacy,
        publish_at=publish_at, thumbnail_path=thumbnail_path, progress=progress,
        service=service,
        contains_synthetic_media=disclosure["value"], localizations=localizations,
    )
    captions = {"action": "skipped", "reason": "no narration.srt"}
    srt = Path(run_dir) / "narration.srt"
    if srt.is_file():                                   # English captions (Phase 3.6-D); never fails the upload
        try:
            captions = upload_captions(video_id, srt, service=service)
        except Exception as exc:                       # noqa: BLE001
            captions = {"action": "failed", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}
            print(f"   WARNING: caption upload failed ({exc.__class__.__name__}); video kept. Retry with "
                  f"python -m scripts.youtube_backfill --video-id {video_id} --captions")
    from agents.narrative import load_json as _load
    from uploader import after_upload
    kind = (_load(Path(run_dir) / "script.json") or {}).get("narrative_type", "")
    try:                                                # Phase 4.0-E: the type's playlist; never fails the upload
        playlist = after_upload.add_episode_to_playlist(service or get_service(interactive=False), video_id, kind)
    except Exception as exc:                            # noqa: BLE001
        playlist = {"action": "failed", "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}
    shorts = []
    from uploader import shorts_upload
    if shorts_upload.included(run_dir):                 # Phase 3.7: after the episode, with its URL
        try:
            shorts = shorts_upload.upload_shorts(run_dir, video_id, service=service, privacy=privacy,
                                                 publish_at=publish_at, disclosure=disclosure)
        except Exception as exc:                       # noqa: BLE001 — the episode is uploaded and recorded
            print(f"   WARNING: Shorts upload stopped ({exc.__class__.__name__}: {str(exc)[:200]}); "
                  "the episode is uploaded.")
    return publications.record_upload(
        video_id, run_dir, title=title, video_path=video_path,
        scheduled_publish_at=publish_at or None,
        extra={"review": {
            "approved_at": approval.get("approved_at"),
            "narrative_status": approval.get("narrative_status"),
            "status_override_reason": approval.get("status_override_reason", ""),
            "title_override_reason": approval.get("title_override_reason", ""),
        }, "captions": captions, "language": getattr(config, "VIDEO_LANGUAGE", "en"),
            "shorts": [record["video_id"] for record in shorts],
            "localizations": sorted(localizations or {}),
            "narrative_type": kind, "playlist": playlist,
            "contains_synthetic_media": disclosure["value"], "synthetic_disclosure_reason": disclosure["reason"]},
    )


def episode_localizations(run_dir, title: str, description: str) -> dict:
    """Phase 4.0-B: the reviewed translations of exactly this title and description, else fresh ones from Gemini;
    a failure never stops the upload (the backfill can add them later)."""
    from uploader import localize
    try:
        found = localize.current(run_dir, title, description)
        if found is None:
            found = localize.ensure(run_dir, title, description)
        return localize.payload(found)
    except Exception as exc:                             # noqa: BLE001
        print(f"   WARNING: translations skipped ({exc.__class__.__name__}: {str(exc)[:160]}); add them later with "
              "python -m scripts.youtube_backfill --localize")
        return {}


def upload_to_youtube(script: dict, video_path: str) -> str:
    """
    Uploads the video with auto-generated title, description, and tags.
    Returns the YouTube URL.
    """
    print("   → Uploading…")
    record = upload_run(
        config.OUTPUT_DIR, video_path=video_path, title=script["title"],
        description=build_upload_description(script), tags=script.get("tags", []),
        progress=lambda pct: print(f"   → {pct}% uploaded", end="\r"),
    )
    print()
    return record["url"]
