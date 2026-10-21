# Private Gravity — Phase 3

**3.0 Render-quality fixes · 3.1 Local image generation (ComfyUI + FLUX 3 Image Klein) with reference-based character consistency and a narration-aligned shot list**

> Implementation prompt for Claude Code. Read everything first. Create branch `feat/phase-3`
> from the current branch head (`feat/phase-2.2.1` + 2.2.2 work; merge state is the user's call).
> Report in **Portuguese**. No live API calls in tests (Gemini, Edge TTS, Pixabay, Openverse,
> YouTube, ComfyUI) — mock them. Never read `.env` or token files. Never push.
> **Checkpoint:** after 3.0 is committed and green, STOP and report; the user re-renders before 3.1.

---

## 0. Evidence: first full render (`output/final_video.mp4`, 10:16, 1920×1080, 24 fps)

Episode: *The Man Who Needed Everyone to Envy Him* (1 558 words, 9 scenes), Edge TTS
`en-US-AndrewNeural` rate −5 %, Pixabay stock. Findings from the user and from frame-by-frame
review (one frame every 8 s), the asset manifest, audio analysis and the thumbnail:

| # | Defect | Root cause (verified) |
|---|---|---|
| 1 | Mojibake in captions (`donâ€™t`, `fatherâ€™s`, `dinnerâ€”eight`) **and wrong pronunciation** of the same words | `gui.py` lines ~284 and ~315 read `research.json`/`script.json` with `open()` without `encoding` → cp1252 on Windows. `script.json` on disk is valid UTF-8 (`’` ×6); the corrupted text is what reached TTS and the SRT (11 occurrences). Same risk: `gui.py` ~966/~999 read/rewrite **`config.py`** without encoding (file contains `─` box characters) and `video/narrator.py` ~166. |
| 2 | Caption sync "borderline" | edge-tts ≥ 7 sends `SentenceBoundary` by default; `WordBoundary` only with `Communicate(..., boundary="WordBoundary")`. Verified: default → 1 SentenceBoundary event; with the flag → 1 event per word. The code falls back to proportional sentence timing and prints a misleading "Microsoft server change" warning recommending a voice switch. |
| 3 | Episode is 10:16, target 12:00 | Planning uses `NARRATION_WPM=125`; measured delivery for Andrew at −5 % is **≈ 152 wpm** (1 558 words / 616.5 s). Every episode will run ~17 % short. |
| 4 | Images often unrelated (girl in yellow hoodie for "woman with earthy hands looking sadly at husband"; ship at sea for "man offering linen cloth"; beach beer toast with groomsmen for "man making a toast at an elegant dinner"; train-track phone for "smartphone face down on marble") | Queries are 10–12-word narrative sentences; Pixabay returns popular loosely-matching results and the code never scores relevance (no tag overlap check). |
| 5 | Tonal clashes: smiling couples, bright beaches, cheerful restaurant scenes in a dark psychological drama | No tonal filtering (brightness/saturation/mood) of stock results. |
| 6 | Julian and Elena are played by ~6 different people of different ages/ethnicities across the episode; an old turbaned man stands in for a Cleveland hardware-store father | Stock cannot preserve character identity; the bible's visual descriptions are not used for asset selection. |
| 7 | Each scene loops 3 images A-B-C-A-B-C (≈ 8–10 s each), so every image appears ~3× per scene; cuts are on a fixed timer, unrelated to what is being said | `BROLL_INTERVAL=10` cycling over 3 assets per scene. |
| 8 | Stock image with embedded text ("NO ACTIVITY" sign) | No text/signage rejection. |
| 9 | **No music at all**: `narration_mixed.mp3` is byte-identical to `narration.mp3`; `music library/` is missing/empty | Mixer silently skips when the library is empty. |
| 10 | Narration loudness −20.6 LUFS integrated, true peak −5.7 dBTP | No loudness normalization (YouTube reference ≈ −14 LUFS). |
| 11 | Thumbnail uses the full title over an empty bedroom; the validated `thumbnail_text` ("Are they watching?") is ignored; arbitrary gold highlight on "THE"/"EVERYONE"; no human/emotional focal point | Thumbnail builder predates Phase 2.2. |
| 12 | A single transient Edge TTS failure aborts the whole narration; no reuse of finished scenes | No per-scene retry/cache. (Earlier 403 was an outdated `edge-tts`; fixed by upgrading to ≥ 7.2.7.) |

---

## 3.0 — Render-quality fixes

### A. Text encoding everywhere (defect 1)

- Every text read/write uses `encoding="utf-8"` (JSON, SRT, config, templates, banned topics,
  ledger). Fix the four known sites and grep the whole repo for others.
- Add a test that scans the source tree with `ast` for `open()` / `Path.read_text` /
  `write_text` calls on text modes without an explicit encoding and fails listing them.
- `gui.py` config patching: read/write `config.py` as UTF-8, preserve line endings, and add a
  regression test with the `─` characters.
- Mojibake guard before TTS and caption rendering: if narration contains typical mojibake
  sequences (`â€`, `Ã©`, `Ã`, `â€™`, `â€œ`, `â€”`), stop with a clear error naming the scene;
  offer `ftfy`-style repair only as an explicit opt-in (no new dependency unless justified).
- If the GUI ever saved a corrupted script, provide `python -m scripts.repair_text_encoding`
  that repairs cp1252-mis-decoded UTF-8 in `output/*.json` with a dry-run diff first.

### B. Narration robustness and timing (defects 2, 3, 12)

- `edge_tts.Communicate(..., boundary="WordBoundary")` when the installed version supports the
  parameter (check the signature); keep older-version compatibility.
- Replace the misleading warning: if no WordBoundary arrives, say so neutrally and mention the
  `boundary` requirement / package version, not a voice change.
- Per-scene retries (3 attempts, backoff 2/5/10 s) for `NoAudioReceived`, WebSocket and 403/5xx
  errors; on a 403, print the "upgrade edge-tts" hint once.
- Per-scene audio cache keyed by hash(text, voice, rate, pitch, edge-tts version): re-running
  narration reuses unchanged scenes; editing one scene re-synthesizes only that scene.
- `render_preflight --live`: synthesize one short sentence and confirm audio + WordBoundary
  events (opt-in; never in tests).
- **WPM calibration.** Persist measured delivery per voice+rate (`data/voice_calibration.json`:
  words, seconds, wpm, date) after every narration. Narrative planning (`NARRATION_WPM` use in
  outline/word targets/duration estimates) reads the calibrated value for the configured voice,
  falling back to config. Set the initial calibrated value for `en-US-AndrewNeural|-5%` from
  this run: 1 558 words / 616.52 s ≈ 151.6 wpm. Document that a 12-minute episode now targets
  ≈ 1 820 words. Show calibrated vs configured WPM in the GUI settings and in preflight.
- Optional ElevenLabs fallback provider is **out of scope** for this phase (note it in the
  report as a future item).

### C. Captions (defect 2)

- With word timings available, build cues of ≤ 7 words / ≤ 42 characters, never crossing a
  sentence boundary, min 0.8 s, max 4.5 s on screen, small lead-in (≈ 80 ms).
- Normalize typographic punctuation for display only (keep curly quotes/em dashes, they render
  fine once encoding is correct); never strip apostrophes.
- Keep the current visual style; expose font size and bottom margin in config.

### D. Music and loudness (defects 9, 10)

- The music library location is `music library/` (see `config.MUSIC_LIBRARY_DIR`). If music is
  enabled and the library is empty, preflight must **WARN** loudly and the render log must say
  "no music track found — rendering narration only" (currently silent).
- Support per-track metadata (`music library/tracks.json`: file, mood tags, license, source URL,
  attribution text); choose by mood tags derived from the episode (`psychological_theme`,
  `intended_emotional_experience`); record the chosen track + license in the asset manifest and
  append any required attribution to the YouTube description.
- Ducking under narration (sidechain-style envelope from narration RMS, configurable
  depth), fade-in/out, loop with crossfade if shorter than the episode.
- Final mix loudness normalization to −14 LUFS integrated, true peak ≤ −1.0 dBTP (ffmpeg
  `loudnorm` two-pass); log measured values; preflight reports them for existing renders.

### E. Stock relevance as a *fallback* provider (defects 4, 5, 8)

Stock remains the fallback for environment/object shots when generation is disabled or fails.

- Derive short search queries (3–5 concrete nouns/adjectives) from each long visual description;
  try 2–3 variants; never send 10-word sentences.
- Relevance score per candidate: overlap between query terms (lemmatized, simple stemming) and
  provider tags/title; reject below a threshold.
- Tonal score: mean luminance and saturation (PIL) against the brand's dark palette; reject
  bright/high-saturation images for this channel (configurable).
- Prefer results without prominent people when the shot needs a specific character (stock
  cannot represent identity): for character shots, stock may only return objects, hands,
  silhouettes or environments.
- Reject likely text/signage images with a cheap heuristic (edge-density of horizontal strokes
  or tags such as `sign`, `text`, `quote`, `typography`, `wallpaper`); keep it simple and tested.
- Record scores in the manifest so the reviewer can see why an image was chosen.

### F. Thumbnail (defect 11)

- Use the validated `thumbnail_text` (≤ 5 words) as the main text, not the title; brand
  typography and palette from config; remove the arbitrary word highlighting (or highlight the
  single emotionally key word chosen by the brief).
- Background selection prefers the strongest *human/emotional* shot of the episode (in 3.1:
  a dedicated generated thumbnail image).

### G. Tests and checkpoint

Unit tests for every item above (mock edge-tts, PIL images, ffmpeg calls where needed). Full
suite, `py_compile`, `git diff --check`. Commit per item. **STOP and report**, with the user's
commands:

```powershell
python -m scripts.render_preflight --live
python gui.py   # re-run narration → music → video for the current output/
```

---

## 3.1 — Local image generation with ComfyUI + FLUX 3 Image Klein

### Environment facts (from the user)

- Windows, RTX 3070 Ti (**8 GB VRAM**), **16 GB RAM**, ComfyUI Desktop on `http://127.0.0.1:8188`.
- **Generator: FLUX 3 Image Klein** (user's choice, replacing Z-Image Turbo). The user exported
  a working API workflow: `workflows/flux3_klein_edit_api.json` (reference only — see below).
  Models: UNET `flux3-image-klein-int8-convrot.safetensors`, text encoder
  `qwen3vl_4b_int8_convrot.safetensors` (CLIPLoader type `flux3`), VAE `flux2-vae.safetensors`;
  KSampler `euler`/`simple`, 16 steps, cfg 1, negative = `ConditioningZeroOut(positive)`.
- For comparison, Z-Image Turbo bf16 took ≈ 2 min per 1920×1080 image. Klein's per-image time
  is unknown: measure it (see C).
- A 12-minute episode with one new image every ~8–10 s needs ~70–90 images. The design must keep
  the GPU budget workable (fewer, better-placed unique shots; ~1 MP native generation + upscale;
  caching; resumable overnight batches).

### The supplied workflow — what it is and how to use it

`flux3_klein_edit_api.json` is the **region-edit template** (replace an earring), not a
text-to-image graph:

- `LoadImage` (11) → `ReferenceMultiLatent` (17, `images.image0`, resize to 1 MP) provides
  conditioning **and** the sampler latent (`KSampler.latent_image ← 17[2]`), so output size
  follows the reference image. `EmptyFlux2LatentImage` (44, 1920×1088) is **disconnected**.
- `CreateBoundingBoxes` (33) + `Flux3LayoutPrompt` (49) implement region editing with
  `<ref_image_0>`/`<obj_1>` tokens — not needed for shot generation.
- A prompt upsampler (`CLIPLoader` 46 = `qwen3.5_9b_flux3_prompt_upsampler_w4a8`, `TextGenerate`
  47 with system prompt 48) rewrites the prompt; `ComfySwitchNode` 51 (`switch=false`) routes the
  upsampled text to the encoder. On 8 GB VRAM / 16 GB RAM this is costly, and Gemini already
  writes detailed prompts → **bypass by default** (`switch=true` routes the raw prompt), keep an
  opt-in flag `IMAGEGEN_PROMPT_UPSAMPLER`.
- `ImageCompare` (18) and `PreviewAny` nodes are UI-only.

Derive **two API templates** programmatically from this file (store them in `workflows/` and
validate every `class_type` and input name against the live `/object_info` before use):

1. `flux3_klein_t2i_api.json` — text-to-image: `UNETLoader`, `CLIPLoader`, `VAELoader`,
   `CLIPTextEncode` (raw prompt), `ConditioningZeroOut`, `EmptyFlux2LatentImage` at the native
   16:9 size (default 1360×752 ≈ 1 MP) → `KSampler` → `VAEDecode` → `SaveImage`. No reference,
   no layout, no upsampler.
2. `flux3_klein_ref_api.json` — reference-conditioned generation: like (1) but conditioning
   goes through `ReferenceMultiLatent` with `images.image0` (and `images.image1` for a second
   character, if `/object_info` shows the dynamic input supports it) fed by `LoadImage` nodes of
   approved character references uploaded via `/upload/image`. Make the **latent source
   configurable**: `reference` (as in the template) vs `empty_16x9` (EmptyFlux2LatentImage), and
   pick the default after the calibration below. Prompts use the generation-request voice:
   *"Create a photograph of the man from the first image, …"*.

If either derived graph fails validation, stop and ask the user to export that variant from
ComfyUI instead of guessing. Placeholders (prompt, seed, size, steps, reference filenames) are
mapped in `workflows/flux3_klein_map.json` by node ID + input name.

**Calibration script** (user runs it live): `python -m scripts.calibrate_imagegen` generates
the same 3 prompts with t2i, ref+`reference` latent and ref+`empty_16x9` latent, records seconds
per image and VRAM peak (`/system_stats`), and writes a side-by-side contact sheet for the user
to choose defaults.

### A. Visual bible (new artifact `visual_bible.json`)

One Gemini call after the Character Bible (contract-first prompt, JSON mode, validated, cached by
source hash) producing:

- **Global style**: a fixed style suffix for every prompt (cinematic, low-key lighting, muted
  palette matching the brand colors in config, 35 mm film grain, shallow depth of field,
  no text, no watermark), plus a negative prompt.
- **Per character**: an immutable visual descriptor (apparent age, ethnicity as stated or
  chosen once, build, face shape, hair, distinguishing features) and **per-scene wardrobe**
  consistent with the screenplay (e.g. Elena's ivory silk shirt in s03 and later; no cardigan).
- **Per location**: descriptor reused in every shot set there (Julian's loft: blackened oak
  table, vaulted concrete, smoked decanters…).
- A fixed base **seed per character** and per location, recorded for reproducibility.

### B. Shot list aligned to narration (new artifact `shot_list.json`)

- Built after narration (needs real timings). Cut points fall on **sentence boundaries** from
  the word-timed SRT/timeline; target shot length 6–12 s (configurable), so each image matches
  what is being said.
- Gemini proposes, per scene, the shots with: `start`, `end`, `narration_excerpt`, `shot_type`
  (wide/establishing, medium, close-up, insert/object, over-the-shoulder, silhouette, hands),
  `characters` (IDs), `location` (ID), `prompt` (built from visual bible descriptors + action +
  composition + global style), `face_visibility` (`none` | `partial` | `full`), and
  `reuse_of` (an earlier shot ID when a re-use is dramatically appropriate).
- **Identity policy — reference-based (default, configurable):**
  1. *Character references first.* For each main character, generate 4 candidate portraits with
     the t2i template (neutral-to-dark backdrop, chest-up, front and three-quarter, the immutable
     descriptor, episode wardrobe). The user approves one per character in the GUI
     (`character_refs/<character_id>.png`, recorded with seed and prompt in `visual_bible.json`).
     No shot generation before approval.
  2. *Character shots* use the reference template with the approved portrait(s); prompts refer to
     "the man from the first image" / "the woman from the second image" plus action, location
     descriptor, wardrobe and composition. Faces may be visible.
  3. *Environment/object/insert shots* use t2i with the location descriptor.
  4. Keep cinematic variety: the planner still mixes silhouettes, hands, over-the-shoulder and
     objects (≈ 40–60 % of shots without a full face), which also eases the GPU budget.
  5. If the calibration shows weak identity retention, fall back automatically to the earlier
     policy (mostly `none`/`partial` faces; ≤ 6 full-face hero shots).
- Budget: `IMAGEGEN_MAX_UNIQUE_SHOTS` (default 40 for 12 min); the planner merges or reuses shots
  to stay within it; remaining length is covered by Ken Burns motion on longer holds.

### C. ComfyUI provider (`video/imagegen_comfyui.py`)

- HTTP API: `POST /prompt` with an **API-format workflow** template, poll `/history/{id}` (or the
  websocket), fetch via `/view`. Inspect `/object_info` to validate node names before queuing.
- Templates: the two derived FLUX 3 Klein graphs and `flux3_klein_map.json` (see "The supplied
  workflow"). Reference images are uploaded with `POST /upload/image` (overwrite by content hash)
  before queuing.
- Resolution strategy: generate at ~1 MP 16:9 (default 1360×752; the model works around 1 MP)
  and upscale to 1920×1080 (Lanczos by default; optional ComfyUI upscale-model node if the user
  adds one). Measure and log seconds per image; preflight estimates total GPU time for the shot
  list from the calibration file.
- Cache by hash(prompt, seed, size, template hash, model names, reference image hashes);
  resumable batches;
  `python -m scripts.generate_shots [--limit N] [--only s04]` for overnight runs; a GPU-memory
  free call between batches if available; graceful handling when ComfyUI is not running.
- Failure policy per shot: retry once with a new seed; then fall back to stock (3.0-E rules,
  objects/environments only); record the fallback.
- Provenance in the asset manifest: provider `comfyui`, model, workflow hash, prompt, seed,
  size, timings, and `synthetic: true`.

### D. Renderer integration

- `video/creator.py` uses `shot_list.json` when present: one image per shot for its exact
  timing, Ken Burns motion chosen per shot type (slow push-in on close-ups, lateral drift on
  wides), short crossfades on cuts, no A-B-C cycling. Without a shot list, keep current behaviour.
- Overlay opacity adapts to each image's luminance (generated images are already low-key).

### E. Thumbnail

Generate a dedicated 1280×720 thumbnail image from a "thumbnail shot" in the visual bible
(strong emotional focal point, partial face allowed, negative space for text), composited with
the validated `thumbnail_text` and brand typography.

### F. Synthetic-content disclosure

Realistic AI-generated people may require YouTube's altered/synthetic content disclosure. Check
the installed YouTube Data API discovery document for the corresponding `status` field (e.g.
`containsSyntheticMedia`); if available, set it when the manifest contains synthetic assets and
add it to the 2.4 approval checklist. If not available in the installed client, add a checklist
item reminding the reviewer to set it in YouTube Studio.

### G. GUI

Add steps "Visual bible" and "Shot list / Generate images" between narration and video, with
progress (shots done / total, ETA), a per-shot preview grid, and "regenerate this shot (new
seed)" / "edit prompt" actions that invalidate only that shot.

### H. Tests and report

Mock ComfyUI (`/prompt`, `/history`, `/view`, `/object_info`, `/upload/image`, `/system_stats`), template derivation from `flux3_klein_edit_api.json` (disconnected node 44 reconnected in t2i, layout/upsampler nodes removed, switch bypass), reference upload by hash, character-ref approval gate, deterministic seeds, cache hits,
fallback to stock, shot timing on sentence boundaries, budget enforcement, renderer using shot
timings, disclosure flag. Full suite, `py_compile`, `git diff --check`; commit per section.
Report in Portuguese with measured/estimated GPU time and the user's commands:

```powershell
python -m scripts.render_preflight --live
python -m scripts.calibrate_imagegen            # timing + t2i vs reference comparison sheet
python -m scripts.generate_character_refs       # 4 portraits per character, approve one each in the GUI
python -m scripts.generate_shots --limit 5      # quick look at style/consistency first
python -m scripts.generate_shots                # full episode (long; resumable)
python gui.py                                   # video step, then review
```
