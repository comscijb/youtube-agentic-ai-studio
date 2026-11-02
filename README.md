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

## Projects: continue work in progress

Every episode lives in its own folder, `projects/<date>-<title-slug>/` (for example
`2026-10-23-the-man-who-needed-everyone-to-envy-him`), with the same artifact names the old
single `output/` folder used, plus `project.json` (title, created/updated, video type, target
duration, stage status, published video id). Channel-wide assets stay outside: `assets/style_refs`,
`music library`, `data/`. Character and location references are per project.

- **Move the old `output/`:** `python -m scripts.migrate_output_to_project --dry-run`, then without
  `--dry-run`. It moves everything except `benchmarks/`, archives and the channel-wide calibration
  renders, rewrites JSON paths, tags the publication ledger, makes the project active and leaves
  `output/MIGRATED.txt`; nothing is deleted.
- **Active project:** `--project <slug>` on every script (`generate_shots`, `generate_character_refs`,
  `generate_style_refs`, `calibrate_*`, `rereview_output`, `render_preflight`,
  `repair_text_encoding`, `register_publication`, `pipeline.py`), the GUI selection, else the
  project last opened, else the most recently updated one. `--output-dir` still points a script at
  any folder, and with no project at all the legacy `output/` is used.
- **Stage status** comes from the artifacts and their `source_hash` chain: `done`, `stale` (an
  upstream artifact changed), `partial` (e.g. shots 5/50), `missing` or `skipped`, with the next
  recommended step. `python -m scripts.project list | show <slug> | open <slug>`.
- **GUI:** the home screen offers **Novo projeto** (the usual flow; the folder is renamed after
  the Story Brief title) and **Continuar projeto** (title, date, thumbnail, stage badges, next
  step). The **Project** tab runs, re-runs or skips each stage, reuses existing artifacts and
  checkpoints, never forces the Story Brief, and warns when an upstream stage is stale or missing
  (re-run it first, or run anyway). Approvals open inside the project; uploads record the
  project slug in the ledger.

## Archive published projects (SSD → HDD)

After a video is **published**, its project moves to `ARCHIVE_ROOT` (`D:\Private Gravity\Archive`
by default) so the SSD doesn't fill up: automatically when `sync_publications` marks it published
(`ARCHIVE_ON_PUBLISH=true`; if the drive is missing or short of space it is queued in
`data/archive_queue.json` and retried on the next sync, never blocking generation), with the GUI
button "Arquivar", or with `python -m scripts.archive_project --project <slug> [--dry-run]`
(`--force --reason "..."` for an unpublished project, `--keep-cache` to keep the caches).

- Layout `<slug>\`: `video\`, `images\` (shots, character/location refs, the style anchors used),
  `audio\` (narration, mix, `music\<track>`), `captions\`, `text\` (brief, bible, outline, script,
  reviews, visual bible, shot list and report, `youtube_metadata.json`, approval, project.json),
  `manifests\` (asset manifest, timeline, `archive_manifest.json`), `logs\debug\`; unknown files go
  to `other\`. Every artifact is mapped explicitly in `studio/archive.py`.
- Project files are **moved** (copy → SHA-256 verify → delete), the music track and style anchors
  are **copied**, regenerable caches are **deleted**. Sources are deleted only after the whole
  project verified; a hash mismatch aborts without deleting anything; an interrupted run resumes;
  the target needs 1.2 × the project size; an archive folder with other content is never
  overwritten (`<slug>__2`). A stub stays on the SSD (`project.json` with status `archived`,
  `ARCHIVED.txt`), so the GUI lists it as "arquivado".
- `python -m scripts.restore_project --project <slug>` copies it back for a re-render or a Short;
  the archive stays intact.

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
| 4 · Images | `video/shot_generation.py` | Generated shots: visual bible → shot list → ComfyUI (stock only when `STOCK_ENABLED`) |
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
  −14 LUFS / −1.5 dBTP. The true peak is measured again on the final MP4: above −1.0 dBTP
  (`FINAL_TRUE_PEAK_MAX`) the audio is limited at −1.5 dBTP and remuxed without re-encoding the
  video. The render uses `RENDER_PRESET = "veryfast"` with `RENDER_CRF = 20` (constant quality,
  ~4–8 Mbps for 1080p24) and logs size, duration and bitrate. Attribution is added to the YouTube
  description.
- **Stock (opt-in fallback, `STOCK_ENABLED = False` by default).** When disabled no Pixabay or
  Openverse request is ever made, in any style, and preflight needs no stock keys; the GUI
  Settings checkbox "Usar banco de imagens (Pixabay/Openverse) como fallback" turns it on. Then
  stock is used only in `cinematic_photo` mode or as a last resort for environment/object shots
  after generation retries, and is recorded in the manifest. Short 3–5-word queries, a tag-relevance threshold
  (`STOCK_MIN_RELEVANCE`), dark-palette tone filtering, text/signage rejection, and no
  identifiable people in shots about a specific character. Scores and rejection reasons are in
  `asset_manifest.json`.
- **Thumbnail.** The validated `thumbnail_text` (≤ 5 words) over the episode's strongest human
  shot (or the dedicated generated `thumbnail_bg.jpg`); the title is not repeated.

## Local image generation (ComfyUI + FLUX 3 Image Klein)

The GUI "Images" step is shot generation (visual bible → shot list → ComfyUI, after narration).
Stock is off unless `STOCK_ENABLED` is set. Requirements: ComfyUI Desktop on `http://127.0.0.1:8188`
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
  filling the frame edge to edge; Gemini is told never to request comic panels, page layouts,
  borders around the image, paper edges or multiple views, and shot prompts containing page-layout
  phrases (`comic panel(s)`, `multiple panels`, `split-screen`, `triptych`, `comic page`,
  `storyboard`, `border around`, `torn paper`, `frame around`, … — see `identity.LAYOUT_TERMS`)
  are rejected (also in the GUI editor). Plain "panel" or "border" (wall panels, the border of
  the light) stay allowed. Every generated image is checked (`video/image_checks.py`): light, flat paper
  margins on ≥ 3 edges or a uniform strip along a full edge count as a border, light gutters
  crossing the frame count as a comic page. Such an image gets up to `IMAGEGEN_CHECK_RETRIES` (2)
  new seeds; a border that persists is cropped to the largest border-free 16:9 area and upscaled,
  but only when that cuts at most 6 % per side (deeper margins fail the attempt and the shot is
  regenerated). The outcome (`accepted`, `regenerated`, `cropped`, …) is stored as
  `image_checks` in the manifest. Disable with `IMAGEGEN_IMAGE_CHECKS = False`.
- **Art direction.** Dread comes from light, shadow, scale and stillness — never from decay,
  dirt, damage or injury the screenplay does not state; settings keep the status the story gives
  them (an expensive loft stays immaculate). The shot planner (shot list version 2) returns, per
  location, the condition the story gives it; per shot, each visible character's grooming and
  expression at that moment (added to the prompt after the scene's wardrobe) and
  `others_present` — only when the narration or the scene's chronology places unnamed people in
  the room; otherwise the prompt says the characters are alone. Injury, decay and tears words
  (`blood`, `wound`, `scratch`, `bruise`, `injur*`, `cut on`, `grimy`, `stained`, `tearful`, …)
  are reported as advisory notes when the whole scene's narration has nothing of the same family
  (tears also cover `cry*`, `sob*`, `wept`, a wet face, `choked`, `blotched`). "Two figures" with
  two listed characters is the cast itself; only `guests`, `crowd`, `strangers`, `waiter`, `staff`,
  `onlookers`, `passers-by`, `people in the background`, or a stated number above the cast count
  as other people, and silhouettes of a skyline, buildings, trees or furniture are scenery.
- **Shot list per scene.** The code owns the skeleton (shot ids, timings and narration excerpts
  from the narration timeline); Gemini returns only the ordered shot contents, one call per scene,
  with the scene's narration, its numbered segments and the previous scene's last shot for
  continuity. Each scene gets up to two repairs (only the failing shots plus neighbours, merged by
  position; a wrong shot count re-asks that scene). A scene that still fails keeps its best
  attempt with those shots **flagged** and never aborts the others. Completed scenes are
  checkpointed in `.checkpoints/shot_list/<scene>.json`, so a rerun (also with
  `--rebuild-shot-list`) only redoes missing or flagged scenes; delete that folder to re-plan all.
- **Blocking vs advisory shot rules.** Only what makes a shot unusable blocks: excluded subjects
  (creatures, tentacles, gore…), page-layout phrases, empty or invalid fields and a wrong shot
  count. Those are repaired and, if they remain, the shot is flagged and skipped until edited.
  Content doubts (unstated injuries/decay/tears, unnamed people, setting conditions) never block:
  they appear as `warnings` on the shot card and in `shot_list_report.json`, and the prompt still
  carries the positive hints (alone in the room, composed). In the GUI each shot card shows its
  flags (⛔) and warnings (⚠); editing a shot re-validates it, clearing its flag (a blocking edit
  is refused) and recomputing its warnings. `generate_shots` prints a warnings summary, generates
  every unflagged shot and lists the flagged ones at the end. The share of wide/establishing shots (`IMAGEGEN_MIN_WIDE_SHARE`, 0.3, from `.env`) and of
  shots without a full face are targets given to Gemini and reported as `warnings` in
  `shot_list.json`, never a failure. The crimson accent examples are wine, a ring, a scarf
  or a candle reflection; this wording change keeps the style hash, so approved references stay
  valid.
- **Always 16:9.** The frame comes from the sampler latent only: `IMAGEGEN_LATENT_SOURCE =
  "empty_16x9"` (default; `reference` makes the output follow the first reference's size and is
  kept for edit-like uses). Character and location references keep their own sizes. Klein rounds
  latent sizes (an edit of a 1920×1080 shot comes back 1360×768), so an output within
  `IMAGEGEN_ASPECT_TOLERANCE` (3 %) of 16:9 is scaled to cover 1920×1080 and centre-cropped by at
  most that much. Further off, a generation is rendered once more from an empty 16:9 latent and
  fails with a clear message if it is still wrong; a region edit is never re-rendered from an
  empty latent: it fails and the original shot stays. The latent source is part of the image
  cache key.
- **Dread and crowds.** The `engraving` suffix asks for deep black negative space, one small warm
  light source and figures small within towering architecture; close-ups sit on a dark background
  with one light. Unnamed groups are rendered as a few distant, indistinct guests in shadow, each of a
  different age, build, posture and clothing — never clones.
- **Shot references.** Reference slots per shot (4): every listed character with an approved
  sheet, whatever the face visibility (≤ 3, the most important first) → location → style anchor;
  the anchor is dropped first, then the location, never a listed character, and a 4th+ character
  becomes a distant, indistinct figure. A failed shot gets two new seeds, then (only with `STOCK_ENABLED`, and only
  for shots without characters) stock, then the previous shot is held longer. The video overlay is lighter
  (`ILLUSTRATION_OVERLAY_OPACITY`), and the synthetic-content disclosure is a reviewer decision
  that defaults to "not realistic".
- **Templates.** `workflows/flux3_klein_t2i_api.json` and `flux3_klein_ref_api.json` are derived
  from the exported edit workflow (layout and upsampler removed; up to 4 references through
  `ReferenceMultiLatent`). Every graph is validated against ComfyUI's `/object_info` before it is
  queued; if a node or model is missing you are asked to export that variant from ComfyUI.
- **Visual bible** (`visual_bible.json`, one Gemini call): style suffix in the brand palette, an
  immutable descriptor and per-scene wardrobe for each character, reusable location descriptors,
  a thumbnail shot, and fixed seeds. The descriptor is physical identity only (no moods, states,
  grooming disorder, injuries or dirt; a scar only when the screenplay has one) and an outfit is
  clothing and accessories only (no skin, hands, dirt, stains or marks); the validator rejects
  both, so story details such as soil on her fingers live only in the shot whose narration has them.
- **Clean prompts (visual bible v3).** Each character carries `signature_markers` (1–3
  always-visible traits such as horn-rimmed glasses) and `scene_states` ({scene: state}); outfits
  and grooming may not name scene ids, and the descriptor starts with age and a gender noun so a
  reference is "the man from the first image", never "the person". A shot prompt has one clause
  list per character: the reference name (the full descriptor only without a reference) + markers
  + this scene's outfit + this scene's state + this moment, with phrases about other scenes
  removed, repeated clauses and a setting that repeats itself dropped, and each part capped.
- **Visual QA, local first (Phase 3.5).** `IMAGEGEN_VISUAL_QA=true`, layers in `IMAGEGEN_QA_LAYERS`
  (default `deterministic,local_vlm`):
  1. *Layer 1, CPU, every shot* — OpenCV DNN detectors (`video/qa/deterministic.py`): YOLOX-s
     counts people (backs and small background figures included), YuNet finds faces, SFace compares
     them. It gives the headcount, extra people (blocking unless the scene has unnamed guests), the
     same face twice, borders/panels and, when Tesseract is installed, lettering — **all boxes come
     from here**. Install once: `pip install opencv-python-headless==4.11.0.86` and
     `python -m scripts.setup_qa_models` (~75 MB of ONNX models in `models/qa/`, not in git).
  2. *Layer 2, local VLM* — `qwen3vl_4b_int8_convrot` through ComfyUI's `TextGenerate` (chosen by the
     probe, `data/qa_probe_report.json`; `IMAGEGEN_QA_VLM_BACKEND=ollama` is the alternative, never
     pulled automatically). It never locates anything: it answers one-word questions about crops
     (glasses on this face? man or woman? blood or red marks? any writing? same person?).
  3. *Layer 3, Gemini vision* — off (`IMAGEGEN_QA_GEMINI=false`); only when the local VLM is
     unavailable, or `qa_shots --gemini`.

  **QA is advice by default (Phase 3.5.1).** On the confirmed fixtures the local QA was not reliable
  (people count 25 %, duplicate 62 %, glasses 62 %, extra person 75 %: it passed real defects and
  flagged clean frames; in this style every face shares one drawing). `IMAGEGEN_QA_MODE` /
  `generate_shots --qa` (`video/qa/policy.py`):
  - `advisory` (default): every issue is stored, but only the reliable deterministic checks act on
    their own: borders/panels, blank or unreadable frames, text read by OCR. Everything else
    (VLM answers, people counts) is a **hint** for the human review;
  - `auto`: an issue type also acts on its own when its accuracy in the latest
    `data/qa_probe_report.json` is ≥ 85 %; `duplicate_person` never does (low-confidence hint);
  - `off`: no QA.

  Stored results are re-evaluated under the current mode, so hints from earlier runs are never
  "fixed" automatically. When something does act, the verdict is code (`video/qa/decision.py`):
  **edit** for ≤ 2 small boxed or instruction-only issues, **regenerate** otherwise.

  **Region edits** are FLUX 3 Klein edits (`workflows/flux3_klein_regionedit_api.json`, derived from
  your edit export: the shot is `<ref_image_0>`, boxes are `<obj_N>`, approved portraits follow).
  Since Phase 3.5.3 every boxed edit is **crop → edit → composite** (`video/qa/region.py`): a 16:9
  crop around the box (×3 on its narrow side, ≥ 25 % of the frame's shorter side) is upscaled to
  ~1 MP and edited alone, so a 3 %-wide figure becomes ~11 % of the edited canvas and can be grounded;
  the result is tone-matched and pasted back through a feathered mask (the box grown by 15 %) —
  **nothing outside the mask can change** (asserted). Instructions name a removal target by its
  size and position, list the characters visible in the crop with their outfits ("Keep the woman in
  the cream knit top exactly as they are") and end with "Change nothing outside <obj_1>". An edit
  that changes nothing inside the box is reported as "edição sem efeito" and not saved; the person
  detector checks that a removed figure is gone. The GUI warns before queuing a box that covers
  > 20 % of a main character. Instruction-only edits keep the whole-frame path (with keep clauses).
  Klein returns 1360×768 for a 1920×1080 frame; any output within `IMAGEGEN_ASPECT_TOLERANCE` (3 %)
  of 16:9 is fitted, and an edit is never re-rendered from an empty latent. Every version is saved
  in `images/shots/history/<shot_id>_vN.jpg` (crops as `_vN_crop_before/after.jpg`).

  **Human review in the GUI** (Continuar projeto → Shots, Phase 3.5.2). The grid is compact: thumbnails
  S / M / L (240 / 360 / 520 px, remembered), grouped by scene, each card with only its id, time,
  type, a badge (`OK`, `n avisos`, `revisado`, `editado`, `na fila`) and the first real warning.
  Filters: Todos · Com avisos · Não revisados · Editados · OK, and an "x/66 revisados" counter.
  "Unclear" VLM answers are hidden ("mostrar incertos" shows them) and never count as warnings.

  Click a shot (or Enter) to open the **viewer**: the full-resolution image (fit / 100 % / wheel zoom,
  drag to pan, detector boxes on/off), the narration of that moment, the characters' approved
  portraits, the hints (real ones first, repeats collapsed, "Ignorar"), the QA layers and date, the
  composed prompt, the history ("Antes / depois", "Restaurar vN"). Actions — **Remover figura**,
  **Adicionar marcador** (character + signature marker, its portrait as reference), **Remover
  marcas**, **Instrução livre**, **Gerar de novo**, **Marcar OK** — use boxes drawn on the large image
  (click a suggested detector box or drag one, then move/resize it; "Sem caixa" for instruction-only
  edits). Edits join the queue; "Executar fila" runs them in one batch with Klein loaded, and the
  viewer shows "na fila / processando / pronto" and refreshes the image. Keys: ← → shot, O OK (and
  next), G regenerate, I instruction, F remove figure, M marker, B boxes, Z fit/100 %, H history,
  Esc close.

  **Gemini second opinion** (optional, live): "Segunda opinião (Gemini)" on a card or for every shot
  with hints, or `python -m scripts.qa_shots --backend gemini [--only sNN_MM]`. Stored beside the
  local result, advisory only.

  `generate_shots` stays phase-batched (Klein and the VLM never share the 8 GB) and ends with the
  shots that have hints and the GUI command. Re-check existing shots with
  `python -m scripts.qa_shots [--layers ...] [--mode advisory|auto]`. Probe:
  `python -m scripts.probe_local_vlm --mode focused`, and `--backend gemini` adds Gemini on the same
  fixtures, side by side in the report.

  ```powershell
  python -m scripts.generate_shots --qa advisory      # regenerates nothing already OK; stores hints
  python gui.py                                       # Continuar projeto → Shots: review hints, one-click edits
  python -m scripts.probe_local_vlm --backend gemini  # optional comparison
  ```
- **Thumbnail identity.** The thumbnail is composed like a shot: the protagonist's approved sheet
  as the first reference (it refuses to run without it, so it never shows a generic face), their
  signature markers, the brief's `thumbnail_concept`, and a figure whose head and shoulders fill at
  least half the frame height on the right third, with negative space on the left for the text.
  The layered QA and the edit → regenerate path run on it like on every shot (`thumbnail_qa.json`).
- **Neutral character sheets.** Reference portraits are head-and-shoulders sheets (facing the
  viewer and three-quarter) on a plain flat dark-grey backdrop, in a simple plain dark top, with a
  neutral expression, even light and the drawing technique without environment clauses: the
  reference carries identity, while every shot prompt states the scene's outfit, grooming and
  expression. Approvals record `CHARACTER_SHEET_VERSION`; an older sheet must be re-approved.
  Only stale references (another style, an older sheet) are archived — valid location references
  stay, and get no new candidates.
- **Explicit headcount.** Every prompt states the exact number and identity of the people:
  "Exactly two people in the scene: the man from the first image and the woman from the second
  image; nobody else is present." With `others_present` it adds "plus a few distant, indistinct
  guests in shadow"; a 4th+ character is "one more distant, indistinct figure". Over-the-shoulder
  shots read "seen over the shoulder of the man from the first image; the woman from the second
  image faces the viewer" — never a third body.
- **Shot list** (`shot_list.json`, after narration, one Gemini call): cuts on sentence boundaries,
  6–9 s per shot (≈ 70 shots for 10 min), at most `IMAGEGEN_MAX_UNIQUE_SHOTS` (75) unique images, a mix of framings with
  40–60% of shots without a full face. Prompts and reference slots are composed in code: approved
  character portraits first ("the man from the first image"), then the location reference.
- **Identity.** Approve one of four generated portraits per character (and optionally one of two
  establishing images per recurring location) before any shot is generated. If identity
  retention is weak, set `IMAGEGEN_IDENTITY_POLICY=limited_faces` (≤ 6 full-face shots).
- **Generation.** Native 1280×720 (`IMAGEGEN_SIZE`; 1360×768 gives a little more detail), Lanczos-upscaled to 1920×1080; cached by prompt, seed,
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
`youtube.upload`; captions, language backfill and analytics add `youtube.force-ssl` and
`yt-analytics.readonly` (Phase 3.6). A stored token without a scope triggers a one-time
re-consent in the browser.
Banned-topic lines can be plain premises or `theme: … | angle: … | premise: … | title: … |
video: … | published: …`; the same theme may return from a clearly different angle.

---

## Authenticity hardening (Phase 3.9)

Every feature below can be switched off; the previous behaviour stays available.

- **Pluggable voices** (`audio/tts/`):
  - `TTS_BACKEND` is `edge` (default), `local` (Kokoro, Apache-2.0, or Chatterbox, MIT, set by
    `TTS_LOCAL_ENGINE`) or `elevenlabs` (`ELEVENLABS_API_KEY` from the environment).
  - Kokoro needs numpy 2, but the project pins numpy < 2, so Kokoro runs in its own venv as a
    worker process. Set it up once with `python -m venv .venv-tts`, then
    `.venv-tts/Scripts/pip install kokoro-onnx`. Put `kokoro-v1.0.onnx` and `voices-v1.0.bin` (from
    the kokoro-onnx releases) in `models/tts/`. To use another interpreter, set `TTS_LOCAL_PYTHON`.
  - Word timings come from the backend, or from forced alignment with faster-whisper
    (`ALIGNER_MODEL`). An alignment is used only when it covers at least 98 % of the script with a
    drift under 80 ms.
  - Pronunciation fixes live in `data/pronunciations.json`. Voice calibration is kept per backend
    and voice.
  - A voice clone needs a reference clip in `assets/voice/` with a provenance note: your own voice
    or one licensed for this use, never a real public person.
  - Compare voices with `python -m scripts.voice_audition --text-file briefs/audition.txt`. The
    results go to `debug/voice_audition/`. In the GUI, the **Vozes** panel does the same.
- **Motion** (`video/motion.py`, `video/ambient.py`):
  - `MOTION_STYLE=parallax` gives a 2.5D move from a depth map (Depth Anything V2 Small, cached
    next to each shot). It falls back to Ken Burns when there is no depth model, the depth is noisy,
    or a face would be split. `ken_burns` is the old look.
  - `AMBIENT_ENABLED` adds candle, dust, rain, fog or steam loops, only where the scene text
    supports them.
  - `PACING_VARIETY` varies shot lengths (CV ≥ 0.35, from 3.5 to 16 s).
  - `SHOT_TRANSITIONS=smart` uses hard cuts on dialogue and crossfades on time jumps.
  - `KEY_MOMENT_CLIPS` (0 by default) adds image-to-video clips at key moments. Run
    `python -m scripts.probe_key_clips` first.
  - Preview with `python -m scripts.preview_motion --project <slug> --seconds 30`.
- **Variation guard** (`studio/variation.py`):
  - Each episode gets a profile: theme tags, opening device and thumbnail composition. Profiles are
    cached in `data/episode_profiles.json`, and you can edit them.
  - The researcher and the outline see the recent ones.
  - Titles similar to published ones are marked "parecido com" and lose points.
  - The upload confirmation shows the cadence advisor (episodes per week, Shorts per day).
- **Visible human authorship:**
  - The description has a credit line (`DESCRIPTION_CREDIT_LINE`), and the validator requires it.
  - The video ends with a credit card (`END_CREDIT_CARD`).
  - Settings show an "About" text.
  - The approval stores an editorial record: what the author wrote, edited, regenerated and chose.
- **Thumbnail–video coherence** (`video/coherence.py`) shows warnings in the approval gate when:
  - the thumbnail's focal element never appears in the video;
  - its text is not in the story (one cached, low-temperature Gemini check);
  - a Short's hook text is not from its own segment.

  Thumbnail prompts always keep the style suffix.
- **Synthetic-content disclosure:**
  - `uploader.approval.disclosure_decision(run_dir)` is the single rule. The flag is set when any
    shot, clip or thumbnail is realistic, or when the voice imitates a real person, and it comes
    with a reason.
  - The approval stores the decision once. The episode and all its Shorts upload with it, and the
    ledger records `contains_synthetic_media` and `synthetic_disclosure_reason`.
  - The approval card shows the decision. Choosing the other value needs a typed reason.
  - Older ledger records are filled in by `python -m scripts.disclosure_backfill [--dry-run]`, using
    the archived approvals.

## Parallel stages, film look and the thumbnail gallery (Phase 3.8)

- **Shots: Gemini plans while ComfyUI renders** (`video.shot_generation.plan_and_render`). A planner
  thread plans the shot list scene by scene, checkpointed. Each scene's shots go into the render
  queue as soon as the scene is ready.

  The calling thread is the only ComfyUI caller. It renders the lowest scene/shot first, with
  layer-1 QA inline, and keeps working while Gemini is in a backoff wait. The local VLM QA and its
  fixes then run once over the batch, as before.

  A scene the planner cannot plan is flagged and reported, and the others go on. Stop (or Ctrl+C)
  finishes the current image; checkpoints and the image cache let a rerun resume both lanes.
  `SHOT_PLANNER_CONCURRENCY` is 1 by default, 2 at most. With 2, a scene starts before the previous
  one ends and uses the latest finished scene as continuity.

  Progress shows "Planejamento: n/m cenas", "Imagens: n/m", an ETA and the Gemini wait, in the CLI
  and in the Projeto view.
- **One stage graph** (`studio/stages.py`: id, label, depends_on, lane `network|gpu|cpu`,
  optional, produces) drives the status, the GUI and the runner:
  - Narration (Edge TTS) and the visual bible (Gemini) run together;
  - reference candidates start as soon as the visual bible exists;
  - music mixing runs next to the shots;
  - at most one GPU job at a time.
- **GUI:** the stage list comes from `/api/stages`. In order:
  1. Premissa / Story Brief
  2. Roteiro
  3. Narração
  4. Música
  5. Visual bible
  6. Referências (aprovação)
  7. Planos (ComfyUI)
  8. Revisão dos planos
  9. Vídeo
  10. Shorts
  11. Publicação

  The legacy "images" step before narration is gone; an old `images` request runs `shots` with a
  warning. **Novo projeto** (the former Generate tab) takes the premise or brief, theme, type and
  duration, then opens the project. Every stage runs in **Projeto**: status, last run time and
  duration, Rodar / Refazer / Pular / Rodar a partir daqui, links to the approval panels, and live
  lane activity (Rede / GPU / CPU).
- **Film look** (`VIDEO_FILM_EFFECT = off | grain | super8 | super8_strong`, default `super8`) on every
  frame of the final encode; the cuts stay clean.
  - `super8`: coarse animated grain, a gentle irregular exposure flicker (±2.5 %), a slight gate
    weave, sparse dust and a rare thin scratch (a cached 10-s layer in `assets/film/`, looped from a
    per-video offset), lifted blacks, a slight warm tint and a vignette.
  - `grain`: grain and vignette only.

  The 3.6 tape glitch on cuts is gone; `VIDEO_TAPE_EFFECT` still works (`subtle` → `super8`,
  `medium` → `super8_strong`). Captions are burnt in after the effect. The Shorts get the same look
  at 9:16. A photosensitivity guard keeps the frame-to-frame luminance step ≤ 10 % (super8 2.3 %,
  super8_strong 2.8 %). The choice is in Settings → "Efeito de filme".

  Encode cost, measured on the episode "He Lied About His Dead Wife" (12 min, 1080p30):

  | Look | Render path (moviepy) | ffmpeg-only re-encode (64.6 s plain) |
  |---|---|---|
  | grain | +10 % | 110 s, +70 % |
  | super8 | +13 % | 132 s, +104 % |
  | super8_strong | +15 % | 147 s, +128 % |

  The render-path figures are the relevant ones; the ffmpeg-only column is the worst case.
- **Thumbnails:**
  - **Mais opções** makes 3 new variants with fresh seeds (a per-project round counter) and
    appends them; **Novos conceitos** asks Gemini for new concepts first; **Gerar de novo** on a
    card re-rolls that concept.
  - Nothing is overwritten: the gallery shows every variant newest first, with "Usar esta" and
    "Arquivar".
  - The panel polls while a job runs and shows its progress and any error (with a link to the
    references when an approval is missing).
  - Jobs act on the project open when the button was pressed.
  - The legacy stock/`image_map` thumbnail path (`video/thumbnail.py`, `_make_thumbnail`) is
    removed.

```powershell
python -m scripts.preview_tape_effect --seconds 20 --look super8      # also: grain, super8_strong
python -m scripts.generate_shots --project <slug>     # planner and renderer run in parallel
python gui.py                                         # Projeto view; thumbnails → "Mais opções" / "Novos conceitos"
```

---

## Premise briefs and Shorts cut from each episode (Phase 3.7)

- **Detailed premise briefs** (`agents/premise_brief.py`). Instead of a one-sentence premise you can
  give the story a brief, up to ~4 000 characters: free text, Markdown with optional headings, or
  JSON. Recognised fields:
  - working title, narrative type (A/B/C), logline;
  - protagonist, other characters (≤ 2 recommended), setting;
  - beats: hook, strategy works, hidden cost, reversal, philosophical layer, ending;
  - tone, must include, must avoid;
  - target minutes, title ideas, thumbnail idea.

  The brief is **authoritative**: the Story Development Agent keeps every provided decision and
  only fills the gaps. `story_brief.json` records `brief_coverage` and `requested_brief_hash`.
  The bible, outline and scene prompts receive only the relevant parts (`author_brief`).

  A faithfulness check reports `major` findings (`brief_faithfulness`), which the targeted
  revision may fix:
  - every must-include present and no must-avoid;
  - every beat mapped to an outline scene;
  - names and ages unchanged.

  A changed brief makes the Story Brief stale. A single sentence still works exactly as before.
  In the GUI, the premise field is a textarea with **Inserir modelo**, **Carregar arquivo** and a
  character counter. Examples: `briefs/ai-companion.md`, `briefs/people-pleaser.md`.
- **Shorts cut from the episode** (`video/shorts.py`, `video/shorts_render.py`,
  `uploader/shorts_upload.py`). After narration, Gemini proposes `SHORTS_PER_EPISODE` (3) cuts of
  45–75 s on sentence boundaries: a hook in the first 2 s, understandable alone, ending on tension.
  Code validates every rule; no cut may reach the episode's twist, and at most one may come close.

  In Review → "Shorts do episódio" you can:
  - see the cuts on the episode timeline;
  - play each one;
  - move its in/out points (snapped to sentences), drop or add a cut, edit its texts.

  Each Short is rendered at 1080×1920:
  - the exact slice of the final mix, normalised to −14 LUFS / −1.5 dBTP;
  - the shots of the cut reframed to 9:16 on the detected faces, or over a blurred copy of the
    image when the subjects don't fit;
  - large word-timed captions clear of the YouTube UI, after the episode's film look;
  - the hook text at the start and an end card.

  The approval gate covers the episode **and** its Shorts (their files and texts are hashed).
  Uploads go episode first, then each Short:
  - `#Shorts` in the title and the episode link in the description;
  - English language fields and their own captions;
  - `SHORTS_SCHEDULE` (default: the first with the episode, then one a day at 18:00, via
    `publishAt`).

  The confirmation shows the plan ("o episódio será PÚBLICO agora; Shorts: agora, amanhã 18:00,
  depois de amanhã 18:00"). Set each Short's "related video" by hand in YouTube Studio; the API
  can't. Shorts are recorded with `parent_video_id`, never ban a topic, appear under their episode
  in Desempenho and are archived in `shorts/`. The standalone mode is now called "Short original
  (história própria)".

```powershell
python -m scripts.project new --brief-file briefs/ai-companion.md      # or GUI → Novo projeto → Inserir modelo
python gui.py   # run stages; review episode + Shorts in the approval gate; upload all
```

---

## Packaging, language, analytics and the tape look (Phase 3.6)

Episode 1 got 220 impressions and 0 clicks in its first 12 hours: a grey thumbnail with the text
over the guests' faces, the protagonist's head cropped, and a title that restated the premise.

- **Thumbnails: 3 variants per episode** (`agents/packaging.py`, `video/thumbnails.py`). Gemini
  proposes 3 concepts, each from a different composition: big face with a readable emotion, small
  figure against an overwhelming presence, the telling object, or two-person tension. Each concept
  has ≤ 4 words of text (thumbnail-text policy; it must not repeat the title). Each concept is
  generated with the character references, then:
  - thumbnail-only grade: lifted midtones, local contrast, a darker clean background and one
    saturated accent (Antique Gold or Dusty Crimson) on the focal element;
  - very large text: cap height ≥ 18 % of the height, on a clean dark side, never over a face;
  - checks: WCAG contrast ≥ 4.5, head inside the frame (detector), text clear of faces, advisory
    QA hints.

  No "PRIVATE GRAVITY" wordmark (`THUMBNAIL_LOGO` adds a tiny corner mark). Review → Thumbnails
  shows the 3 variants side by side, each with its 168×94 phone-feed preview. "Usar esta" makes
  it the uploaded `thumbnail.jpg`. "Baixar" saves the others for YouTube Studio's **Test &
  compare**, which is manual (not in the API).
- **Titles.** Story Development still writes 5 candidates, at least two built on the story's most
  shareable moment (the reversal), ≤ 60 characters with the hook in the first ~45. A scoring pass
  (Gemini, temperature 0.2) rates curiosity, concreteness, emotion and clarity; a length score is
  added and the total runs 0-100. The best title that passes the policy is chosen. Review lists
  the scored titles ("Usar" / edit); `titles.json` keeps every choice for later swaps. The approval
  refuses a thumbnail text that repeats the title.
- **Description** (`video/description.py`, template in `data/description_template.txt`, editable
  in Settings):
  - hook (no spoilers);
  - chapters from `scene_timeline.json`: first at 0:00, ≥ 3, each ≥ 10 s, spoiler-free titles;
  - a CTA block (subscribe, like, share with someone who'd recognise themselves, an
    episode-specific comment question);
  - channel line, fiction note, credits, 2–3 hashtags at the end.

  It is validated (≤ 5000 characters, CTA, chapters). The pinned-comment suggestion appears under
  the description to copy (the API cannot pin).
- **English signals and captions.** Uploads set `defaultLanguage`/`defaultAudioLanguage` = `en`
  and send `narration.srt` as the "English" caption track (`captions.insert`). This needs the
  `youtube.force-ssl` scope, and analytics needs `yt-analytics.readonly`: a stored token without
  them triggers one re-consent. The OAuth app is in *Testing*, so sign in with the channel's
  account (a listed test user).
- **Desempenho tab** (`uploader/analytics.py`):
  - from the YouTube Analytics API: views, average view duration and percentage, subscribers,
    top-10 countries with the US share, traffic sources, daily series;
  - impressions and impressions CTR come from the **Reporting API** daily report
    `channel_reach_basic_a1` (the Analytics API has no thumbnail impressions). The first report
    arrives 1–2 days after the job is created.

  Cached in `data/analytics/` and refreshed on demand or by the publication sync (every 6 h at
  most). Studio data lags 24–48 h. Flags: CTR < 2 % after ≥ 1 000 impressions, average viewed
  < 30 %.
- **Tape (VHS) look** (replaced in Phase 3.8 by the continuous film look, see above) (`VIDEO_TAPE_EFFECT = off | subtle | medium`), applied by
  ffmpeg in the final encode:
  - grain, 1–2 px chromatic aberration, faint scanlines, vignette, slight luminance wobble and
    gate weave;
  - a 0.3 s tracking glitch only on scene cuts.

  The captions are burnt in after it (ASS, same pill style), so they stay crisp. A
  photosensitivity guard keeps the frame-to-frame luminance change ≤ 10 % and glitches ≥ 1 s
  apart. It costs about +13 s of encode per minute of video. Thumbnails never get it.

```powershell
python -m scripts.youtube_backfill --video-id w-XazyuEUv8 --captions --language --description   # re-consent first time; --dry-run to preview
python -m scripts.thumbnails --project 2026-10-21-the-man-who-needed-everyone-to-envy-him --restore   # 3 variants (archived: restores only what it needs)
python -m scripts.preview_tape_effect --seconds 20 --look super8   # before | after, and the encode overhead (3.8)
python gui.py   # Review: thumbnails, scored titles, description; 📈 Desempenho
```

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
