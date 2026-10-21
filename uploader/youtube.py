"""
YouTube Uploader
The single upload path used by both the GUI and pipeline.py: OAuth, video
insert (optionally scheduled with publishAt), thumbnail, progress reporting,
and the publication-ledger record written right after a successful upload.
"""
import os
import pickle

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
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
                "YouTube authorization is missing, expired or lacks a required scope. "
                "Run: python -m scripts.sync_publications"
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


def build_upload_description(script: dict) -> str:
    """The description pipeline.py has always uploaded: story text, footer, hashtags."""
    return (
        script.get("description", "") + "\n\n"
        + "─────────────────────────────\n"
        + f"{config.CHANNEL_NAME} — original cinematic psychological fiction.\n"
        + "─────────────────────────────\n"
        + " ".join(f"#{t}" for t in script.get("tags", []))
    )


def upload_video(video_path: str, *, title: str, description: str, tags=None,
                 privacy: str | None = None, publish_at: str = "",
                 thumbnail_path: str | None = None, progress=None, service=None) -> str:
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
        },
        "status": {
            # If scheduling: upload as private, YouTube publishes at publishAt
            "privacyStatus":            "private" if publish_at else (privacy or config.VIDEO_PRIVACY),
            "selfDeclaredMadeForKids":  False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(
        video_path,
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
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
            ).execute()
        except Exception as exc:
            print(f"   WARNING: thumbnail upload failed ({exc.__class__.__name__}); video kept.")
    if progress:
        progress(100)
    return video_id


def upload_run(run_dir: str, *, video_path: str, title: str, description: str, tags=None,
               privacy: str | None = None, publish_at: str = "",
               thumbnail_path: str | None = None, progress=None, service=None) -> dict:
    """Upload a generated run and record it in the publication ledger.

    Returns the ledger record (state ``uploaded_pending_publication``); the
    sync later marks it published and bans its topic.
    """
    from uploader import publications

    video_id = upload_video(
        video_path, title=title, description=description, tags=tags, privacy=privacy,
        publish_at=publish_at, thumbnail_path=thumbnail_path, progress=progress,
        service=service,
    )
    return publications.record_upload(
        video_id, run_dir, title=title, video_path=video_path,
        scheduled_publish_at=publish_at or None,
    )


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
