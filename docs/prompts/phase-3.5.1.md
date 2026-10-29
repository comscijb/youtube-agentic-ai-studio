# Private Gravity — Phase 3.5.1

**Flexible aspect handling for edits · QA as advice, not automatic action · one-click human edits**

> Implementation prompt for Claude Code. Continue on the current branch. Report in **Portuguese**.
> No live calls in tests. Never push. One commit per section.

## 0. Evidence

1. **Every region edit was discarded** (22 shots + thumbnail): Klein returns edits at **1360×768** while
   the pipeline expects **1360×752** (`IMAGEGEN_SIZE`). 1360×768 (1.771) is actually *closer* to 16:9
   (1.778) than 1360×752 (1.809); the model rounds latent sizes. The 2 % guard rejected it, and the edit
   path then "regenerated from an empty 16:9 latent" — which discards the source image, meaningless for
   an edit.
2. **Focused local QA is not reliable** (`probe_local_vlm --mode focused`, 8 confirmed fixtures):
   people_count 25 %, duplicate_person 62 %, missing_glasses 62 %, extra_person 75 %; detector person
   recall 67 %. It **passed** every real defect (duplicated Julian s09_04, 3-people s03_06, no-glasses
   s01_05, red mark s03_05) and **flagged** clean frames (extra_person on s01_03 and s08_02,
   duplicate_person on s04_03). In the real run, 22/66 shots were flagged, mostly duplicate/extra person —
   largely false positives; had the edits worked, they would have erased real characters. Likely cause:
   in this illustration style all faces share the same drawing, so "same man?" comparisons fail.

## A. Aspect ratio

- Accept any generated or edited image whose aspect is within **3 %** of 16:9 (configurable
  `IMAGEGEN_ASPECT_TOLERANCE=0.03`), then fit to 1920×1080 by scaling and centre-cropping the small
  excess (never more than 3 % per axis). Outside tolerance: for generation, retry once; for edits,
  **never** fall back to an empty latent — keep the original shot and record the failed edit.
- Set the default native size to a model-friendly true 16:9 multiple of 16: **1280×720** (or 1360×768 if
  calibration shows better detail); make the region-edit template output size follow the source image.
- Tests: 1360×768 and 1344×768 accepted and fitted; 1024×1024 rejected; edit never regenerates from an
  empty latent.

## B. QA modes: advice by default

- `IMAGEGEN_QA_MODE = "advisory"` (default) | `"auto"` | `"off"`.
  - **advisory**: QA runs and stores issues, but **no automatic edits or regenerations from VLM
    signals**. Automatic actions only from deterministic, reliable checks: aspect, borders/panels,
    OCR-detected text (if OCR is available), corrupt/blank images.
  - **auto**: current behaviour, allowed only for issue types whose measured fixture accuracy is
    ≥ 85 % (read from the latest `data/qa_probe_report.json`); others stay advisory.
- Remove `duplicate_person` from automatic decisions entirely for the illustration style (keep it as a
  low-confidence hint).
- `generate_shots` ends with a summary: shots with advisory hints, and the GUI command to review them.
  No `--fix-only` loop by default.

## C. One-click human edits in the GUI

- Shot grid: each card shows QA hints (with detector boxes if any) and buttons:
  - **Remover figura** — user clicks/drags a box on the image (or accepts a suggested detector box) →
    region edit "remove the figure, continue the background".
  - **Adicionar marcador** — picks a character and its signature marker (e.g. Julian's glasses) → region
    edit with the character's approved portrait as reference.
  - **Remover marcas** — box or instruction-only edit.
  - **Instrução livre** — box optional + free text.
  - **Gerar de novo** / **Ignorar aviso**.
- Edits are queued and run in a batch with Klein loaded; before/after toggle and version history per shot;
  undo restores any previous version.
- Keyboard-friendly review: next/previous card, "mark OK" — so reviewing ~70 shots takes a few minutes.

## D. Optional Gemini second opinion

- `qa_shots --backend gemini [--only ...]` and a GUI button per shot/batch "Segunda opinião (Gemini)";
  results stay advisory.
- `probe_local_vlm --backend gemini` measures Gemini on the same 8 fixtures with the same metrics, so the
  report compares local vs Gemini side by side (live, run by the user).

## E. Commands for the user

```powershell
python -m scripts.generate_shots --qa advisory      # regenerates nothing already OK; stores hints
python gui.py                                       # Continuar projeto → Shots: review hints, one-click edits
python -m scripts.probe_local_vlm --backend gemini  # optional comparison
```
