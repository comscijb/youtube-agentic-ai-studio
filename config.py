import os
from pathlib import Path

_PROCESS_ENV_KEYS = set(os.environ)
_LOCAL_ENV_KEYS = set()


def _load_local_env():
    """Load a simple local .env without overriding process-level settings."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                _LOCAL_ENV_KEYS.add(key)
    except OSError:
        pass


_load_local_env()

# ─────────────────────────────────────────────────────────────────────────────
#  YouTube AI Agent Studio — Configuration
#  Copy this file as-is. Fill in your API keys below (or use env vars).
#  All settings are documented. Change only what you need.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────
#  API Keys  (all free-tier)
# ─────────────────────────────────────────
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY",     "YOUR_GEMINI_API_KEY")
PIXABAY_API_KEY    = os.getenv("PIXABAY_API_KEY",    "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "YOUR_ELEVENLABS_KEY")  # optional
GEMINI_API_KEY_SOURCE = (
    "process environment" if "GEMINI_API_KEY" in _PROCESS_ENV_KEYS
    else "local .env" if "GEMINI_API_KEY" in _LOCAL_ENV_KEYS
    else "not configured"
)

# Stock providers. "auto" tries the approved local library, Pixabay when a
# key is configured, then the credential-free Openverse fallback.
# Off by default: no Pixabay/Openverse request is ever made unless this is true.
STOCK_ENABLED             = os.getenv("STOCK_ENABLED", "false").lower() in ("1", "true", "yes", "on")
STOCK_PROVIDER            = os.getenv("STOCK_PROVIDER", "auto").lower()
STOCK_FALLBACK_PROVIDER   = os.getenv("STOCK_FALLBACK_PROVIDER", "openverse").lower()
STOCK_CACHE_ENABLED       = os.getenv("STOCK_CACHE_ENABLED", "true").lower() in ("1", "true", "yes", "on")
STOCK_LICENSE_LOG_ENABLED = os.getenv("STOCK_LICENSE_LOG_ENABLED", "true").lower() in ("1", "true", "yes", "on")
STOCK_LOCAL_ASSET_DIR     = os.getenv("STOCK_LOCAL_ASSET_DIR", "assets/stock_approved")
# Stock is the fallback for environment/object shots. Candidates must share at
# least this share of the short query's terms with their tags, must not be
# bright/saturated (dark channel palette), must not look like text/signage, and
# cannot show identifiable people when the shot is about a specific character.
STOCK_MIN_RELEVANCE  = float(os.getenv("STOCK_MIN_RELEVANCE", "0.4"))
STOCK_TONE_FILTER    = True
STOCK_MAX_LUMINANCE  = 150        # mean 0-255
STOCK_MAX_SATURATION = 140        # mean 0-255, applied to brighter images only
STOCK_TEXT_THRESHOLD = 0.4        # 0-1 text/signage likelihood

# ─────────────────────────────────────────
#  Your Channel Identity
#  The more specific you are, the better the AI's topic suggestions.
# ─────────────────────────────────────────
CHANNEL_DESCRIPTION = """
A cinematic storytelling channel exploring the psychology and philosophy of modern human life through original fictional stories.
Each episode follows a carefully developed character facing universal struggles such as loneliness, ambition, identity, envy, regret, status, meaning, and fear of failure.
The stories combine realistic psychological drama with occasional philosophical thought experiments and subtle surreal elements.
Psychological and philosophical ideas are embedded naturally within the narrative rather than presented as lectures.
The focus is on emotionally compelling plots, strong character motivations, escalating internal conflicts, and memorable reversals.
Episodes are designed to create curiosity, identification, and reflection while remaining accessible to a broad audience.
The visual and narrative style is cinematic, atmospheric, mature, and distinctive.
The channel functions as an anthology, with mostly standalone stories centered on the hidden forces that shape human behavior and modern life.
"""
CHANNEL_NAME = "Private Gravity"   # shown on-screen and in upload metadata

# Narrative development defaults. Regular episodes can be set from 12 to 20
# minutes in the GUI; Shorts use their own fixed pacing contract.
NARRATIVE_TYPE = os.getenv("NARRATIVE_TYPE", "auto")
PSYCHOLOGICAL_THEME = os.getenv("PSYCHOLOGICAL_THEME", "")
TARGET_DURATION_MINUTES = float(os.getenv("TARGET_DURATION_MINUTES", "15"))
NARRATION_WPM = int(os.getenv("NARRATION_WPM", "125"))
# Planning uses the measured delivery of the configured voice + rate from
# data/voice_calibration.json (updated after every narration) when available;
# NARRATION_WPM is the fallback. AndrewNeural at -5% measured ~151.6 wpm, so a
# 12-minute episode targets ~1 820 words.
# Captions (normal 16:9 videos): cue limits and placement.
CAPTION_MAX_WORDS = 7
CAPTION_MAX_CHARS = 42
CAPTION_MIN_SECONDS = 0.8
CAPTION_MAX_SECONDS = 4.5
CAPTION_LEAD_IN_SECONDS = 0.08
CAPTION_FONT_SIZE = 36
CAPTION_BOTTOM_MARGIN = 90        # px from the bottom edge, above the progress bar
VOICE_CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "voice_calibration.json")
USE_CALIBRATED_WPM = os.getenv("USE_CALIBRATED_WPM", "true").lower() in ("1", "true", "yes", "on")
NARRATIVE_REVIEW_ENABLED = os.getenv(
    "NARRATIVE_REVIEW_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
NARRATIVE_MAX_REVISIONS = 1
NARRATIVE_AUTO_REVISION_ENABLED = os.getenv(
    "NARRATIVE_AUTO_REVISION_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
NARRATIVE_EXPANSION_MAX_CALLS = int(os.getenv("NARRATIVE_EXPANSION_MAX_CALLS", "3"))
NARRATIVE_EXPANSION_BATCH_SIZE = int(os.getenv("NARRATIVE_EXPANSION_BATCH_SIZE", "3"))
# Phase 3.8: scenes Gemini plans at once while ComfyUI renders (1 keeps strict scene-to-scene
# continuity; 2 starts the next scene early with the latest finished scene as continuity; never more).
SHOT_PLANNER_CONCURRENCY = int(os.getenv("SHOT_PLANNER_CONCURRENCY", "1"))
# A batch that is not fully accepted is retried scene by scene (at most this many extra calls).
NARRATIVE_EXPANSION_SINGLE_RETRIES = int(os.getenv("NARRATIVE_EXPANSION_SINGLE_RETRIES", "3"))
# One bounded rewrite of scenes cited by confirmed/probable major or blocking
# editorial findings (lecture, continuity, agency...). Worst-case cost per
# episode: +1 revision call per batch (MAX_CALLS) and +1 editorial re-review.
NARRATIVE_SEMANTIC_REVISION_ENABLED = os.getenv(
    "NARRATIVE_SEMANTIC_REVISION_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
NARRATIVE_SEMANTIC_REVISION_MAX_CALLS = int(os.getenv("NARRATIVE_SEMANTIC_REVISION_MAX_CALLS", "1"))
NARRATIVE_SEMANTIC_REVISION_BATCH_SIZE = int(os.getenv("NARRATIVE_SEMANTIC_REVISION_BATCH_SIZE", "3"))
NARRATIVE_SEMANTIC_REREVIEW = os.getenv(
    "NARRATIVE_SEMANTIC_REREVIEW", "true"
).lower() in ("1", "true", "yes", "on")
# Above this many stacked modifiers/intensifiers per episode the deterministic
# review adds a minor (never blocking) style finding.
NARRATIVE_STYLE_LINT_THRESHOLD = int(os.getenv("NARRATIVE_STYLE_LINT_THRESHOLD", "3"))
NARRATIVE_SEMANTIC_REVISION_TYPES = (
    "lecture", "continuity", "unintroduced_character", "character_consistency", "agency",
    "causality", "repetition", "style", "brief_faithfulness",
)

# ─────────────────────────────────────────
#  Voice (Edge TTS — free, no API key)
#  Voices confirmed to emit word-level timing (needed for caption sync):
#    en-GB-RyanNeural     — British, deep, cinematic
#    en-US-AndrewNeural   — US, warm, authoritative  ← default
#    en-US-BrianNeural    — US, calm, documentary
#    en-US-GuyNeural      — US, clear, neutral
# ─────────────────────────────────────────
VOICE_ID    = "en-US-AndrewNeural"
VOICE_RATE  = "-5%"     # slow down slightly for narration feel
VOICE_PITCH = "-3Hz"    # slightly deeper = more cinematic

# ─────────────────────────────────────────
#  AI Model
#  Preferred starting model. Runtime discovery keeps only models that this
#  API key can access and that explicitly support generateContent.
#  Change this in the GUI under Settings → AI Model, or here directly.
#  Current candidates: gemini-3.8-flash, gemini-3.6-flash,
#  gemini-3.5-flash-lite. Availability varies by project.
# ─────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Request deadline for long-form generation stages (outline, scene batches,
# editorial review). The SDK forwards it to the server as X-Server-Timeout, so
# a short value makes thinking models answer 504 DEADLINE_EXCEEDED. On a 504
# the same model is retried once with twice the deadline (capped at 600 s).
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "300"))
# Short deadline for model discovery and the connectivity diagnostic.
GEMINI_DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("GEMINI_DISCOVERY_TIMEOUT_SECONDS", "30"))
# Thinking depth for Gemini 3.x: minimal | low | medium | high, or an integer
# token budget. Empty keeps the model's own default (safest across models).
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "").strip()
# Optional output cap per call; 0 keeps the model's own limit.
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "0"))
# Largest repair prompt sent in one call. Larger outline repairs are split into
# scene-scoped repairs that only resend the invalid scenes and their neighbours.
GEMINI_REPAIR_MAX_CHARS = int(os.getenv("GEMINI_REPAIR_MAX_CHARS", "120000"))
# Overload (503 "high demand") and rate limits (429): wait on the SAME model with
# exponential backoff (~10/20/40/80 s, or the server's Retry-After) before trying
# the fallback model with the same budget. One call never exceeds the wall clock.
GEMINI_OVERLOAD_RETRIES   = int(os.getenv("GEMINI_OVERLOAD_RETRIES", "4"))
GEMINI_OVERLOAD_BASE_DELAY = float(os.getenv("GEMINI_OVERLOAD_BASE_DELAY", "10"))
GEMINI_OVERLOAD_MAX_DELAY = float(os.getenv("GEMINI_OVERLOAD_MAX_DELAY", "90"))
GEMINI_MAX_CALL_WALLCLOCK = float(os.getenv("GEMINI_MAX_CALL_WALLCLOCK", "600"))
# Sampling temperature per generation stage.
GEMINI_STAGE_TEMPERATURES = {
    "premise": 0.9,
    "character_bible": 0.7,
    "outline": 0.6,
    "scenes": 0.85,
    "expansion": 0.7,
    "editorial_review": 0.2,
    "repair": 0.2,
    "packaging": 0.8,          # thumbnail concepts, description parts (Phase 3.6)
    "title_scoring": 0.2,      # title candidates scored on curiosity, concreteness, emotion, clarity
}

# ─────────────────────────────────────────
#  Local image generation (ComfyUI + FLUX 3 Image Klein)
# ─────────────────────────────────────────
# Visual identity (agents/identity.py): "dark_illustration" (default) or the legacy
# "cinematic_photo". The variant is chosen after python -m scripts.calibrate_style:
# "engraving" | "charcoal_gouache" | "hybrid".
VISUAL_STYLE         = os.getenv("VISUAL_STYLE", "dark_illustration")
VISUAL_STYLE_VARIANT = os.getenv("VISUAL_STYLE_VARIANT", "engraving")   # chosen after calibration
# Channel-level style anchors approved after python -m scripts.generate_style_refs.
STYLE_REFS_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "style_refs")
# Illustrations are already low-key: much lighter darkening overlay than photos.
ILLUSTRATION_OVERLAY_OPACITY = 0.12
IMAGEGEN_ENABLED          = os.getenv("IMAGEGEN_ENABLED", "true").lower() in ("1", "true", "yes", "on")
IMAGEGEN_COMFYUI_URL      = os.getenv("IMAGEGEN_COMFYUI_URL", "http://127.0.0.1:8188")
IMAGEGEN_SIZE             = (1280, 720)   # true 16:9, multiples of 16; (1360, 768) for a little more detail
IMAGEGEN_ASPECT_TOLERANCE = 0.03          # outputs within 3 % of 16:9 are fitted (scale + centre crop)
IMAGEGEN_STEPS            = None          # None keeps the template's 16 steps
IMAGEGEN_LATENT_SOURCE    = os.getenv("IMAGEGEN_LATENT_SOURCE", "empty_16x9")  # "reference" follows the first reference's size
IMAGEGEN_PROMPT_UPSAMPLER = False         # the 9B upsampler is costly on 8 GB VRAM; Gemini writes the prompts
IMAGEGEN_TIMEOUT_SECONDS  = 900
IMAGEGEN_MAX_UNIQUE_SHOTS = int(os.getenv("IMAGEGEN_MAX_UNIQUE_SHOTS", "75"))   # ~70 for 10 min at 6-9 s
IMAGEGEN_SHOT_MIN_SECONDS = 6.0
IMAGEGEN_SHOT_MAX_SECONDS = 9.0     # one image every 6-9 s (a single longer sentence keeps its image)
# "reference": approved character portraits drive identity, faces may be visible.
# "limited_faces": mostly none/partial faces, at most IMAGEGEN_MAX_FULL_FACE_SHOTS hero shots.
IMAGEGEN_IDENTITY_POLICY  = os.getenv("IMAGEGEN_IDENTITY_POLICY", "reference")
IMAGEGEN_MAX_FULL_FACE_SHOTS = 6
IMAGEGEN_FREE_MEMORY_EVERY   = 10         # call ComfyUI /free between batches of shots
# Every generated image is checked for paper margins and comic-page gutters;
# flawed images are regenerated with new seeds, then margins are cropped away.
IMAGEGEN_IMAGE_CHECKS        = True
IMAGEGEN_CHECK_RETRIES       = 2
# Visual QA (Gemini vision) of every generated shot and thumbnail: headcount, duplicated
# person, signature markers, unstated injuries, text. A failed blocking check gets up to
# IMAGEGEN_QA_RETRIES new seeds; the best attempt is kept and flagged in the GUI.
IMAGEGEN_VISUAL_QA        = os.getenv("IMAGEGEN_VISUAL_QA", "true").lower() in ("1", "true", "yes", "on")
IMAGEGEN_QA_RETRIES       = int(os.getenv("IMAGEGEN_QA_RETRIES", "2"))
IMAGEGEN_QA_MAX_SIDE      = 768           # px; the image is downscaled before it is sent
IMAGEGEN_VISUAL_QA_MODEL  = os.getenv("IMAGEGEN_VISUAL_QA_MODEL", "")   # "" = the configured Gemini chain (Flash)
# Phase 3.5: local-first layered QA. Layer 1 = OpenCV detectors on the CPU (models in
# models/qa/), layer 2 = a local VLM answering one-word questions about crops, layer 3 =
# Gemini vision, off by default (fallback when the local VLM is unavailable, or qa_shots --gemini).
IMAGEGEN_QA_LAYERS        = os.getenv("IMAGEGEN_QA_LAYERS", "deterministic,local_vlm")
IMAGEGEN_QA_GEMINI        = os.getenv("IMAGEGEN_QA_GEMINI", "false").lower() in ("1", "true", "yes", "on")
IMAGEGEN_QA_VLM_BACKEND   = os.getenv("IMAGEGEN_QA_VLM_BACKEND", "comfy_textgenerate")   # or "ollama"
IMAGEGEN_QA_VLM_MODEL     = os.getenv("IMAGEGEN_QA_VLM_MODEL", "qwen3vl_4b_int8_convrot.safetensors")
IMAGEGEN_QA_VLM_CLIP_TYPE = os.getenv("IMAGEGEN_QA_VLM_CLIP_TYPE", "flux3")
IMAGEGEN_QA_OLLAMA_URL    = os.getenv("IMAGEGEN_QA_OLLAMA_URL", "http://127.0.0.1:11434")
IMAGEGEN_QA_OLLAMA_MODEL  = os.getenv("IMAGEGEN_QA_OLLAMA_MODEL", "qwen2.5vl:3b")         # never pulled automatically
IMAGEGEN_QA_MODELS_DIR    = os.getenv("IMAGEGEN_QA_MODELS_DIR", "")       # "" = models/qa
IMAGEGEN_QA_MAX_EDITS     = 2             # region edits per shot before regenerating
IMAGEGEN_QA_ROUNDS        = 2             # QA → fix rounds per generate_shots run
# advisory: only borders, blank/corrupt images and OCR text act on their own, everything else is a hint
# for the GUI review; auto: also issue types measured >= 85 % on the probe fixtures; off: no QA.
IMAGEGEN_QA_MODE          = os.getenv("IMAGEGEN_QA_MODE", "advisory")
# Target share of wide/establishing shots with small figures: guidance for the
# shot planner and a warning in shot_list.json, never a failure.
IMAGEGEN_MIN_WIDE_SHARE      = float(os.getenv("IMAGEGEN_MIN_WIDE_SHARE", "0.3"))   # target only: a warning, never a failure

# ─────────────────────────────────────────
#  Video Dimensions
# ─────────────────────────────────────────
VIDEO_WIDTH  = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS    = 24

# YouTube Shorts dimensions (9:16 vertical)
SHORTS_WIDTH  = 1080
SHORTS_HEIGHT = 1920
SHORTS_FPS    = 30

# ─── Visual Style ───────────────────────
# Ken Burns effect: how much to zoom/pan each image
KB_ZOOM_START = 1.00    # starting scale (1.0 = no zoom)
KB_ZOOM_END   = 1.10    # ending scale   (1.1 = 10% zoom in)

# Image crossfade duration (seconds)
# 0.0 = instant cut  |  0.5 = snappy  |  0.7 = smooth  |  1.2 = dreamy
CROSSFADE_DURATION = 0.7

# Normal video B-roll cycling
BROLL_INTERVAL  = 10.0   # seconds each image stays on screen
BROLL_XFADE_DUR =  1.2   # crossfade duration (must be < BROLL_INTERVAL)

# Render quality preset (ffmpeg libx264)
# "ultrafast" = fastest/largest  |  "veryfast" = recommended  |  "medium" = best quality
RENDER_PRESET = "veryfast"
# Constant quality instead of a fixed high bitrate: 18-20 gives ~4-8 Mbps for 1080p24 illustrations.
RENDER_CRF = 20

# Overlay opacity: lower = more image visible but text harder to read (0–1)
OVERLAY_OPACITY = 0.62
CINEMATIC_OVERLAY_OPACITY = 0.38
NORMAL_SUBTITLES_ENABLED = True
# Tape (VHS) playback effect, applied by ffmpeg before the captions (Phase 3.6-F): "off" | "subtle" | "medium".
# Preview it first: python -m scripts.preview_tape_effect --seconds 20 --intensity subtle
VIDEO_TAPE_EFFECT        = os.getenv("VIDEO_TAPE_EFFECT", "subtle")
VIDEO_TAPE_GLITCH_TITLE  = False          # also a tracking glitch on the title card (first second)

# Colour palette — customise to match your brand
COLORS = {
    "background": (12, 18, 24),       # Midnight Ink
    "overlay":    (0, 0, 0),          # Black
    "primary":    (145, 168, 184),    # Fog Blue
    "accent":     (193, 138, 112),    # Muted Copper
    "highlight":  (224, 186, 121),    # Antique Gold
    "white":      (244, 240, 232),    # Warm Ivory
    "light":      (202, 212, 219),    # Pale Silver
    "success":    (121, 171, 152),    # Muted Sage
    "red":        (197, 102, 112),    # Dusty Crimson
}

# Shorts cut from each episode (Phase 3.7): same narration and shots, uploaded with the episode.
SHORTS_PER_EPISODE  = int(os.getenv("SHORTS_PER_EPISODE", "3"))
SHORTS_MIN_SECONDS  = 45          # YouTube allows 3 min; shorter Shorts keep viewers better
SHORTS_MAX_SECONDS  = 75
# When the Shorts go public: the first with the episode, then one every spacing_hours at time (local).
SHORTS_SCHEDULE     = {"first": "with_episode", "spacing_hours": 24, "time": "18:00"}

# Thumbnails (Phase 3.6): no channel wordmark; an optional tiny corner mark instead.
THUMBNAIL_LOGO = os.getenv("THUMBNAIL_LOGO", "false").lower() in ("1", "true", "yes", "on")

# Fonts — add your own .ttf paths for best results; system falls back gracefully
FONT_PATHS = {
    "bold": [
        "./assets/fonts/LibreBaskerville-Bold.ttf",
        "./assets/fonts/Manrope-ExtraBold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "regular": [
        "./assets/fonts/LibreBaskerville-Regular.ttf",
        "./assets/fonts/Manrope-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "light": [
        "./assets/fonts/Manrope-Light.ttf",
        "C:/Windows/Fonts/segoeuil.ttf",
    ],
}

# ─────────────────────────────────────────
#  Review Server
# ─────────────────────────────────────────
REVIEW_PORT = 5050

# ─────────────────────────────────────────
#  YouTube Upload
# ─────────────────────────────────────────
YOUTUBE_CLIENT_SECRET = "client_secret.json"   # OAuth credentials file (see SETUP.md)
# youtube.upload covers videos.insert and thumbnails.set; videos.list (used to
# detect when an upload becomes public) requires youtube.readonly; captions.insert
# and videos.update (language, description backfill) require youtube.force-ssl
# (Phase 3.6-D). A stored token missing a scope triggers a one-time re-consent.
# The OAuth app is in Testing: the channel's Google account must be a test user.
YOUTUBE_SCOPES        = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    # YouTube Analytics API (views, retention, countries, traffic) and Reporting API (impressions, CTR).
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
ANALYTICS_REFRESH_HOURS = float(os.getenv("ANALYTICS_REFRESH_HOURS", "6"))   # cache age before a new fetch
VIDEO_LANGUAGE        = "en"   # snippet.defaultLanguage / defaultAudioLanguage and the caption track
CAPTION_TRACK_NAME    = "English"
VIDEO_CATEGORY_ID     = "24"  # Entertainment
VIDEO_PRIVACY         = "public"   # "public" | "unlisted" | "private"

# Publication ledger (tracked in git, no secrets). A topic enters
# banned_topics.txt only after its video is actually public on YouTube.
PUBLICATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "publications.json")
PUBLICATION_SYNC_INTERVAL_MINUTES = float(os.getenv("PUBLICATION_SYNC_INTERVAL_MINUTES", "30"))

# ─────────────────────────────────────────
#  Background Music
#  Drop MP3/WAV files into the  music library/  folder.
#  Tracks are shuffled and looped automatically to match video length.
# ─────────────────────────────────────────
MUSIC_ENABLED     = True
MUSIC_VOLUME      = 0.12          # 0.0–1.0  (0.12 = subtle underscore)
MUSIC_LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "music library")
# Optional "music library/tracks.json": [{"file", "moods", "license",
# "source_url", "attribution"}]; tracks are chosen by mood overlap with the
# episode's theme and intended emotional experience.
MUSIC_DUCKING_RATIO     = 8       # sidechain ratio: higher = music dips further under speech
MUSIC_DUCKING_THRESHOLD = 0.03    # narration level that starts the ducking (0-1)
# Final mix loudness (YouTube reference): two-pass ffmpeg loudnorm.
LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_TRUE_PEAK   = -1.5     # mix target
# Checked on the final MP4: above this the audio is limited again at LOUDNESS_TRUE_PEAK and remuxed.
FINAL_TRUE_PEAK_MAX  = -1.0

# ─────────────────────────────────────────
#  Paths  (do not change unless you know what you're doing)
# ─────────────────────────────────────────
# The active project (projects/<slug>/) replaces these at runtime (studio/projects.py);
# "output" stays the legacy folder used when no project exists.
OUTPUT_DIR   = "output"
IMAGES_DIR   = "output/images"
PROJECTS_DIR = "projects"
# Published projects move from the SSD to this archive on the HDD (studio/archive.py).
ARCHIVE_ROOT       = os.getenv("ARCHIVE_ROOT", r"D:\Private Gravity\Archive")
ARCHIVE_ON_PUBLISH = os.getenv("ARCHIVE_ON_PUBLISH", "true").lower() in ("1", "true", "yes", "on")
ARCHIVE_QUEUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "archive_queue.json")
