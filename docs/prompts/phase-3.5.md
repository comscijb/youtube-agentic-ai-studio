# Private Gravity — Phase 3.5

**Local-first, edit-first image QA: layered checks, Klein region edits, batched VRAM use, pluggable providers**

> Implementation prompt for Claude Code. Branch `feat/phase-3.5` from `feat/phase-3.4` (head
> `fadc98c`). Report in **Portuguese**. No live calls in tests (mock ComfyUI, Gemini, OCR/face
> detectors). Never read `.env`/tokens. Never push. One commit per section.
> **Status:** section A is done (probe run by the user, results in A.1). Continue from **A.1**: implement
> decisions 2–5, then re-run the probe in focused mode (decision 6) as a test the user runs, then B–G.

## 0. Why

- Phase 3.4-D added Gemini-vision QA per shot (`video/visual_qa.py`): ~70–100 multimodal calls per
  episode on the API that is most often overloaded. Image generation is local and fast (Klein ≈ 17–21 s);
  it should not wait on Gemini.
- On a QA failure 3.4 **regenerates** with a new seed (`IMAGEGEN_QA_RETRIES`), which can trade one
  defect for another and loses good composition. FLUX 3 Image Klein can **edit** (region boxes + layout
  prompt + up to 4 reference images — the user's original template `workflows/flux3_klein_edit_api.json`
  is exactly this), so localized defects should be edited, not regenerated.
- VRAM: Klein peaks at 7.6 GB of 8 GB — a QA model cannot be resident at the same time.

## A. Feasibility probe: QA with the models already installed (user runs it)

Goal: find out whether ComfyUI's `TextGenerate` node (present in the edit template, node 47, which accepts
an `image` input) can do visual QA with models already on disk, so no new install is needed.

- `python -m scripts.probe_local_vlm` (live, opt-in, ComfyUI running):
  - Inspect `/object_info` for `TextGenerate`, `CLIPLoader` types and installed text encoders/LLMs; list
    which loaded model can feed `TextGenerate` (candidates on disk: `qwen3.5_9b_flux3_prompt_upsampler_w4a8`,
    `qwen3vl_4b_int8_convrot`). Report what is and isn't possible; don't guess node inputs.
  - For each usable model, run the QA prompt (section B schema, JSON only, temperature low, thinking off) on
    a fixed test set of 8 existing project shots chosen to cover known cases: two duplicated-Julian frames,
    a Julian-without-glasses frame, the Elena red-mark frame, a frame with background guests in a no-guest
    scene, and 3 clean frames. Store the expected answers in `tests/fixtures/qa_probe_expected.json`
    (fill them from the 3.4 Gemini QA results in the manifest where available; otherwise ask the user to
    confirm them).
  - Measure: valid-JSON rate, accuracy per check (people count, duplicate, glasses, mark, extra people, text),
    bounding-box usefulness (IoU against Gemini boxes when present), seconds per image, VRAM peak.
  - Write `data/qa_probe_report.json` and a short contact sheet with each model's answers overlaid.
- Also measure the cost of swapping models: unload Klein (`/free`), load the QA model, run 8 images, unload,
  reload Klein — total seconds.
- **STOP after A** and report the numbers.

## A.1 Probe results (`data/qa_probe_report.json`, 8 shots) and decisions

| Model | valid JSON | behaviour | s/image |
|---|---|---|---|
| `qwen3.5_9b_flux3_prompt_upsampler_w4a8` | 0 % | answers with edit instructions (it is a prompt upsampler) | 44 |
| `qwen_3_4b` | 100 % | **text-only — never sees the image**, says "pass" to everything | 6 |
| `qwen3vl_4b_int8_convrot` | 88 % | **does see the image**: caught the duplicated Julian (s09_04) and 3 people/missing glasses (s03_06); missed glasses on the s01_05 close-up, the 4 background figures in s02_04, and returned no JSON for s03_05 | 1–4 |

Boxes from every model are unusable (mean IoU 0.02, 0 hits). Model swap Klein↔VLM ≈ 37 s per round.

Decisions for the rest of this phase:

1. **Layer-2 backend = `comfy_textgenerate` with `qwen3vl_4b_int8_convrot.safetensors`** (CLIPLoader type
   `flux3`). Drop the other two from candidates. Ollama stays an optional backend, not implemented unless
   cheap.
2. **The VLM is a classifier, never a locator.** All boxes come from layer 1 (person/face detector). Ignore
   any box the VLM returns.
3. **Focused questions instead of one big JSON**: layer 1 detects people/faces; layer 2 receives each
   relevant **crop** with one short question and a one-word answer (`yes|no|unclear`), e.g. "Is this person
   wearing glasses?", "Is there blood, a wound or red marks on the skin?", "Is there any visible writing?".
   One whole-image question only for things that need context ("Do two of these people look like the same
   man?" with the two crops side by side). Parse one token; retry once on unparseable output; `unclear`
   becomes advisory. Keep `max_length` small and thinking off.
4. **Headcount and extra/background people come from the detector** (use a person/body detector, not only
   faces, so backs and small background figures count); calibrate thresholds on the probe set.
5. **Instruction-only edits** when no reliable box exists (e.g. red marks on a hand): the region-edit
   template must also work without bounding boxes (shot as `<ref_image_0>` + instruction); use boxes only
   when they come from the detector.
6. Re-run the probe after implementing 2–4 (`probe_local_vlm --mode focused`) and report the same metrics;
   target ≥ 85 % accuracy on glasses/duplicate/extra-person with the fixture. The user **confirmed** all fixture
   truths on 2026-10-27: set `fixture_confirmed_by_user=true` in `tests/fixtures/qa_probe_expected.json`.

## B. QA result schema with locations and an action

Every QA backend returns, per image:

```json
{"people_count": 2,
 "issues": [{"type": "missing_marker|duplicate_person|extra_person|unstated_injury|text|border|style_break|wrong_subject|other",
             "severity": "blocking|advisory",
             "box": [x0, y0, x1, y1],          // normalized 0–1, required for localized issues
             "target": "julian_vance",         // character concerned, if any
             "note": "short description"}],
 "verdict": "pass|edit|regenerate"}
```

Decision rules in code (not left to the model):

- **edit** when every blocking issue is localized (has a box), covers < 35 % of the frame, and total
  blocking issues ≤ 2 — e.g. missing glasses, a mark on a hand, an extra background figure, text, a
  duplicated person who is not the main subject.
- **regenerate** when: style break, border/panel, wrong subject or missing main character, a blocking issue
  covering the main subject (> 35 %), ≥ 3 blocking issues, or 2 edits already failed.
- Advisory issues never trigger work; they show in the GUI.

## C. Layered QA (local first)

- **Layer 1 — deterministic, CPU, every shot:**
  - face/person count with a lightweight detector (OpenCV DNN or MediaPipe; pick one, pin it in
    requirements, CPU only); compare with the expected headcount from 3.4-C; near-identical face crops
    (embedding or perceptual-hash similarity) flag `duplicate_person` with boxes;
  - OCR (Tesseract via `pytesseract` if installed, else EasyOCR CPU — check availability, don't force a
    heavy install) for `text` with boxes;
  - existing border/panel/aspect checks.
  - Illustration style may reduce detector recall: calibrate thresholds on the probe set and report recall.
- **Layer 2 — local VLM, batched, only for checks layer 1 can't do** (signature markers such as glasses,
  unstated injuries, identity plausibility, extra people when layer 1 is unsure): backend chosen from the A
  results — `comfy_textgenerate` (preferred if A shows it works) or `ollama` (fallback; model configurable,
  not pulled automatically). Run it only on shots that passed layer 1 or need confirmation.
- **Layer 3 — Gemini vision, optional:** `IMAGEGEN_QA_GEMINI=false` by default; usable as fallback when the
  local VLM is unavailable or for a manual `qa_shots --gemini` run.
- Provider interface `video/qa/` (`base.py`, `deterministic.py`, `comfy_vlm.py`, `ollama_vlm.py`,
  `gemini_vlm.py`); config `IMAGEGEN_QA_LAYERS="deterministic,local_vlm"`.

## D. Klein region-edit workflow

- Derive `workflows/flux3_klein_regionedit_api.json` from the user's `flux3_klein_edit_api.json`
  (LoadImage of the shot as `<ref_image_0>`, `CreateBoundingBoxes` from QA boxes as `<obj_N>`,
  `Flux3LayoutPrompt`, `ReferenceMultiLatent` with the shot plus up to 3 extra references — the relevant
  approved character portrait(s) for identity fixes — latent from the reference image so size stays 16:9,
  upsampler switch bypassed). Validate against `/object_info`; if it fails, stop and ask the user to
  export it.
- Edit instructions are composed by code from the issue type (templates), e.g.
  - missing_marker (glasses): "On the man in <obj_1>, add the horn-rimmed glasses of the man in the second
    image; keep his face, pose, lighting and everything else unchanged."
  - unstated_injury: "Remove the red marks on the hand in <obj_1>, restoring clean skin consistent with the
    surrounding drawing; keep everything else unchanged."
  - extra_person / duplicate_person: "Remove the figure in <obj_1> and continue the background behind it in
    the same drawing style; keep everything else unchanged."
  - text: "Remove the lettering in <obj_1>, continuing the surface behind it."
  - identity: "Replace the face of the man in <obj_1> with the face of the man in the second image, keeping
    pose, lighting and drawing style."
- After each edit re-run QA on that image (layer 1 + the layer that flagged it); max 2 edits, then
  regenerate (max `IMAGEGEN_QA_RETRIES` seeds), then keep the best and flag. Keep every version
  (`images/shots/history/<shot_id>_vN.jpg`) and record actions in the manifest.

## E. Batched pipeline to share 8 GB of VRAM

`generate_shots` becomes phase-batched:

1. generate all pending shots (Klein loaded) → layer-1 QA inline (CPU);
2. `/free` Klein → load local VLM → layer-2 QA for the batch → unload;
3. reload Klein → apply edits/regenerations for the failed shots;
4. repeat 2–3 for the edited shots only (max 2 rounds).

Progress and ETA per phase in CLI and GUI; resumable (QA results and edit history are checkpointed).
Thumbnail follows the same path.

## F. GUI

Shot grid shows QA verdict, issues with their boxes drawn on hover, edit history (before/after), and
buttons: "Editar (QA)", "Editar com instrução própria" (user draws/accepts a box and types the instruction —
reuses the region-edit template), "Gerar de novo".

## G. Tests and report

Mocks for every backend and ComfyUI; decision-rule tests (edit vs regenerate thresholds); region-edit
template derivation; batching order (Klein never loaded together with the VLM); resume; GUI endpoints.
Report test counts, the probe numbers (after the user runs A), and the commands:

```powershell
python -m scripts.probe_local_vlm                 # A — send me data/qa_probe_report.json + contact sheet
python -m scripts.qa_shots --layers deterministic,local_vlm      # QA the current project's shots
python -m scripts.generate_shots --fix-only       # edit/regenerate only what failed QA
```
