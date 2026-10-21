# 🛠️ Private Gravity Setup Guide

Complete step-by-step instructions to get YouTube AI Agent Studio running from scratch.

> **Time required:** ~10 minutes

---

## Prerequisites

- Python **3.10 or newer** — [python.org/downloads](https://www.python.org/downloads/)
- `ffmpeg` installed and on your PATH (see [Step 1b](#1b-install-ffmpeg))
- A Google account (for Gemini + YouTube)
- A Pixabay account (recommended primary image API; Openverse works without credentials)

---

## Step 1 — Clone and install

### 1a. Clone the repository

```bash
git clone https://github.com/yourusername/youtube-ai-agent.git
cd youtube-ai-agent
```

### 1b. Install ffmpeg

ffmpeg is required by MoviePy for video rendering.

| OS | Command |
|---|---|
| **Windows** | Download from [ffmpeg.org](https://ffmpeg.org/download.html) → extract → add `bin/` folder to PATH |
| **Mac** | `brew install ffmpeg` |
| **Ubuntu/Debian** | `sudo apt install ffmpeg` |

Verify: `ffmpeg -version` should print version info.

### 1c. Install Python dependencies

```bash
pip install -r requirements.txt
```

> ⚠️ **Do not upgrade** `moviepy` or `numpy` — they are pinned intentionally. See [FAQ in README](README.md#-faq).

---

## Step 2 — Get your API keys

### 2a. Google Gemini API Key (required)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key

### 2b. Pixabay API Key (recommended — free)

1. Go to [pixabay.com/api/docs](https://pixabay.com/api/docs/)
2. Create a free account and copy the API key shown in the documentation page
3. Copy the key

If the key is omitted, `STOCK_PROVIDER=auto` skips Pixabay and uses Openverse.

---

## Step 3 — Configure your keys

### Option A — Environment variables (recommended)

```bash
# Mac / Linux
export GEMINI_API_KEY="your_key_here"
export PIXABAY_API_KEY="your_key_here"

# Windows (Command Prompt)
set GEMINI_API_KEY=your_key_here
set PIXABAY_API_KEY=your_key_here

# Windows (PowerShell)
$env:GEMINI_API_KEY="your_key_here"
$env:PIXABAY_API_KEY="your_key_here"
```

To make these permanent, add them to your shell profile (`~/.bashrc`, `~/.zshrc`) or use a `.env` file:

```bash
cp .env.example .env
# Now edit .env and fill in your keys
```

### Option B — Local `.env` file (recommended for persistent settings)

Copy `.env.example` to `.env`, then set:

```dotenv
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-3.8-flash
PIXABAY_API_KEY=your_pixabay_key
STOCK_PROVIDER=auto
STOCK_FALLBACK_PROVIDER=openverse
```

The GUI also saves new credentials to this gitignored file. It never writes
API secrets into `config.py` or returns their values from `/api/settings`.

Process-level environment variables take precedence over `.env` during a fresh
start. A key entered in the GUI takes effect immediately for the running process
and invalidates the cached Gemini client. If a system-level `GEMINI_API_KEY`
exists, update or remove it as well when you want the `.env` value to win after
a restart.

Model availability and quotas vary by project. Verify the current key with one
small, explicit diagnostic run:

```bash
python diagnose_gemini.py
```

This lists accessible `generateContent` models and performs one minimal text
request. It never prints the API key.

### Stock provider behavior

For each requested visual, the app checks `assets/stock_approved/`, then uses
Pixabay when configured, then Openverse. Openverse results are accepted only
when the API explicitly reports CC0 or Public Domain Mark. Validated downloads
and searches are cached under `output/.stock_cache/`; provenance and manual
review flags are recorded in `output/asset_manifest.json`.

---

## Step 4 — Set up YouTube upload (optional)

Skip this step if you just want to generate videos locally without uploading.

### 4a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select a project → New Project**
3. Name it (e.g. "YouTube AI Agent") and click **Create**

### 4b. Enable the YouTube Data API

1. In your new project, go to **APIs & Services → Library**
2. Search for **YouTube Data API v3**
3. Click **Enable**

### 4c. Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. If prompted, configure the consent screen first:
   - User Type: **External**
   - Fill in app name, support email, and developer email
   - Scopes: click **Add or remove scopes** → search "youtube" → select `youtube.upload`
     and `youtube.readonly` (needed to detect when an upload becomes public)
   - Test users: add your own Gmail address
4. Back in Credentials → **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. "YT Agent Desktop")
5. Click **Download JSON**
6. Rename the downloaded file to `client_secret.json`
7. Place it in the root of the project (replacing the placeholder)

### 4d. First-time authentication

On your first run, a browser window will open asking you to sign in with Google and grant permission. This only happens once — the token is saved locally as `youtube_token.pickle`.

Tokens created before `youtube.readonly` was added lack that scope. Run
`python -m scripts.sync_publications` once: it detects the missing scope and asks for consent
again. Background sync never opens a browser; it logs a warning and continues.

> If you see a "Google hasn't verified this app" warning, click **Advanced → Go to (app name) (unsafe)**. This is expected for personal OAuth apps in testing mode.

---

## Step 5 — Configure the narrative workflow

Open `config.py` and edit:

```python
CHANNEL_DESCRIPTION = """
A cinematic storytelling channel exploring psychology and philosophy through original fiction.
"""
CHANNEL_NAME = "Private Gravity"
NARRATIVE_TYPE = "auto"            # or one of the three explicit narrative types
PSYCHOLOGICAL_THEME = ""            # blank lets the Story Development Agent choose
TARGET_DURATION_MINUTES = 15         # regular episodes accept 12–20
NARRATIVE_REVIEW_ENABLED = True      # adds one Gemini editorial-review call
```

Generation reliability and cost settings live in `.env` (see `.env.example`):
`GEMINI_TIMEOUT_SECONDS` (300), `GEMINI_THINKING_LEVEL` (empty = model default),
`GEMINI_REPAIR_MAX_CHARS`, `NARRATIVE_SEMANTIC_REVISION_ENABLED` (true; worst case +2 Gemini
calls per episode) and `PUBLICATION_SYNC_INTERVAL_MINUTES` (30).

The GUI exposes the same controls per run. Exact-premise mode preserves the
entered premise; automatic mode compares weighted candidates. Disable the
editorial review to save one model call—the deterministic schema checks still run.

---

## Step 6 — Launch

### GUI mode (recommended)

```bash
python gui.py
```

Opens at **http://localhost:7070** — configure everything visually and click Generate.

### CLI mode

```bash
python pipeline.py
```

Runs the full pipeline in the terminal. The review dashboard opens automatically in your browser when the video is ready.

### Before uploading

Upload is refused until the review checklist is completed in the GUI review panel (or the
pipeline dashboard). Uploads are public by default; a narrative status of `revision_required`
or `structurally_invalid` additionally needs a typed override reason. Before the first render,
run `python -m scripts.render_preflight` to check the toolchain offline.

---

## Step 7 — Add background music (optional)

Drop any `.mp3` or `.wav` files into the `music library/` folder.  
The pipeline will automatically shuffle and loop them to match the video duration.

The repo includes a few dark cinematic tracks to get you started. Replace or add to them freely.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named 'moviepy'` | Run `pip install -r requirements.txt` again |
| `ffmpeg not found` | Install ffmpeg and ensure it's on your PATH |
| `RESOURCE_EXHAUSTED` / 429 | Run `python diagnose_gemini.py`, then review the project's current rate limits/billing or retry after the reported reset |
| Gemini 401/403 | Replace the key or verify that its project has Gemini API/model access |
| No available Gemini models | Use **Check Available Models** in Settings and verify `generateContent` access in Google AI Studio |
| `youtube_token.pickle` auth error | Delete the file and re-run to re-authenticate |
| Tiny/ugly video text | Install DejaVu fonts: `sudo apt install fonts-dejavu` (Linux) |
| Flask port already in use | Change `REVIEW_PORT` in `config.py` |
| `client_secret.json` error | Ensure you replaced the placeholder with your real Google credentials |
| Captions out of sync | Switch to a different `VOICE_ID` in `config.py` (see voice list in file) |

---

## Optional: Run as a background service

To keep the studio running continuously (e.g. on a home server):

```bash
# Using nohup (Linux/Mac)
nohup python gui.py > studio.log 2>&1 &

# Using screen
screen -S yt-studio
python gui.py
# Detach: Ctrl+A then D
```

---

If you get stuck, [open an issue](https://github.com/yourusername/youtube-ai-agent/issues) with your error message and OS.
