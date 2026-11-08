# Private Gravity — Phase 3.9.6

**Figure removal without ghosts: real repainting inside the mask, a ghost detector, a fill prompt, and regeneration for large figures**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests (mock ComfyUI). Never read `.env`/tokens. Never push. One commit per section.

## 0. Evidence: project `2026-11-06-the-ferryman-who-took-memories-as-fare`

The user reports that the new removal leaves the **silhouette of the removed figure** in the image. The
intermediate files in `images/shots/removal/` show it clearly (engine `lama_comfy`, profile `base`,
denoise 0.45):

- **`s01_04_1794064434_1_*`** (three background figures on the jetty, brush mask):
  - `erased.png`: LaMa replaced the figures with a **blurred, mottled band** across the river;
  - `restored.jpg`: Klein at 0.45 only re-textured the band slightly. The **outline of each removed
    figure is still readable**, as a blur patch with their shape in the middle of sharp hatching.
- **`s07_07_1794065593_1_*`** (the Ferryman standing on the deck behind the Mayor, a large mask, about
  25 % of the crop):
  - `erased.png`: a dark, blocky column where he stood;
  - `restored.jpg`: the column is still there, with **block artefacts** (LaMa works at low resolution
    and is upscaled). Anyone would see "something was here".

**Why:**
1. LaMa is good at continuing simple surfaces. On this channel's dense engraving hatching and large
   masks, it produces smooth or mottled fills whose shape is exactly the mask, which is the figure's
   silhouette.
2. The restore pass runs at **denoise 0.45–0.55**, which by design keeps the layout of its input. It
   preserves the ghost. The code comment in `video/qa/removal.py` already says so for the OpenCV fill:
   "Klein only blurs it at 0.45–0.6 and redraws it from 0.75". The same holds for LaMa on this style.
3. The verification only checks the **person count** (layer-1 detector). A blurred silhouette has no
   detectable person, so it passes and is offered as a good result.
4. The mask follows the figure's outline closely (grow 10 %), so even a good fill keeps the contour.

## A. Repaint the hole for real

- **Probe first:** check `/object_info` for `DifferentialDiffusion`, `InpaintModelConditioning` and
  `SetLatentNoiseMask` working with the Klein model.
  - On the two fixtures above, run a denoise ladder of **0.75 / 0.85 / 0.95** with differential
    diffusion (a soft mask: full strength inside, falling off over a ring).
  - Measure the ghost score (B), the person count and the time. Report the table and choose the
    defaults from it.
- **New pipeline:**
  1. **Mask:** grow **20–25 %** beyond the figure (and its shadow), so the contour falls *inside* the
     repainted area. Use a soft edge: a gradient ring of ~4 % of the crop side, which differential
     diffusion uses so the borders blend.
  2. **Init:**
     - LaMa fill as before, used only as a low-frequency hint, or **plain blurred surroundings** when
       the mask is large: over ~12 % of the crop, or when the LaMa output has block artefacts (B);
     - always run LaMa at the crop's resolution (tile, or upscale its input) to avoid 512-px blocks, or
       skip it.
  3. **Repaint:** masked Klein at **denoise ≥ 0.8** inside the mask, with differential diffusion, the
     location reference and the style references. The prompt is the fill prompt from C.
  4. **Composite and tone match** as now (protected pixels outside).
- **New escalation ladder** (replaces `PROFILES`):
  1. base: grow 0.22, denoise 0.82;
  2. denoise 0.92 with a new seed;
  3. bigger mask (grow 0.35) at 0.95;
  4. the last step is **"Gerar o plano de novo sem a figura"** (D), not "só apagar".
  
  "Só apagar" (LaMa only) stays as a manual option, never automatic.

## B. Ghost detector (results must fail when a silhouette remains)

`video/qa/ghosts.py` compares the repainted region with its surroundings and with the removed figure. It
needs four signals:

| Signal | What it measures |
|---|---|
| **Texture deficit** | High-frequency energy (Laplacian / hatching density) inside the mask vs a ring around it. A smear has far less. Flag when inside/ring < ~0.6, calibrated. |
| **Contour persistence** | Gradient magnitude along the *original figure's outline* (the hard mask edge) in the result vs the same band a few pixels outside. A remaining silhouette keeps a strong edge exactly on the old contour. |
| **Block artefacts** | Energy at 8/16/32 px grid frequencies inside the mask. |
| **Tone step** | Mean/std difference between the mask and the ring after the tone match. |

Rules:
- A candidate fails if any signal is beyond its threshold. Calibrate on the six files above (all must
  fail) and on clean patches of the same shots (must pass).
- Store a `ghost` score per candidate. The viewer shows "silhueta visível" on failed candidates, and
  they are never ranked first.
- The person count check stays. A candidate is "good" only when the person is gone **and** there is no
  ghost.

## C. Fill prompt: say what should be there

- The repaint prompt describes **concretely what belongs behind the figure**:
  - from the shot's location descriptor;
  - from what surrounds the mask (sampled from the shot list and visual bible: "weathered wooden deck
    planks, dark river water, low mist");
  - in positive wording, with the style suffix;
  - still never person words (keep `PERSON_WORDS`).
- Add an optional field in the viewer, **"Substituir por"** (e.g. "névoa", "cais vazio", "pedras no
  casco", "água escura"). The user's text is put at the start of the fill prompt. Giving the model
  something to draw is what stops it from tracing the old shape.

## D. Large or foreground figures: regenerate instead

- When the mask covers more than ~15 % of the frame, or overlaps a listed character, the viewer suggests
  **"Gerar o plano de novo sem a figura"** as the first option:
  - regenerate the whole shot with the 3.9.4-A wording rules;
  - add an explicit headcount that excludes the unwanted figure;
  - keep `others_present: false`;
  - use a new seed.
- The result goes through the normal QA. The removal candidates remain available for comparison.
- Batch: "Remover figura" queued on several shots splits automatically into repaint jobs and regeneration
  jobs.

## E. Tests

- Mask grow and soft ring.
- The ladder order, with regeneration as the last automatic step.
- The fill prompt never contains person words and includes the "Substituir por" text first.
- Ghost detector:
  - flags synthetic cases: a smooth blob on hatching, a blocky patch, a retained contour;
  - passes a clean hatched fill;
  - flags the six fixture files, committed downscaled under `tests/fixtures/removal_ghosts/`.
- A large mask suggests regeneration.
- The workflow JSON includes `DifferentialDiffusion` when available (fake `object_info`), and falls back
  without it.

## F. Report and commands

Report:
- the denoise ladder table;
- the detector calibration;
- before/after sheets for `s01_04` and `s07_07` in `docs/reports/3.9.6/`;
- the time per removal.

```powershell
python gui.py   # Planos → visualizador → F (máscara) → "Substituir por: névoa" → Enter → escolher; figura grande → "Gerar o plano de novo sem a figura"
```
