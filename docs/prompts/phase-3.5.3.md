# Private Gravity — Phase 3.5.3

**Reliable region edits: crop → edit → composite, protected pixels outside the box, explicit keep clauses**

> Implementation prompt for Claude Code. Continue on the current branch. Report in **Portuguese**.
> No live calls in tests. Never push. One commit per section.

## 0. Evidence — shot `s01_06` (19 versions, `images/shots/history/s01_06_v*.jpg`)

Wide shot: Julian in the foreground (left), Elena standing mid-frame (x≈0.64–0.72), and two unwanted
background silhouettes — one on the staircase (x≈0.40, box ≈ 3 % wide) and one in the lit doorway on the
right (x≈0.76–0.80). The user drew tight, correct boxes around the doorway silhouette, yet:

- v3 and v14 (box on the doorway silhouette): **Elena was removed**, the silhouette stayed;
- v13 (same box): nothing changed; v2 (box on the staircase figure): nothing changed.

Causes:
1. **Boxes are too small for layout grounding.** ~3 % of the width is 2–3 latent cells at Klein's /16
   latent scale; `<obj_1>` cannot be localised, and "Remove the figure" falls back to the most salient
   figure — Elena.
2. **Nothing protects the rest of the frame**: the edit regenerates the whole image, and the instruction
   doesn't say what must be kept.

## A. Crop → edit → composite for every region edit

- Expand the box with context: crop = box enlarged ×3 (min 25 % of the frame's shorter side, clamped to the
  image, keeping 16:9 or the model's preferred ratio), upscale the crop to ~1 MP, and run the edit **on the
  crop only** with the box re-expressed in crop coordinates (it is now large enough to ground).
- Composite back: resize the edited crop to its original size and paste through a **feathered mask equal to
  the original box expanded by ~15 %** (feather ~2 % of the crop side). Every pixel outside the mask is
  restored from the source image — characters outside the box cannot change, by construction.
- Colour/tone match the pasted region to the surrounding source (mean/std per channel in a ring around the
  mask) to avoid visible seams.
- Instruction-only edits (no box) keep the current whole-image path, but still get keep clauses (B).
- Keep the full pre/post crops in history for debugging (`history/<shot>_vN_crop_before/after.jpg`).

## B. Descriptive targets and explicit keep clauses

- "Remover figura" prompts name the target concretely from its position and appearance when known
  ("the small dark silhouette standing in the lit doorway"), and add a keep clause listing every listed
  character visible in the crop with their outfit ("keep the woman in the cream knit top exactly as she is").
- When the crop contains a listed character and the box overlaps them by > 20 %, warn in the GUI before
  queuing ("a caixa cobre parte da Elena").

## C. Verification after the edit

- Compare inside vs outside the mask: outside must be pixel-identical (assert), inside must have changed
  meaningfully (mean abs diff above a small threshold); if inside didn't change, report "edição sem efeito"
  instead of silently saving a new version.
- Re-run layer-1 person detection on the result: for "Remover figura", the person count inside the
  mask should drop; for others, the count outside must be unchanged.

## D. Tests

Synthetic images with three figures: removing the small one changes only its masked area; another figure
outside the mask is byte-identical; tiny boxes get expanded crops; seam/tone matching keeps the ring
difference low; "no effect" detection; overlap warning.

## E. User check

```powershell
python gui.py   # s01_06 → restore v1 → Remover figura on the doorway silhouette, then on the staircase figure
```
