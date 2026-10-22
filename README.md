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

### Timeouts, thinking and temperature

| Setting (`.env`) | Default | Meaning |
|---|---|---|
| `GEMINI_TIMEOUT_SECONDS` | `300` | Request deadline for long stages (sent to the server). A `504 DEADLINE_EXCEEDED` is retried once on the same model with twice the deadline (max 600 s), then falls back. |
| `GEMINI_THINKING_LEVEL` | *(empty)* | `minimal`, `low`, `medium`, `high`, or an integer token budget; empty keeps the model default. |
| `GEMINI_MAX_OUTPUT_TOKENS` | `0` | Optional output cap; `0` keeps the model limit. |
| `GEMINI_REPAIR_MAX_CHARS` | `120000` | Largest repair prompt; larger outline repairs only resend the invalid scenes. |

Every JSON stage uses JSON mode and its own temperature (`GEMINI_STAGE_TEMPERATURES` in
`config.py`: premise 0.9, bible 0.7, outline 0.6, scenes 0.85, expansion 0.7, editorial 0.2,
repair 0.2). Each call logs stage, model, prompt size, elapsed time and outcome — never prompt
content. When a stage fails, the raw response and the error are saved (secrets redacted) to
`<run_dir>/debug/<stage>-<n>.txt`.

### Overload, rate limits and resuming

A `503 high demand` or `429` is retried on the **same model** first: `GEMINI_OVERLOAD_RETRIES` (4)
waits of about 10/20/40/80 s (`GEMINI_OVERLOAD_BASE_DELAY`, `GEMINI_OVERLOAD_MAX_DELAY`, or the
server's Retry-After), then the fallback model gets the same budget; one call never exceeds
`GEMINI_MAX_CALL_WALLCLOCK` (600 s). Every wait is printed, Ctrl+C interrupts it, and each run ends
with a one-line Gemini usage summary (calls, retries, waiting time, fallbacks, failures).

Every stage is checkpointed. Inside the script stage each validated Gemini result (scene batches,
expansions, editorial review, semantic revision) is saved in `output/.checkpoints/` until the
stage completes, so re-running after a failure continues where it stopped: in the GUI leave
"Story Brief" unchecked, or run `python pipeline.py --resume`. To review the current `output/`
again without re-promoting: `python -m scripts.rereview_output --confirm-live`.

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

## Narrative QA and safe benchmarks

Private Gravity uses narrative contract v4 while keeping schema-v2 and contract-v3 artifacts readable. A scene's
canonical `section_type` (`opening`, `setup`, `development`, `reversal`, `climax`, `resolution`,
or `aftermath`) is separate from its free-text `narrative_function`.

For regular episodes, each scene must land within 85%–115% of its allocation and the complete
screenplay within 90%–110% of its planned word count. Falling below 85% of the total (more than
15% short) is blocking. A scene that is only slightly over its allocation (up to 130%) inside a
total within range is a minor pacing note; under-target scenes stay major. Shorts use a separate,
wider pacing contract. A failed check produces
exact per-scene word deficits and a bounded targeted expansion; it never silently pads the story.
Deterministic blockers cannot be cancelled by high editorial scores. The final statuses are
`structurally_invalid`, `revision_required`, `human_review_required`, and
`ready_for_human_review`. Every status still requires a person before upload.

Offline validation (no API, stock, TTS, render, or upload):

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m py_compile agents/narrative.py agents/scriptwriter.py gui.py
git diff --check
```

Optional live 12-minute narrative smoke test (not run automatically):

```powershell
$env:RUN_PRIVATE_GRAVITY_LONGFORM_SMOKE_TEST = "1"
python -m unittest tests.test_narrative.PrivateGravityLongFormSmokeTest
```

An ordinary valid response uses about 7–10 Gemini calls: brief, bible, outline, three scene
batches, zero to three targeted expansion batches, and editorial review. Any malformed artifact
may consume one additional bounded repair call. No stock, TTS, rendering, or upload is involved.

To regenerate the benchmark without overwriting `output/`, use the explicit opt-in command below.
It creates a new timestamped directory under `output/benchmarks/`; compare its JSON artifacts and
QA report with `tests/fixtures/architecture_of_envy_benchmark.json` before accepting changes.

```powershell
python -m scripts.regenerate_narrative_benchmark --confirm-live --minutes 12
# if it stops mid-way, reuse the valid brief/bible/outline already generated:
python -m scripts.regenerate_narrative_benchmark --confirm-live --minutes 12 --resume output/benchmarks/<dir>
```

The benchmark prints a per-stage timing table at the end, also on failure.

### Channel identity and title policy

`agents/identity.py` is the single source of the channel identity (promise, episode types A/B/C,
the Type A movements, craft rules such as *no stacked modifiers*, and the title policy). It is
injected into the story, outline, scene and editorial prompts. Outlines are contract v4: Type A
scenes declare `arc_movements`; a missing movement is a major (not blocking) finding.

Titles must present a person and a situation, never explain the thesis. "The Psychology of …",
"Why …", colon subtitles, "The <Noun> of …" and the theme word block publication; a title with no
concrete human anchor is a major finding. The Story Development Agent proposes five titles and
three thumbnail texts (max 5 words) and keeps the first passing pair. Each brief also states a
`psychological_angle`: one sentence naming the specific mechanism explored (for example "needing
to be envied as a substitute for self-worth"), distinct from the broad `psychological_theme`.

### Editorial review reliability and semantic revision

Word counts and duration are owned by the deterministic checks and passed to the editor as
facts. Editorial findings about duration or word count, or whose numbers (word counts, scene
counts, ages) contradict the script or Character Bible, are moved to
`discarded_editorial_findings` and never affect the status or the upload gate.

Remaining confirmed/probable major or blocking findings of type lecture, continuity,
unintroduced character, character consistency, agency, causality, repetition or style trigger
one bounded **semantic revision**: only the cited scenes are rewritten (each within 85–115% of
its budget, other scenes untouched), followed by one editorial re-review. Worst-case cost: +1
revision call and +1 re-review call per episode. Details are recorded under
`semantic_revisions` in `narrative_review.json`, and the pre-revision script is kept as
`script.pre_revision.json`. Switches: `NARRATIVE_SEMANTIC_REVISION_ENABLED`,
`NARRATIVE_SEMANTIC_REVISION_MAX_CALLS` (1), `NARRATIVE_SEMANTIC_REREVIEW`. A deterministic style
lint counts stacked modifiers and intensifiers per scene and adds a minor finding above
`NARRATIVE_STYLE_LINT_THRESHOLD` (3 per episode).

### Promote a benchmark and check render readiness

```powershell
python -m scripts.promote_benchmark --source output/benchmarks/<dir> --edits docs/prompts/edits/<file>.json [--rerun-editorial --confirm-live]
python -m scripts.render_preflight
```

`promote_benchmark` archives the current `output/` into `output/benchmarks/archive-<timestamp>/`
(once per distinct content), copies the benchmark artifacts into `output/`, applies human edits
(`{scene_id, find, replace}`, each `find` must occur exactly once or nothing changes), re-runs the
deterministic review and invalidates narration, images, video, thumbnail and any approval.
`render_preflight` is offline: it estimates narration per scene, flags scenes where one image
stays on screen too long, and checks moviepy 1.0.3, numpy < 2, ffmpeg, fonts, music, disk space,
Edge TTS and stock key presence. It exits non-zero on FAIL. `--live` also synthesises one short
sentence to confirm Edge TTS audio and word timings.

---

## Render quality

- **Encoding.** All text files are read and written as UTF-8. Narration and caption rendering
  stop with a clear error if a scene contains mojibake (`â€™`, `Ã©`…);
  `python -m scripts.repair_text_encoding` shows a dry-run diff and `--apply` repairs
  `output/*.json` (keeping `.bak` copies).
- **Narration.** Edge TTS is asked for `WordBoundary` events, each scene is retried on transient
  failures (2/5/10 s) and cached in `output/.tts_cache`, so editing one scene only re-synthesises
  that scene. Measured speed per voice and rate is saved in `data/voice_calibration.json` and
  used for planning: with `en-US-AndrewNeural` at −5% (≈151.6 wpm) a 12-minute episode targets
  ≈1 820 words. `NARRATION_WPM` is the fallback; `USE_CALIBRATED_WPM=false` disables it.
- **Captions.** Cues follow real word timings: ≤ 7 words / 42 characters, never across a
  sentence, 0.8–4.5 s, 80 ms lead-in (`CAPTION_*` settings in `config.py`).
- **Music and loudness.** Put licensed tracks in `music library/` and describe them in
  `music library/tracks.json` (`file`, `moods`, `license`, `source_url`, `attribution`); without
  metadata the mood is read from "Title - Artist - Genre - Mood" file names. The track is chosen
  by mood, looped with crossfades, ducked under the narration, and the final mix is normalised to
  −14 LUFS / −1 dBTP. Attribution is added to the YouTube description.
- **Stock (fallback provider).** Short 3–5-word queries, a tag-relevance threshold
  (`STOCK_MIN_RELEVANCE`), dark-palette tone filtering, text/signage rejection, and no
  identifiable people in shots about a specific character. Scores and rejection reasons are in
  `asset_manifest.json`.
- **Thumbnail.** The validated `thumbnail_text` (≤ 5 words) over the episode's strongest human
  shot (or the dedicated generated `thumbnail_bg.jpg`); the title is not repeated.

## Local image generation (ComfyUI + FLUX 3 Image Klein)

Generated images replace stock for an episode once every shot has an image; stock remains the
fallback for environment/object shots. Requirements: ComfyUI Desktop on `http://127.0.0.1:8188`
with the models named in `workflows/flux3_klein_edit_api.json` (UNET
`flux3-image-klein-int8-convrot`, text encoder `qwen3vl_4b_int8_convrot`, VAE `flux2-vae`).

- **Visual identity.** `VISUAL_STYLE = "dark_illustration"` (default; `cinematic_photo` is the
  legacy option): ink, scratchboard hatching and charcoal/gouache washes, cosmic dread carried by
  scale, fog and light only (no creatures), in the brand palette read from `config.COLORS`. The
  spec lives in `agents/identity.py` (versioned); its hash is part of every image cache key and of
  every approved reference, so a style change never reuses old images. Choose the variant with
  `python -m scripts.calibrate_style` (`VISUAL_STYLE_VARIANT`: `engraving` — the default —,
  `charcoal_gouache`, `hybrid`; each variant has its own recorded seed base, and
  `--variants engraving` re-renders only that column), then approve 1–2 of our own style boards
  (`python -m scripts.generate_style_refs`) as channel-wide style anchors.
- **Full-bleed frames, no pages.** Every style suffix asks for a single full-bleed illustration
  filling the frame edge to edge; Gemini is told never to request pages, panels, frames, borders,
  vignettes, paper edges or multiple views, and shot prompts containing `panel`, `comic page`,
  `storyboard`, `border`, `torn paper`, `paper edge` or `frame around` are rejected (also in the
  GUI editor). Every generated image is checked (`video/image_checks.py`): light, flat paper
  margins on ≥ 3 edges or a uniform strip along a full edge count as a border, light gutters
  crossing the frame count as a comic page. Such an image gets up to `IMAGEGEN_CHECK_RETRIES` (2)
  new seeds; a border that persists is cropped to the largest border-free 16:9 area and upscaled.
  The outcome (`accepted`, `regenerated`, `cropped`, …) is stored as `image_checks` in the
  manifest. Disable with `IMAGEGEN_IMAGE_CHECKS = False`.
- **Dread and crowds.** The `engraving` suffix asks for deep black negative space, one small warm
  light source and figures small within towering architecture; at least `IMAGEGEN_MIN_WIDE_SHARE`
  (40 %) of shots must be wide or establishing, and close-ups sit on a dark background with one
  light. Unnamed groups are rendered as a few distant, indistinct guests in shadow, each of a
  different age, build, posture and clothing — never clones.
- **Shot references.** Reference slots per shot: characters (≤ 2) → location (≤ 1) →
  style anchor (≤ 1). In this style stock is never used for shots: a failed shot gets two new
  seeds, then the previous shot is held longer. The video overlay is lighter
  (`ILLUSTRATION_OVERLAY_OPACITY`), and the synthetic-content disclosure is a reviewer decision
  that defaults to "not realistic".
- **Templates.** `workflows/flux3_klein_t2i_api.json` and `flux3_klein_ref_api.json` are derived
  from the exported edit workflow (layout and upsampler removed; up to 4 references through
  `ReferenceMultiLatent`). Every graph is validated against ComfyUI's `/object_info` before it is
  queued; if a node or model is missing you are asked to export that variant from ComfyUI.
- **Visual bible** (`visual_bible.json`, one Gemini call): style suffix in the brand palette, an
  immutable descriptor and per-scene wardrobe for each character, reusable location descriptors,
  a thumbnail shot, and fixed seeds.
- **Shot list** (`shot_list.json`, after narration, one Gemini call): cuts on sentence boundaries,
  6–12 s per shot, at most `IMAGEGEN_MAX_UNIQUE_SHOTS` (50) unique images, a mix of framings with
  40–60% of shots without a full face. Prompts and reference slots are composed in code: approved
  character portraits first ("the man from the first image"), then the location reference.
- **Identity.** Approve one of four generated portraits per character (and optionally one of two
  establishing images per recurring location) before any shot is generated. If identity
  retention is weak, set `IMAGEGEN_IDENTITY_POLICY=limited_faces` (≤ 6 full-face shots).
- **Generation.** ~1 MP 16:9 (1360×752), Lanczos-upscaled to 1920×1080; cached by prompt, seed,
  size, template, models and reference hashes; resumable; one retry with a new seed, then stock.
  At about 1 minute per image a 50-shot episode needs roughly 50–60 minutes of GPU time.
- **Renderer.** One image per shot for its exact timing, motion by shot type (push-in on
  close-ups, lateral drift on wides), short crossfades, overlay adapted to image brightness.
- **Disclosure.** Runs with generated images upload with `containsSyntheticMedia=true` and the
  review checklist gains a synthetic-media item.

```powershell
python -m scripts.render_preflight --live        # also checks ComfyUI and Edge TTS
python -m scripts.calibrate_imagegen             # seconds/image, VRAM, t2i vs reference contact sheet
python -m scripts.generate_character_refs        # 4 portraits per character; approve in the GUI
python -m scripts.generate_character_refs --approve character:<id>:<n>
python -m scripts.generate_shots --limit 5       # quick look first
python -m scripts.generate_shots                 # full episode (resumable)
```

In the GUI the same work is available as the "Visual bible" and "Shots (ComfyUI)" steps and in
the Review tab (candidates, approvals, per-shot preview, new seed, edit).

---

## Publishing: approval gate, ledger and banned topics

Uploads are **public by default**, so every upload (GUI or `pipeline.py`) requires a recorded
review in `<run_dir>/review_approval.json`: watched the full video, read the narrative review,
title and thumbnail text pass the policy (or a written override), license log reviewed,
description and tags reviewed. The approval is bound to hashes of the script, video, title,
description, tags and thumbnail; any later change invalidates it. Status `revision_required` or
`structurally_invalid` additionally needs a typed override reason, stored in the approval and
in the ledger.

Each upload is recorded in `data/publications.json` as pending. A topic enters
`banned_topics.txt` only when YouTube reports the video as public and processed (scheduled
videos stay pending until `publishAt`). Sync runs when the GUI starts and every
`PUBLICATION_SYNC_INTERVAL_MINUTES` (30), at pipeline start, before automatic premise
generation, and on demand:

```powershell
python -m scripts.sync_publications
python -m scripts.register_publication --video-id <id> --run-dir <dir>   # manual uploads
```

Sync uses `videos.list`, which requires the `youtube.readonly` scope in addition to
`youtube.upload`; a stored token without it triggers a one-time re-consent in the browser.
Banned-topic lines can be plain premises or `theme: … | angle: … | premise: … | title: … |
video: … | published: …`; the same theme may return from a clearly different angle.

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
