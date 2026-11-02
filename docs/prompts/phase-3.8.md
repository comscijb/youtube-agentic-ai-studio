# Private Gravity — Phase 3.8

**Pipelined shot generation (Gemini and ComfyUI in parallel) · Stage overlap · GUI pipeline cleanup · Continuous Super 8 film look · Thumbnail regeneration fix**

> Implementation prompt for Claude Code. Branch `feat/phase-3.8` from the current head. Report in
> **Portuguese**. No live calls in tests (mock Gemini, ComfyUI, Edge TTS). Never read `.env`/tokens.
> Never push. One commit per section. No change to story, prompt or image-content rules.

## 0. Context

- Today `generate_shots` first requests the whole shot list from Gemini (one call per scene, ~9 calls,
  each 20–90 s, longer with overload backoff) and only then starts ComfyUI. The GPU sits idle for minutes
  while Gemini works, and Gemini sits idle while the GPU renders.
- The GUI pipeline is out of date: both the **Projeto** and **Generate** tabs show a legacy
  "Generate images (shots)" step *before* narration (the old stock/"images" alias) and the real
  "Shots (ComfyUI)" step after the visual bible. Users can't tell which one to run.

## A. Producer–consumer shot generation

- Split shot generation into two concurrent lanes inside one run:
  - **Planner lane (network):** per scene, build the skeleton (code-owned IDs/timings), call Gemini for
    that scene's shot contents, validate/repair, checkpoint `sNN.json`, compose the final prompts and
    reference slots, then **enqueue each shot** as soon as its scene is ready.
  - **Render lane (GPU):** a single ComfyUI worker consuming the queue in scene order (priority: lowest
    scene/shot index first), generating, running layer-1 QA inline (CPU), saving, updating the manifest.
- Continuity dependency stays sequential only in the planner (scene N receives scene N−1's last shot);
  rendering never waits for later scenes.
- Planner concurrency: configurable `SHOT_PLANNER_CONCURRENCY` (default 1; allow 2 with the existing
  overload backoff — never more). The render lane must keep working while the planner is in a backoff wait.
- Layer-2 local VLM QA still runs **after** all renders in one batch (VRAM swap), as in 3.5; edits and
  regenerations follow. The thumbnail renders when the render queue is empty.
- Robustness: if the planner fails on a scene after its repairs, flag that scene, keep rendering the others,
  and report it at the end; Stop/Ctrl+C drains cleanly (finish the current image, keep checkpoints); rerun
  resumes both lanes from checkpoints and the image cache.
- Use `threading` + `queue.Queue` (the codebase is synchronous); keep ComfyUI calls on one thread; make
  progress reporting thread-safe.
- Progress (CLI + GUI): two bars — "Planejamento: 6/9 cenas" and "Imagens: 23/66" — plus overall ETA from the
  calibration file; show when the planner is waiting on Gemini overload.
- Tests (mocked, deterministic with fake clocks): rendering starts before planning finishes; order is
  preserved; a planner failure on one scene doesn't stop others; stop/resume; concurrency limits; the GPU
  worker is the only ComfyUI caller.

## B. Overlap independent stages

- Run **Narration (Edge TTS)** and **Visual bible (Gemini)** concurrently — they don't depend on each other.
  Reference candidates (ComfyUI) can start as soon as the visual bible exists, while narration may still be
  running. Shots wait for both narration (timings) and approved references.
- Music mixing can run in parallel with shot generation (CPU/ffmpeg vs GPU).
- Make the dependency graph explicit in one place (`studio/stages.py`: id, label, depends_on, resource
  lane: `network|gpu|cpu`, optional, produces) and let the runner schedule ready stages by lane, at most one
  job per GPU lane. Same graph drives the status computation and the GUI.

## C. GUI pipeline cleanup

- Single source of truth: the GUI renders the stage list from `/api/stages` (built from `studio/stages.py`);
  no hard-coded step lists in `index.html`.
- Final stage order and names (labels in Portuguese, ids unchanged in code):
  1. Premissa / Story Brief
  2. Roteiro (outline + cenas + revisão)
  3. Narração
  4. Música
  5. Visual bible
  6. Referências (aprovação)
  7. Planos (ComfyUI) — includes shot list planning, rendering, QA
  8. Revisão dos planos (visualizador)
  9. Vídeo
  10. Shorts
  11. Publicação
- **Remove** the legacy "Generate images (shots)" step before narration from both tabs (and the old
  `images` alias in the run API — map any old request to `shots` with a warning in the log). Keep stock as a
  setting only (already off by default).
- Merge the duplicated controls: the **Generate** tab becomes the "Novo projeto" wizard (premise brief,
  type, duration) that ends by opening the project view; all stage running happens in the **Projeto** view.
- Project view: each stage card shows status (feito / desatualizado / parcial n/m / faltando / em execução /
  aguardando aprovação), last run time and duration, and buttons Rodar / Refazer / Pular / "Rodar a partir
  daqui"; a stage waiting on approval links straight to the approval panel.
- Running stages show live lane activity (network / GPU / CPU) so the user sees parallel work.
- Remove dead UI code and endpoints left by previous phases (search for unused routes and JS functions;
  list what was removed in the report).
- Tests: `/api/stages` order and dependencies; old `images` requests mapped; no legacy step in the rendered
  template (Flask test client + simple HTML assertions); JS syntax check.

## D. Continuous film look (replaces the tape glitch)

**Problem.** The 3.6-F effect (`video/tape.py`) puts its visible event, the **tracking glitch** (tear,
noise band, strong chroma shift, 0.3 s), on every scene cut. In the rendered episode this reads as a
*transition* effect. The constant layer underneath (grain 5, chroma 1, faint scanlines) is too weak to be
noticed. What the user wants is the opposite: **film noise across the whole video, in Super 8 style, with
clean cuts.**

### D.1 New looks

`VIDEO_TAPE_EFFECT = "off" | "grain" | "super8"`. The new default is `super8`. Map the old values with a
warning: `subtle` → `super8`, `medium` → `super8` at the stronger preset. Optionally rename the setting to
`VIDEO_FILM_EFFECT` and keep the old name as an alias.

**`super8`**: a continuous, uniform look on every frame. Nothing is tied to cuts.
- **Grain:** animated luma grain, clearly visible but not dirty. Make it slightly coarser than pixel noise,
  e.g. noise at half resolution then upscaled, or `noise` + a light `gblur` on the noise layer.
- **Exposure flicker:** gentle and irregular, ±2–3 % luminance at roughly 2–6 Hz with a smooth random
  curve (no square steps). It must always pass the photosensitivity guard (≤ 10 % between frames, no
  flashes).
- **Gate weave:** slight, slow, sub-pixel to 1–2 px, vertical and horizontal.
- **Dust and scratches, sparse:**
  - a few small specks (light and dark) lasting 1–2 frames each, average ≤ 1 visible per second;
  - an occasional thin vertical scratch lasting ≤ 0.5 s, every 8–20 s;
  - spread by a seeded random generator, **not** aligned to cuts.
- **Look:**
  - soft vignette;
  - slight halation/bloom on highlights (optional, if cheap);
  - gently lifted blacks;
  - warm tint that stays inside the brand palette (ivory/gold highlights, no orange cast);
  - no scanlines (that is video, not film);
  - chroma shift at most 1 px or none.
- **Two strengths** (`super8` and `super8_strong`), each with a 20 s preview.

**`grain`**: the fallback if `super8` costs too much render time. Cinema grain plus a soft vignette only:
no flicker, no dust, no weave.

**Remove the cut glitch entirely:**
- delete `glitch_windows`, the tear and noise band, and `VIDEO_TAPE_GLITCH_TITLE`;
- delete the glitch parts of `check_safety`;
- clean up the logs and report fields.

**Transitions stay clean:**
- the effect must not change at cuts;
- grain and dust keep running across the cut;
- the cut itself is the existing crossfade or hard cut, untouched.

### D.2 Implementation and cost

- Keep everything in the final ffmpeg encode pass (`filter_graph`). Captions are still burned **after** the
  effect, so they stay crisp. The same function serves the episode and the Shorts (9:16 sizes).
- **Dust/scratch overlay:** don't generate dust per frame in Python. Pre-render a short tileable overlay
  once and cache it in `assets/film/`:
  - e.g. a 10 s 1280×720 (or 1080×1920) sequence of dust and scratch frames with alpha, from a fixed seed;
  - loop it with `movie=...:loop=0` plus `overlay`, with a random start offset per video;
  - alternatively, a tiny alpha PNG atlas driven by `overlay` `enable` expressions.
  
  Choose whichever is faster. The point is that the cost stays roughly constant with video length.
- **Flicker:** use `eq=brightness=...` with an expression of `t` (sum of 2–3 sines at incommensurate
  frequencies, small amplitude). Weave uses `crop`/`pad` offsets from a similar expression (or `scroll`).
- **Measure** encode time for a real 12-min episode: `off` vs `grain` vs `super8`. Report the overhead in
  %.
  - If `super8` adds more than about +35 % encode time, make `grain` the default, and say so in the
    report.
  - Report the photosensitivity numbers (max luma step) for each look.
- **Thumbnails** never get the effect (unchanged).
- **Preview:** `python -m scripts.preview_tape_effect --seconds 20 --look grain|super8|super8_strong`
  renders a 20 s sample, before/after side by side, for the user to choose. Use a stretch that **contains
  a scene cut**, to show it stays clean.
- **GUI settings:** a selector "Efeito de filme: desligado / grão de cinema / Super 8 / Super 8 forte".
- **Tests:**
  - filter graph construction for each look;
  - no `enable=` windows tied to cut times;
  - flicker amplitude within the guard;
  - overlay is cached and reused;
  - old values are mapped;
  - captions come after the effect in the graph;
  - Shorts use the same look.

## E. Thumbnail regeneration in the GUI does nothing

**Symptoms.** The user tried "Gerar variantes", "Novos conceitos" and the older "regenerate thumbnail"
button in the upload panel. Nothing visibly happened.

**Findings from reading the code. Confirm each one with a reproduction before fixing.**
1. **"Gerar variantes"** (`/api/thumbnails/generate` with no `new_concepts`) reuses the **cached
   concepts** (same `key`). `generate_variants` uses the same seeds (`composed["seed"] + 7919 * number`),
   and `ImageGenerator.generate` returns the **cached image** for the same prompt+seed. The variants are
   rebuilt identical: work happens, but the result is the same.
2. **The UI never refreshes the variants panel.** `igPost` calls `loadImagegen()` on success, not
   `loadThumbVariants()`. There is no polling of `busy`, no progress line in the thumbnail panel, and
   errors from the background thread (e.g. `ApprovalRequired`, a Gemini error) go only to the log. Even
   "Novos conceitos", which does make new images, looks like nothing happened.
3. Variants are **overwritten in place** (`variant_1..3.jpg`). There is no way to get *more* options while
   keeping the current ones.
4. **The legacy `/api/regenerate-thumbnail`** (`regenThumbnail()` in the upload panel) still calls
   `_make_thumbnail(script, image_map)`, the old stock/`image_map` path from before Phase 3.6. It needs
   `_state["script"]` and fails or produces the old style for projects opened from the Projeto view.

**Fixes:**
- **"Mais opções"** (replaces "Gerar variantes"): generate N new variants (default 3) with **fresh seeds**
  (store a per-project `thumbnail_round` counter mixed into the seed) and keep the concepts. **Append**
  them to the gallery as `variant_<n>.jpg` (n continues: 4, 5, 6…). Never overwrite.
- **"Novos conceitos"**: force new Gemini concepts (as now), then render them with fresh seeds, also
  appended.
- **"Gerar de novo"** on a single variant card: new seed, same concept. Append it as a new variant linked
  to its parent.
- **Gallery** shows every variant, newest first, each with:
  - its 168×94 mobile preview, checks and QA hints;
  - "Usar esta" (select);
  - "Arquivar" (hide; files kept).
  
  The selected one is clearly marked. `thumbnails.json` keeps the full list with `round`, `seed`,
  `concept_key` and `parent`.
- **Progress and errors:**
  - while running, the thumbnail panel shows "Gerando miniatura 2/3…" (poll `/api/thumbnails` every
    3 s while `busy`, and reuse `_imagegen_progress`);
  - it refreshes automatically when done;
  - any exception from the background thread is stored (`last_error`) and shown in the panel. For
    `ApprovalRequired`, show the approval message with a link to the references panel. Never fail
    silently.
  - When another generation is running (409), show "Outra geração em andamento".
- **Remove the legacy path:**
  - `regenThumbnail()` and `/api/regenerate-thumbnail` now delegate to the 3.6 variant system;
  - a title change only re-letters the selected variant's text if the text is derived from the title.
    Normally the thumbnail text is independent and must not repeat the title.
  - Delete `_make_thumbnail` and its stock/`image_map` code if nothing else uses it, and list it in the
    report.
  - The upload panel's thumbnail box shows the **selected variant** (with cache-busting) and a link
    "Escolher outra miniatura" to the gallery.
- **Project scoping:** verify that `/api/thumbnails*` act on the **currently open project** (not a stale
  `config.OUTPUT_DIR` from a previously opened project). Test this with two projects.
- **Tests (Flask test client, mocked ComfyUI/Gemini):**
  - "Mais opções" appends 3 variants with new seeds and does not overwrite;
  - "Novos conceitos" calls the concept generator with `force=True`;
  - a single-variant regenerate links `parent`;
  - selection survives;
  - errors from the worker reach `/api/thumbnails` as `last_error`;
  - the 409 busy response;
  - the legacy endpoint delegates;
  - the right project directory is used;
  - JS check that the buttons call `loadThumbVariants` and the polling.

## F. Report and commands

Report:
- the measured speed-up on a mocked timeline and, if possible, a real run's per-stage timings from the
  logs;
- the film-look encode overhead for each option, with the recommended default;
- the root cause of the thumbnail bug that was confirmed.

Commands:

```powershell
python -m scripts.preview_tape_effect --seconds 20 --look super8      # also: grain, super8_strong
python -m scripts.generate_shots --project <slug>     # planner and renderer run in parallel
python gui.py                                         # Projeto view; thumbnails → "Mais opções" / "Novos conceitos"
```
