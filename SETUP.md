# Private Gravity Studio: Setup Guide

This guide takes you from a fresh clone to a rendered episode. The core needs only Python, ffmpeg and a Gemini key; the local media stack (ComfyUI for images, Kokoro for narration, local visual QA) is what the channel uses in production, and each piece can be added later.

| Level | What you get | What you need |
|---|---|---|
| **1. Core** | Story brief, character bible, outline, screenplay and editorial review | Python, ffmpeg, Gemini API key |
| **2. Images** | Generated shots, references, thumbnails | ComfyUI + FLUX 3 Image Klein models (a GPU with 8 GB VRAM works) |
| **3. Narration** | Local channel voice with alignment and stutter QA | Kokoro in its own venv, faster-whisper |
| **4. Visual QA** | Automatic checks on every shot | OpenCV models (CPU) + a local VLM |
| **5. Publishing** | Upload, scheduling, publication sync | Your own Google OAuth client |

Edge TTS (free, no key) and stock images (Pixabay/Openverse) remain available as fallbacks, so you can render a full video before setting up levels 2 and 3.

---

## 1. Core

### 1.1 Prerequisites

- Python **3.10+**
- **ffmpeg** on your PATH (used for rendering and the final film-look pass)

| OS | Install ffmpeg |
|---|---|
| Windows | `winget install Gyan.FFmpeg` (or download from ffmpeg.org and add `bin/` to PATH) |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |

Check with `ffmpeg -version`.

### 1.2 Clone and install

```bash
git clone https://github.com/comscijb/youtube-agentic-ai-studio.git
cd youtube-agentic-ai-studio
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

> `moviepy==1.0.3` and `numpy<2` are pinned on purpose. Don't upgrade them in this environment; Kokoro, which needs numpy 2, gets its own venv in step 3.

### 1.3 Gemini API key

1. Create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Copy the example settings file and add the key:

```bash
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

```dotenv
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-3.6-flash
GEMINI_TIMEOUT_SECONDS=300
```

`.env` is gitignored. The GUI can also save keys there; it never writes secrets into `config.py` or returns them from its API. Process-level environment variables win over `.env` on a fresh start.

Check which models your key can use (one small request, the key is never printed):

```bash
python diagnose_gemini.py
```

The client discovers accessible models at runtime and falls back automatically; see [docs/OPERATIONS.md](docs/OPERATIONS.md) for timeouts, overload handling and `--resume`.

### 1.4 First run

```bash
python gui.py          # studio at http://localhost:7070
```

Create a project from **Novo projeto** (new project), or from the command line with a premise brief:

```bash
python -m scripts.project new --brief-file briefs/<brief>.md --minutes 11
python -m scripts.project list
```

Each project lives in its own folder under `projects/`, and every stage can be re-run from the project view. Before the first render, check the toolchain offline:

```bash
python -m scripts.render_preflight
```

> The studio UI is in Portuguese. Stage names: Premissa (premise), Roteiro (script), Narração (narration), Música (music), Referências (references), Planos (shots), Vídeo, Publicação (publish).

---

## 2. Images: ComfyUI + FLUX 3 Image Klein

Shots, character references and thumbnails are generated locally through ComfyUI's HTTP API.

1. Install [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and start it (default `http://127.0.0.1:8188`).
2. Put the models the graphs in `workflows/` expect in ComfyUI's model folders:

| File | ComfyUI folder |
|---|---|
| `flux3-image-klein-int8-convrot.safetensors` | `models/diffusion_models/` |
| `qwen3vl_4b_int8_convrot.safetensors` | `models/text_encoders/` |
| `flux2-vae.safetensors` | `models/vae/` |

3. Point the studio at ComfyUI if it is not on the default address:

```dotenv
IMAGEGEN_ENABLED=true
IMAGEGEN_COMFYUI_URL=http://127.0.0.1:8188
```

4. Measure speed and VRAM on your machine, and pick the drawing style:

```bash
python -m scripts.calibrate_imagegen     # seconds per image, VRAM, contact sheet
python -m scripts.calibrate_style        # style variants
python -m scripts.generate_style_refs    # channel style anchors
```

**Without ComfyUI:** set `IMAGEGEN_ENABLED=false` and `STOCK_ENABLED=true`. Stock uses approved local assets first (`assets/stock_approved/`), then Pixabay (free key at [pixabay.com/api/docs](https://pixabay.com/api/docs/), `PIXABAY_API_KEY`), then Openverse, accepting only CC0 or Public Domain Mark results. Provenance is logged per asset.

**Optional parallax:** 2.5D motion estimates depth with Depth Anything V2 on the CPU. Install `transformers` (and PyTorch) to enable it; without it, shots fall back to Ken Burns motion.

---

## 3. Narration: Kokoro (local)

The project pins numpy below 2, while Kokoro needs numpy 2, so Kokoro runs out of process in its own venv, `.venv-tts`.

1. Create the venv and install Kokoro:

```bash
python -m venv .venv-tts
# Windows: .venv-tts\Scripts\pip install kokoro-onnx soundfile
# macOS/Linux: .venv-tts/bin/pip install kokoro-onnx soundfile
```

2. Download `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases) into `models/tts/` (not tracked by git).

3. In the main environment, install faster-whisper, used for word alignment and the stutter check:

```bash
pip install faster-whisper
```

4. Select the backend and voice in `.env`:

```dotenv
TTS_BACKEND=local
TTS_LOCAL_ENGINE=kokoro
TTS_VOICE=bm_lewis
TTS_LOCAL_DEVICE=auto        # auto | cpu | cuda
# TTS_LOCAL_PYTHON=          # only if the venv is somewhere other than .venv-tts
```

5. Audition voices side by side (render time, timing drift, loudness, stutter hits), then pick the channel voice in **Configurações → Vozes**:

```bash
python -m scripts.voice_audition --catalogue
```

   The measured speaking rate per voice is kept in `data/voice_calibration.json`; the script stage uses it to plan word counts.

Other backends behind the same interface: `edge` (default, free, no key), `elevenlabs` (`ELEVENLABS_API_KEY`), and Chatterbox as a local engine (`TTS_LOCAL_ENGINE=chatterbox`, `pip install chatterbox-tts`).

---

## 4. Visual QA

Generated shots go through layered checks before you review them: deterministic checks, then OpenCV detectors, then a vision-language model.

1. Download the CPU detectors (YuNet, YOLOX-s, SFace; about 75 MB) into `models/qa/`:

```bash
python -m scripts.setup_qa_models
python -m scripts.setup_qa_models --check
```

2. Choose the VLM layer:

```dotenv
IMAGEGEN_VISUAL_QA=true
IMAGEGEN_QA_LAYERS=deterministic,local_vlm
IMAGEGEN_QA_VLM_BACKEND=comfy_textgenerate     # reuses ComfyUI and the Qwen3-VL encoder above
# IMAGEGEN_QA_VLM_BACKEND=ollama                # alternative: a local Ollama server
# IMAGEGEN_QA_OLLAMA_MODEL=qwen2.5vl:3b         # never pulled automatically: ollama pull it yourself
IMAGEGEN_QA_GEMINI=false                        # true adds Gemini vision as a layer
```

Check that the local VLM answers with `python -m scripts.probe_local_vlm`. Failed shots are repaired with region edits where possible (`IMAGEGEN_QA_MAX_EDITS`) before being regenerated.

---

## 5. Publishing to YouTube

Skip this section to produce videos locally only.

### 5.1 Create an OAuth client

1. In [Google Cloud Console](https://console.cloud.google.com), create a project.
2. **APIs & Services → Library**: enable **YouTube Data API v3**.
3. **OAuth consent screen**: User type **External**; add the scopes `youtube.upload` and `youtube.readonly` (the second lets the studio detect when an upload becomes public); add your Google account as a test user.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**, then download the JSON.
5. Save it as `client_secret.json` in the project root. It is gitignored; never commit it.

The first upload opens a browser for consent and stores `youtube_token.pickle` locally (also gitignored). If you see "Google hasn't verified this app", that is expected for an app in testing mode: **Advanced → Go to (app name)**.

### 5.2 Approval gate

Uploads are refused until the review checklist in **Revisão e edição** (review and edit) is complete. The approval is bound to hashes of the script, video, title, description and thumbnail, so any change after approval requires a new one. Uploads are public by default; scheduling uploads as private with a publish time. A narrative status of `revision_required` or `structurally_invalid` additionally needs a typed override reason.

### 5.3 Publication ledger

Every upload is recorded in `data/publications.json`. A video's topic is added to `banned_topics.txt` only after YouTube reports it public. Sync runs when the GUI starts, every 30 minutes while it runs (`PUBLICATION_SYNC_INTERVAL_MINUTES`), at the start of `pipeline.py`, and on demand:

```bash
python -m scripts.sync_publications
python -m scripts.register_publication --video-id <id> --run-dir <dir>   # register a manual upload
```

A token created before `youtube.readonly` was added lacks that scope; `sync_publications` detects this and asks for consent again.

---

## 6. Music

Put `.mp3` or `.wav` files in `music library/`. Tracks are looped to the episode length, ducked under the narration and loudness-normalised. Use only music you have the rights to publish.

---

## 7. Tests

The suite runs offline; every external service is mocked.

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Live checks are opt-in, for example:

```bash
python -m scripts.regenerate_narrative_benchmark --confirm-live --minutes 12
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ffmpeg not found` | Install ffmpeg and make sure it is on your PATH, then reopen the terminal |
| Gemini `504` / deadline exceeded | Raise `GEMINI_TIMEOUT_SECONDS`; the client already retries once with a longer deadline before falling back |
| Gemini `429` / `RESOURCE_EXHAUSTED` or overloaded | Run `python diagnose_gemini.py`; resume the run later with `--resume` instead of starting over |
| Gemini `401` / `403` | Replace the key or check that its project has Gemini API access |
| ComfyUI model name mismatch | Compare the files in ComfyUI's model folders with the table in section 2 |
| ComfyUI out of memory | Lower `IMAGEGEN_SIZE` in `config.py` or close other GPU apps; the studio frees ComfyUI memory every `IMAGEGEN_FREE_MEMORY_EVERY` shots |
| "Kokoro is not installed" | Create `.venv-tts` with `kokoro-onnx` and put both weight files in `models/tts/` |
| "forced alignment needs faster-whisper" | `pip install faster-whisper` in the main environment |
| `youtube_token.pickle` auth error | Delete the file and upload again to re-authenticate |
| Upload button disabled | Complete the review checklist; an edit after approval invalidates it |
| Port 7070 in use | Stop the other process, or change the port at the bottom of `gui.py` |

More detail on every subsystem is in [docs/OPERATIONS.md](docs/OPERATIONS.md). Problems or questions: [open an issue](https://github.com/comscijb/youtube-agentic-ai-studio/issues).
