# Private Gravity — Phase 3.9.4

**Figures that don't appear in the first place · a "Remover figura" that really removes (mask inpainting) · importing externally edited images · honest video logs, voice pacing and length · clean narration joins, slower voice, re-timing without regenerating**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests (mock ComfyUI and Gemini). Never read `.env`/tokens. Never push. One commit per section.

## 0. Evidence: project `2026-11-03-everyone-heard-what-he-thought`

The user is fixing images in ChatGPT because "Remover figura" doesn't work.

**History:**
- `s02_01`: 5 versions, three of them "Remover figura" crop edits (`history/s02_01_v1/v3/v5_crop_before|after.jpg`);
- `s07_03`: 3 versions.

**What the crops show:** the box is right and the crop→edit→composite from 3.5.3 works (pixels outside are
untouched), but **the figure is redrawn, not removed**.
- In `s02_01` the second baker in the doorway comes back in every attempt, with a slightly different pose.
- In `s07_03` the silhouette on the horizon is redrawn sharper.

The reason is the model itself. Klein's reference edit preserves the content of the input image, which is
its job for edits, and "Remove the figure" is a weak instruction against a strong visual prior. Only
**mask-based inpainting**, where the masked pixels are unknown to the model, makes the figure disappear
reliably.

**Why the extra figures exist in the first place:**
- 7 shots were flagged `extra_person` at v1: `s02_01, s02_03, s04_03, s06_03, s06_05, s07_03, s09_03`.
- **4 of them** have a listed character *and* an action written with a generic person word.
- In total, 12 of the 60 shots combine a listed character with such wording:
  - `s02_01`: "**A lone figure** small inside the towering storehouse…" with `characters: ["thomas_wren"]`;
  - `s07_03`: "**A lone figure** stands in sharp silhouette… dark green wool coat…" with `characters: ["nell_wren"]`.
- `compose_shot` adds "the man from the first image" **and** keeps "a lone figure", so the model draws
  two people: the referenced character, usually large and facing the camera because the reference is a
  portrait, and the "figure".
- This happens most with `face_visibility: "none"` / `silhouette` / `wide` shots. In those, the portrait
  reference also pulls the character into a close-up that the shot didn't ask for.

## A. Prevention in shot planning and composition

1. **Wording rule + deterministic rewrite.**
   - When a shot lists characters and `others_present` is false, the action must refer to them by their
     short name or pronoun. Rewrite "a (lone|solitary|dark-coated)? figure", "a man", "a woman",
     "someone", "a person", "a silhouette" → the character's short reference ("the man", "Nell"), and keep
     the adjectives ("the man, small and alone, …").
   - Add the rule to the shot-planner prompt. The validator rewrites what slips through and logs it.
   - Generic person words remain allowed only when `others_present` is true.
2. **Faceless, distant and silhouette shots** (`face_visibility: none`, shot types `wide`, `silhouette`,
   `over_the_shoulder`):
   - the prompt states the headcount and the framing explicitly, e.g. "the only person in the image is the
     man from the first image, seen small and from behind, far from the viewer";
   - probe A vs B on the 7 flagged shots (3 seeds each): A = keep the portrait reference, B = no portrait;
     use text identity, wardrobe and the location reference. Choose per shot type based on the measured
     extra-person rate and identity score. Report the table.
3. **Re-plan command for existing shots:**
   `python -m scripts.replan_shots --project <slug> --only-wording`.
   - It rewrites the affected actions without new Gemini calls and lists which shots changed.
   - The user then uses "Gerar de novo (lote)" on them.

**Tests:**
- the rewrite on the 12 actions of this project (fixture);
- generic words kept when `others_present` is true;
- the headcount sentence is present for faceless shots;
- the reference choice per shot type follows the config.

## B. Real removal: mask inpainting

### B.1 Probe first

Query ComfyUI `/object_info` and report what is available:
- LaMa/MAT inpaint nodes (e.g. the `comfyui-inpaint-nodes` pack: "Load Inpaint Model", "Inpaint (using
  Model)");
- `InpaintModelConditioning` / `SetLatentNoiseMask` with the Klein model;
- any FLUX fill/inpaint model that fits 8 GB VRAM.

If LaMa isn't installed, document the exact install steps for the node pack and the `big-lama` weights
(check the licences) and stop for the user. Never install silently. If ComfyUI isn't usable, a local
ONNX LaMa on the CPU is an acceptable fallback (a few seconds per crop).

### B.2 Pipeline (replaces the current "Remover figura")

1. **Mask:**
   - start from the user's box (or the detector's box);
   - refine to the person: use the detector's person box/mask inside the user box if it overlaps, then
     dilate ~10 % and extend downward to include the cast shadow and the feet's contact area;
   - feather 6–12 px;
   - show the mask overlay in the viewer before running, editable with the brush (C).
2. **Erase:** LaMa (or MAT) inpaint on the crop (the existing ×3 context crop, ~1 MP). This removes the
   figure completely and continues floors, walls and sky.
3. **Restore the drawing texture:**
   - LaMa output is smooth, but the style is hatching. Run a masked Klein pass on the same crop, only
     inside the feathered mask: `SetLatentNoiseMask`, denoise **0.35–0.5**;
   - the prompt describes **only the surroundings**, from the location descriptor plus what the shot
     shows, in positive wording ("bare floorboards and the dark doorway, ink hatching"). It never says
     "person", "figure" or "remove", because naming people draws them when the negative is zeroed;
   - include the location reference and the style swatch, if 3.9.3-B.2 exists, as references for this
     pass.
4. **Composite:** use the existing protected-pixel composite (3.5.3) and the colour match.
5. **Verify:**
   - the layer-1 detector count inside the mask must drop, and the outside must stay pixel-identical;
   - if a person is still detected, retry with the next profile in a short escalation list: larger mask
     → denoise 0.55 → LaMa only;
   - at most 3 attempts.
6. **Choice:** keep up to **3 results** and show them side by side in the viewer with "Usar este". Never
   silently save a failed removal ("edição sem efeito" stays).

- Keep the old Klein region edit as "Editar região (instrução)" for non-removal edits.
- Batch support: "Remover figura" can be queued on several shots and runs in one batch, so the LaMa model
  loads once.
- Measure the time per removal, for both LaMa and the restore pass.

**Tests (mocked ComfyUI):**
- mask construction (dilation, shadow extension, feather) from a box and a detector mask;
- the restore prompt never contains person words;
- the escalation order;
- the outside is byte-identical;
- 3 candidates are stored;
- the workflow JSON is valid against fake `object_info`.

**Real check:** run it on `s02_01` (the baker in the doorway) and `s07_03` (the horizon silhouette).
Before/after sheets go in `docs/reports/3.9.4/`.

## C. Viewer tools

- **Brush mask:** paint or erase over the image at full size, with the brush size on the mouse wheel. The
  existing box remains as a quick option and seeds the mask.
- Shortcuts: `F` remove figure (mask mode), `[`/`]` brush size, `X` toggle erase, `Enter` run, `Esc`
  cancel.
- History shows each removal's mask and the chosen candidate.

## D. Import images edited outside (ChatGPT, Photoshop…)

The user already fixes images in ChatGPT. Make that a supported path instead of a manual file swap.
- **"Exportar para edição"** saves the current shot at full size (and optionally the crop around a box)
  into `exports/<shot>.png`, and opens the folder.
- **"Importar imagem editada"** (file picker or drag-and-drop onto the viewer):
  - accepts PNG, JPG or WEBP of any size or aspect;
  - fits to 1920×1080: if the aspect differs by ≤ 8 %, centre-crop to 16:9; otherwise ask whether to
    crop (with a draggable crop box) or extend with blur-fill;
  - upscales with Lanczos if smaller;
  - runs the margin detector (3.9.3), the copy check and layer-1 people counting;
  - saves the result as a **new version** in history, with `source: "external"` and the original file
    kept in `history/`;
  - marks the video as stale.
- Imported images are flagged in the manifest and in the editorial record (human edits, 3.9-D).
- Tests: aspect fitting (1536×1024, 1024×1024, 2048×1152), version recording, margin check on import,
  stale marking.

## F. Video stage: misleading warnings and episode length

**Log from the render of this project:**
```
Building 9 section clips from shot_list.json (60 shots):
[01] The Slip at the Counter 63.7s [opening]
⚠ no images for section 1 — gradient fallback
… (same for all 9 sections)
→ Rendering 7m 56s video…
```

1. **The warning is false.** `create_video` still loads the legacy stock `image_map` and prints "no
   images … gradient fallback" whenever it is empty, even though `load_shot_schedule` found all 60 shots
   and `_make_clip` renders from them.
   - Print the warning only when a section has **neither** scheduled shots nor `image_map` images.
   - When the shot schedule is used, print per section "N planos" instead.
   - When `load_shot_schedule` returns None (a missing image), name **every** missing shot rather than
     only the first, and fail the stage instead of silently falling back to stock/gradient.
     A normal episode must never render with gradients.
   - Add a preflight check in the Vídeo stage: all shot images exist, sizes are 1920×1080, there are no
     margins (3.9.3), and the shot timings cover the narration. Show the result before rendering starts.
2. **The episode is 7m53s instead of ~12 min.** The script was planned at 125 wpm (1500 words), but the
   new channel voice `local:bm_lewis|-5%` speaks at **178.9 wpm** (measured in
   `data/voice_calibration.json`), and the script ended at 1409 words.
   - **Planning:** the scriptwriter must use the calibrated wpm of the **current channel voice**
     (`planning_wpm()` keyed by backend+voice+rate). When there is no calibration yet for a new channel
     voice, measure it first from the audition sample (A.3 of 3.9) instead of falling back to 125.
   - **Pacing for Kokoro.** Kokoro reads with almost no pauses. Add pauses in the narration assembly:
     ~0.35 s after sentences, ~0.7 s at paragraph breaks, ~1.5 s at scene changes, and a longer, configurable
     hold before the final narrated line.
     - Insert silence between synthesized chunks and shift the word timings accordingly; captions and
       shots follow the timings.
     - Recalibrate the wpm with pauses included.
   - **Speed option** for the channel voice in Vozes (Kokoro `speed`, e.g. 0.85–1.0). Keep the voice id;
     the calibration key includes the speed.
   - Report the new estimated duration of this episode with the pauses, and with the pauses plus a speed
     of 0.9, without re-rendering.
3. **Stop during the video stage.** The log shows "⏹ Stop requested — will halt after current step
   completes…" while the sections were being built, and the render continued.
   - Make Stop interrupt the moviepy/ffmpeg render cleanly (terminate the encoder process, delete the
     partial `final_video.mp4`, mark the stage `interrompido`).
   - If the user didn't press Stop, find what sent it.

**Tests:**
- no gradient warning when shots cover a section;
- the stage fails on a missing shot image;
- preflight report;
- pause insertion shifts word timings exactly and keeps the total consistent;
- the planner uses the channel voice calibration;
- Stop terminates the encoder subprocess (mocked).

## G. Narration audio: scene joins, speed and re-timing (user review of the rendered episode)

The user's verdict: the Lewis voice is beautiful, expressive and natural, but **too fast**; slow it down
**10–15 %**. At scene changes, the incoming scene's narration runs over the outgoing one, with an
**audible artifact** and no breathing room. The new visual effects are good, so keep them.

**Measured on `narration.mp3` of this project** (16 kHz mono, 10 ms RMS windows):

| Join | Gap between last and first word |
|---|---|
| 1→2 | 0.38 s |
| 2→3 | 0.31 s |
| 3→4 | 0.38 s |
| 4→5 | 0.22 s |
| 5→6 | 0.24 s |
| 6→7 | 0.37 s |
| 7→8 | **0.07 s** |
| 8→9 | 0.36 s |

- **The artifact.** At every join inspected (63.7, 113.8, 278.0, 402.0 and 449.1 s), the outgoing scene
  decays to digital silence (−90 to −120 dB) and then plays an **isolated ~60–90 ms burst at a flat
  ~−30 dB**, followed by silence again, before the next scene's first word. This is the classic moviepy
  1.0.3 end-of-file stale-buffer glitch: `concatenate_audioclips` on MP3 `AudioFileClip`s (in
  `video/narrator.py`) repeats the last decoded buffer, together with MP3 encoder padding at each file
  boundary. The line-level `_concat` uses the same path, so joins inside scenes are at risk too.
- The video itself (476.6 s = 472.6 s narration + the 4 s end card) adds no overlap. The problem is
  entirely in the narration file.

**Fixes:**
1. **No more MP3/moviepy joins.**
   - Keep every synthesized line and scene as **PCM WAV**: the Kokoro output is already 24 kHz WAV.
   - Join with numpy or the ffmpeg concat demuxer on WAV, with 5–10 ms equal-power fades at every
     join.
   - Encode to MP3 (or AAC in the video) **once**, at the end.
   - Trim each piece's leading and trailing silence to a consistent threshold (−50 dB, keep 40 ms), so the
     pauses from section F are the **only** silence between pieces and they are exact.
2. **Join checker.** `audio/qa/joins.py` scans the finished narration for isolated bursts (a short
   non-silent island of 30–150 ms between two near-silent regions) and for gaps below the configured
   pause at scene joins.
   - Run it after the narration stage; any hit is a stage warning with timestamps.
   - Make this project's current `narration.mp3` a regression fixture: a short excerpt around 63.7 s
     and 278.0 s must be flagged, and the rebuilt narration must pass.
3. **Scene transitions breathe.**
   - Use the scene pause from section F (default **1.5 s**, configurable), plus a little more before the
     climax and coda scenes (outline `section_type` climax or aftermath: 2.0 s).
   - Centre the visual crossfade between scenes in that silence, not on the next word.
   - Let the music ducking release partially during pauses ≥ 1 s (about +4 dB, smooth), so the gap feels
     like a breath, not a dropout.
4. **Speed.**
   - Use Kokoro's **native `speed` parameter**, not a post-process `atempo`, which degrades the voice.
     Don't stack it with the current `-5%` rate.
   - Check how `-5%` is applied today for the local backend and replace it with an explicit speed.
   - Set the channel voice to `bm_lewis` with **speed 0.88**: about 12 % slower than now, from ~179 wpm
     to roughly 155–160 wpm. Make it editable in Vozes, with a 20 s preview at 0.85 / 0.88 / 0.92.
   - The calibration key includes the speed. Recalibrate it after the rebuild, with the pauses included.
5. **Re-timing without regenerating images.** A new narration changes every word time.
   - The shots must be **re-timed**, not regenerated: map each shot to its narration span (word indices
     from `narration_excerpt`, or the stored start/end word index) and recompute start/end from the new
     word timings. Recompute scene_timeline and captions.
   - Image files, history, edits and approvals stay untouched.
   - Shorts segments re-snap to sentence boundaries by their sentence indices.
   - Only the Vídeo and Shorts stages become stale; Planos must **not** be marked stale.
6. **Estimate before rendering.** Report the new total duration with speed 0.88 plus the pauses. The
   expected value is roughly 9.5–10.5 min from 1409 words. Show it in the Narração stage card.

**Tests:**
- WAV joins have no burst (synthetic clips that end in digital silence);
- fades at joins;
- the trimming threshold;
- exact pause lengths;
- the join checker flags the fixture and passes a clean file;
- speed is passed to Kokoro and not to atempo;
- re-timing keeps the image paths and approvals and only changes times;
- the Shorts re-snap;
- the stale flags (Planos not stale, Vídeo stale).

## H. Report and commands

Report:
- the A-vs-B probe table;
- the ComfyUI node availability and install notes;
- the removal success rate on this project's 7 flagged shots (person count in the mask before/after);
- the time per removal;
- the before/after sheets.

Also report the join-checker results before and after, and the new duration.

```powershell
python -m scripts.replan_shots --project 2026-11-03-everyone-heard-what-he-thought --only-wording
python gui.py   # Planos → visualizador → F (máscara) → Enter → escolher entre 3 resultados; ou Importar imagem editada
```
