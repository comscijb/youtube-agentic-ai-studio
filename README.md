<div align="center">

<img src="docs/screenshots/banner.png" alt="YouTube AI Agent Studio" width="100%">

# 🎬 Private Gravity — Cinematic Fiction Studio

**A local production pipeline for original cinematic psychological fiction: premise development, character continuity, scene writing, narration, licensed stock acquisition, rendering, review, and optional YouTube upload.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-4285F4?style=flat-square&logo=google&logoColor=white)](https://aistudio.google.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Stars](https://img.shields.io/github/stars/raunakpatil/youtube-agentic-ai-studio?style=flat-square)](https://github.com/raunakpatil/youtube-agentic-ai-studio/stargazers)

[**Quick Start**](#-quick-start) · [**Features**](#-features) · [**Pipeline**](#️-how-it-works) · [**Setup Guide**](SETUP.md) · [**FAQ**](#-faq)

</div>

---

## ✨ Features

| Feature | Details |
|---|---|
| 💡 **Story Development** | Gemini compares weighted premises or develops an exact user premise into a validated story brief |
| 🎭 **Narrative Architecture** | Versioned character bible and causal outline preserve identity, rules, chronology, and escalation |
| ✍️ **Scene Writing** | Scene-batched 12–20 minute screenplay (15 default) or 40–60 second Short |
| 🎙️ **Free Voice** | Microsoft Edge TTS (no API key, no cost, word-level timing) |
| 🖼️ **Stock Images** | Pixabay primary + CC0/PDM Openverse fallback, with provenance |
| 🎞️ **Cinematic Video** | Ken Burns motion, restrained overlays, subtitles, and duration-preserving transitions |
| 📱 **Shorts Support** | Generates vertical 9:16 Shorts alongside the main video |
| 🕵️ **Human Review** | Inspect the brief, character bible, outline, screenplay, deterministic checks, and optional editorial review |
| 🚀 **Auto Upload** | Publishes to YouTube with SEO tags, description, and thumbnail |
| 🔄 **Smart Fallback** | Discovers accessible Gemini text models before selecting fallbacks |
| 🎵 **Background Music** | Drop MP3s in `music library/` — auto-looped to fit video length |

---

## 🖥️ Screenshots

| | |
|---|---|
| ![Main GUI](docs/screenshots/gui_main.png) | ![Settings](docs/screenshots/gui_settings.png) |
| **Main dashboard** — generate videos with one click | **Settings panel** — configure every aspect |
| ![Review Dashboard](docs/screenshots/review_dashboard.png) | ![Banner](docs/screenshots/banner.png) |
| **Review dashboard** — watch before you publish | **Pipeline overview** |

---

## Narrative modes

- Realistic psychological fiction
- Philosophical thought experiment
- Dark human parable

Psychological and philosophical ideas are expressed through character choices and consequences. The production contract explicitly rejects explainer sections, lesson bullets, formulaic retention language, and fabricated factual authority.

---

## ⚡ Quick Start

```bash
# 1. Clone
git clone https://github.com/raunakpatil/youtube-agentic-ai-studio.git
cd youtube-agentic-ai-studio

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API keys (copy and edit .env.example)
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY and PIXABAY_API_KEY

# 4. Run the GUI
python gui.py
# → Opens at http://localhost:7070

# — OR — run the pipeline directly from the terminal
python pipeline.py
```

> **First run?** Read the full [Setup Guide](SETUP.md) — it takes about 10 minutes.

---

## 🏗️ How It Works

The pipeline runs 6 sequential phases:

```
Story brief ─► Character bible ─► Causal outline ─► Scene batches
                                                        │
YouTube ◄── Approval ◄── Packaging ◄── Video ◄── Exact scene audio timeline
```

| Phase | Agent | What it does |
|---|---|---|
| 1 · Story brief | `agents/researcher.py` | Scores multiple premises or preserves an exact override, then writes schema v2 |
| 2 · Narrative build | `agents/scriptwriter.py` | Creates/reuses the character bible and outline, then writes bounded scene batches |
| 3 · Narration | `video/narrator.py` | Renders each scene separately and writes a cumulative exact timeline and global SRT |
| 4 · Images | `video/stock.py` | Acquires validated JPEGs from local assets, Pixabay, then Openverse |
| 5 · Video | `video/creator.py` | Uses the exact audio timeline; fiction mode removes documentary cards, bullets, lower thirds, and progress UI |
| 6 · Review | `review/app.py` | Spins up a local dashboard — you watch and approve before anything is published |
| 7 · Upload | `uploader/youtube.py` | Publishes to YouTube with full SEO metadata on your approval |

`download_images(script, output_dir)` remains the stock subsystem's public
contract. It returns `{section_id: [local_jpeg_paths]}` for Shorts and
`{section_id: [local_jpeg_paths] | None}` for regular videos. The renderer uses
its existing animated gradient whenever a section has no usable image.

Every job writes `output/asset_manifest.json` with provider, creator, source,
license, original download URL, local filename and section usage. Treat this as
provenance—not legal clearance. Manually review recognizable people,
trademarks, sensitive fictional depictions and any use that could resemble
standalone redistribution of a stock asset.

---

## 📁 Project Structure

```
youtube-ai-agent/
├── 📄 pipeline.py              ← Run this to start the full pipeline
├── 🩺 diagnose_gemini.py       ← Explicit Gemini connectivity/model diagnostic
├── 🖥️  gui.py                  ← Run this for the visual GUI
├── ⚙️  config.py               ← Non-secret settings: colors, voice, video
├── 📦  requirements.txt
├── 🔑  client_secret.json      ← YouTube OAuth (you add yours — see SETUP.md)
├── 📝  .env.example            ← Copy to .env and fill in your API keys
│
├── agents/
│   ├── gemini_client.py        ← Gemini discovery, diagnostics and structured fallback
│   ├── narrative.py            ← Schema v2, deterministic validation, hashes and invalidation
│   ├── researcher.py           ← Story Development Agent
│   └── scriptwriter.py         ← Bible, outline, scene batches and quality review
│
├── video/
│   ├── narrator.py             ← Edge-TTS voiceover generator
│   ├── timeline.py             ← Exact per-scene audio/video timing contract
│   ├── stock.py                ← Pixabay/Openverse provider manager
│   ├── creator.py              ← Full video renderer (Ken Burns, captions, etc.)
│   └── music.py                ← Background music mixer
│
├── review/
│   ├── app.py                  ← Flask review server
│   └── templates/review.html  ← Review dashboard UI
│
├── uploader/
│   └── youtube.py              ← YouTube Data API v3 upload
│
├── gui_templates/
│   └── index.html              ← Full GUI (served by gui.py)
│
├── music library/              ← Drop your MP3/WAV tracks here
│
└── output/                     ← Auto-generated (gitignored)
    ├── story_brief.json
    ├── character_bible.json
    ├── story_outline.json
    ├── script.json
    ├── narrative_review.json
    ├── scene_timeline.json
    ├── asset_manifest.json
    ├── narration.mp3
    ├── images/
    └── final_video.mp4
```

---

## 🤖 Gemini Model Discovery and Fallback

The preferred candidates are:

```
gemini-3.8-flash → gemini-3.6-flash → gemini-3.5-flash-lite
```

Before generation, the client calls `models.list()` and keeps only models the
current project can access that explicitly support `generateContent`. Permanent
credential, permission and request errors fail immediately. Model-specific
availability and quota errors may use a verified fallback; transient retries are
bounded by the SDK.

Set the preference in the GUI or `.env`:

```dotenv
GEMINI_MODEL=gemini-3.8-flash
```

Availability, pricing and limits vary by account and project. Use **Settings →
AI Model → Check Available Models**, or run the explicit diagnostic:

```bash
python diagnose_gemini.py
```

## Tests and opt-in narrative smoke test

The default suite is offline and uses mocks/local fixtures:

```bash
python -m unittest discover -s tests -v
```

The Private Gravity smoke test is deliberately skipped unless explicitly
enabled. It uses real Gemini calls to create an exact short premise, character
bible, outline, complete Shorts screenplay, and saved schema-v2 artifacts. It
does not render, upload, or publish anything.

```powershell
$env:RUN_PRIVATE_GRAVITY_SMOKE_TEST="1"
python -m unittest tests.test_narrative.PrivateGravityIntegrationSmokeTest -v
```

Unset the variable afterward if you do not want later test runs to consume API
quota. Provider-specific Gemini and Pixabay smoke tests retain their own opt-in
flags documented in the test output.

---

## 🔑 API Keys and Services

| Service | Key | Free Tier | Where to get it |
|---|---|---|---|
| **Google Gemini** | `GEMINI_API_KEY` | Project-specific limits/pricing | [aistudio.google.com](https://aistudio.google.com/apikey) |
| **Pixabay** | `PIXABAY_API_KEY` | Provider limits apply | [pixabay.com/api/docs](https://pixabay.com/api/docs/) |
| **Openverse** | *(none)* | Public API limits apply | [api.openverse.org](https://api.openverse.org/v1/) |
| **Edge TTS** | *(none)* | Unlimited | Built-in |
| **YouTube API** | `client_secret.json` | Free | [Google Cloud Console](https://console.cloud.google.com) |
| **ElevenLabs** | `ELEVENLABS_API_KEY` | Optional | [elevenlabs.io](https://elevenlabs.io) |

Check each provider's current pricing and project limits before production use.

---

## 🎨 Customising Your Channel

Edit `config.py` to make the output yours:

```python
# 1. Private Gravity narrative defaults
CHANNEL_DESCRIPTION = """
A cinematic anthology exploring modern psychology through original fiction.
"""
CHANNEL_NAME = "Private Gravity"
NARRATIVE_TYPE = "auto"
TARGET_DURATION_MINUTES = 15
NARRATIVE_REVIEW_ENABLED = True

# 2. Pick your voice
VOICE_ID = "en-GB-RyanNeural"   # British, deep, cinematic

# 3. Brand colours (RGB tuples)
COLORS = {
    "primary":   (99, 102, 241),   # your brand colour
    "highlight": (251, 191, 36),   # accent / titles
    ...
}

# 4. Video style
CROSSFADE_DURATION = 0.7   # image transition smoothness
CINEMATIC_OVERLAY_OPACITY = 0.38
```

---

## ❓ FAQ

<details>
<summary><b>Does this cost anything to run?</b></summary>

Local rendering is free, while hosted-service cost depends on current provider terms:
- **Gemini** — availability, quotas and pricing vary by model and project
- **Pixabay** — free API key; provider limits apply
- **Openverse** — credential-free fallback restricted here to CC0/PDM results
- **Edge TTS** — completely free, no account needed
- **YouTube Data API** — free for uploads
- **Video rendering** — runs locally on your machine (CPU/GPU)

</details>

<details>
<summary><b>How long does one video take to generate?</b></summary>

Runtime depends on Gemini latency, story length, TTS, stock providers and CPU.
A 12–20 minute episode requires several bounded model calls (brief, bible,
outline, scene batches, plus optional review) and per-scene TTS. Rendering is
normally the longest local phase. No upload occurs before human approval.

</details>

<details>
<summary><b>Can I use this without the YouTube upload?</b></summary>

Yes. The review step lets you click "Reject" to skip the upload — the rendered MP4 will still be saved in `output/`. You can also comment out the upload step in `pipeline.py` entirely.

</details>

<details>
<summary><b>Why is moviepy pinned to 1.0.3?</b></summary>

MoviePy v2.0 removed the `moviepy.editor` module and changed the entire API. v1.0.3 is required. Similarly, NumPy must be `<2.0.0` because MoviePy's underlying C extensions were compiled against NumPy 1.x. **Do not upgrade either.**

</details>

<details>
<summary><b>The video renders with tiny/ugly text</b></summary>

Your system is missing the fonts the renderer looks for. Install DejaVu fonts:
- **Ubuntu/Debian:** `sudo apt install fonts-dejavu`
- **Mac:** `brew install --cask font-dejavu-sans-mono-nerd-font`  
- **Windows:** fonts are already present in `C:/Windows/Fonts/`

</details>

<details>
<summary><b>YouTube upload fails with "access denied"</b></summary>

Delete `youtube_token.pickle` and re-run. This forces a fresh OAuth flow. Make sure your Google account is added as a **Test User** in Google Cloud Console → OAuth consent screen.

</details>

---

## 🗺️ Roadmap

- [ ] Thumbnail generator (AI-designed cover art)
- [ ] Scheduler (auto-post at peak times)
- [ ] Multi-channel support
- [ ] Custom intro/outro clips
- [ ] Analytics dashboard
- [ ] Voice cloning via ElevenLabs

---

## 🤝 Contributing

Pull requests are welcome! Please open an issue first to discuss major changes.

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit: `git commit -m 'Add my feature'`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request

---

<div align="center">
Made with ❤️ by <a href="https://github.com/raunakpatil">@raunakpatil</a>
</div>



[![Star on GitHub](https://img.shields.io/github/stars/raunakpatil/youtube-agentic-ai-studio?style=social)](https://github.com/raunakpatil/youtube-agentic-ai-studio)
