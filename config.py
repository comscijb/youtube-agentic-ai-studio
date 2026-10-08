import os
from pathlib import Path


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

# Stock providers. "auto" tries the approved local library, Pixabay when a
# key is configured, then the credential-free Openverse fallback.
STOCK_PROVIDER            = os.getenv("STOCK_PROVIDER", "auto").lower()
STOCK_FALLBACK_PROVIDER   = os.getenv("STOCK_FALLBACK_PROVIDER", "openverse").lower()
STOCK_CACHE_ENABLED       = os.getenv("STOCK_CACHE_ENABLED", "true").lower() in ("1", "true", "yes", "on")
STOCK_LICENSE_LOG_ENABLED = os.getenv("STOCK_LICENSE_LOG_ENABLED", "true").lower() in ("1", "true", "yes", "on")
STOCK_LOCAL_ASSET_DIR     = os.getenv("STOCK_LOCAL_ASSET_DIR", "assets/stock_approved")

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
#  Starting model for the fallback chain — the system auto-switches
#  through all models below if quota or errors are hit.
#  Change this in the GUI under Settings → AI Model, or here directly.
#  Options (free tier):
#    gemini-2.5-flash        — highest quality   (500 req/day)
#    gemma-4-31b-it          — Gemma 4 31B       (500 req/day)
#    gemini-2.0-flash        — recommended       (1500 req/day)  ← default
#    gemini-2.0-flash-lite   — fastest           (1500 req/day)
#    gemini-1.5-flash-001    — reliable fallback (1500 req/day)
# ─────────────────────────────────────────
GEMINI_MODEL = "gemini-2.0-flash"

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
RENDER_PRESET = "ultrafast"

# Overlay opacity: lower = more image visible but text harder to read (0–1)
OVERLAY_OPACITY = 0.62

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

# Fonts — add your own .ttf paths for best results; system falls back gracefully
FONT_PATHS = {
    "bold": [
        "./assets/fonts/Manrope-ExtraBold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "regular": [
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
YOUTUBE_SCOPES        = ["https://www.googleapis.com/auth/youtube.upload"]
VIDEO_CATEGORY_ID     = "24"  # Entertainment
VIDEO_PRIVACY         = "public"   # "public" | "unlisted" | "private"

# ─────────────────────────────────────────
#  Background Music
#  Drop MP3/WAV files into the  music library/  folder.
#  Tracks are shuffled and looped automatically to match video length.
# ─────────────────────────────────────────
MUSIC_ENABLED     = True
MUSIC_VOLUME      = 0.12          # 0.0–1.0  (0.12 = subtle underscore)
MUSIC_LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "music library")

# ─────────────────────────────────────────
#  Paths  (do not change unless you know what you're doing)
# ─────────────────────────────────────────
OUTPUT_DIR   = "output"
IMAGES_DIR   = "output/images"
