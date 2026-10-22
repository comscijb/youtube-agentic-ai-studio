# Private Gravity — Phase 3.2

**Pending 2.2.2 (Gemini resilience) · New visual identity: dark illustration**

> Implementation prompt for Claude Code. Read everything first. Branch `feat/phase-3.2` from the
> current head. Report in **Portuguese**. No live calls in tests (Gemini, ComfyUI, Edge
> TTS, stock, YouTube) — mock them. Never read `.env` or token files. Never push.
> Order: A → B, one commit per section. Do not regenerate any images yourself: the user runs
> the style calibration and approvals afterwards.

---

## A. Implement `docs/prompts/phase-2.2.2.md` (it was never applied)

Evidence: `config.py` has no `GEMINI_OVERLOAD_*` settings and `scripts/rereview_output.py` does
not exist, so a `503 high demand` still aborts after ~5 s. The user reports frequent Gemini
failures. Implement that document in full (overload/rate-limit backoff on the same model before
fallback, wall-clock cap, proportionate over-budget severity, `scripts.rereview_output`,
housekeeping), adapting to code that changed since. Additionally:

- **Stage checkpoints everywhere**: every long-form stage already writes its artifact; make sure a
  failure mid-pipeline (GUI or `pipeline.py`) can resume from the last valid artifact without
  regenerating earlier stages (same `source_hash` caching used by the benchmark `--resume`).
- Print a one-line end-of-run summary of Gemini usage: calls, retries, waits, fallbacks, failures.

---

## B. New visual identity — dark illustration ("cosmic dread without creatures")

### B.1 Direction (from the user)

Replace extreme photorealism with a **dark hand-drawn illustration style**, with a Lovecraftian
*atmosphere* but **no creatures**, rendered in the **brand palette**. Goal: originality and
impact; it also makes the channel instantly recognisable.

Do **not** name living artists or specific artworks in prompts, and do not feed third-party
artworks into the pipeline as references. The style is defined by the description below and by
**our own approved style anchors** (B.4).

### B.2 Style specification (single source of truth in `agents/identity.py`, versioned)

- **Medium:** ink drawing with scratchboard/engraving-like hatching and cross-hatching, charcoal
  and dry-brush gouache washes, visible paper grain, hand-drawn imperfection; painterly smoke and
  fog. Illustrated, not photographic.
- **Atmosphere — dread without monsters:** oppressive scale (small figures against vast rooms,
  towering doorways, endless staircases), deep pooled shadows, a single dominant light source,
  smoke/fog tendrils and shadows that *suggest* a presence but never form a creature, subtly wrong
  geometry (corridors too long, ceilings lost in darkness), stillness, isolation.
- **Palette (from `config.COLORS`):** near-monochrome duotone of **Midnight Ink `#0C1218`**
  (dominant) and **Fog Blue `#91A8B8`** (midtones), **Warm Ivory `#F4F0E8`** for highlights; a single
  warm accent per image — **Antique Gold `#E0BA79`** or **Muted Copper `#C18A70`** — on the light
  source or the story's focal object (candle, decanter, phone glow, key); **Dusty Crimson
  `#C56670`** only for rare emotional punctuation (wine, a wound, a ring). Read the hex values from
  config, never hard-code them twice.
- **People:** stylised but consistent faces (gaunt planes, long shadows, expressive hands);
  identity comes from the approved character references, re-made in this style.
- **Hard exclusions (negative guidance / prompt rules):** tentacles, monsters, creatures, eldritch
  beings, giant eyes, gore, occult symbols, text, letters, watermark, signature, photorealism,
  3D render, glossy CGI, anime, cartoon, bright saturated colour.
- Remove the previous photographic suffix (35 mm film grain, shallow depth of field, etc.)
  wherever it lives (visual bible prompt, defaults, thumbnail prompt). Search the codebase.
- Expose `VISUAL_STYLE` in config (`dark_illustration` default; keep `cinematic_photo` as a legacy
  option) and include a **style hash** in every image cache key, so changing style never reuses
  old images.

### B.3 Style calibration (user runs it live)

`python -m scripts.calibrate_style` generates the same 6 prompts (2 characters-free establishing
shots, 2 character shots, 1 object insert, 1 thumbnail composition) in **three variants** of the
spec, and writes a labelled contact sheet plus timings:

1. *Engraving* — scratchboard/engraved line work dominant, minimal wash.
2. *Charcoal & gouache* — painterly tonal masses, softer edges, dry-brush texture.
3. *Hybrid* — engraved line work over painterly washes.

The user picks one (config `VISUAL_STYLE_VARIANT`).

### B.4 Style anchors as references

After the variant is chosen, `python -m scripts.generate_style_refs` generates 6 character-free "style board" images; the user approves 1–2
in the GUI (`style_refs/`). Reference slot allocation in `ReferenceMultiLatent` (max 4):
characters present (≤ 2) → location reference (≤ 1) → style anchor (≤ 1, only when a slot is
free). Prompts name the style anchor by ordinal ("rendered in the drawing style of the fourth
image"). Record the slot plan per shot in `shot_list.json`.

### B.5 Regenerate references and dependent assets

- Character references and location references are **style-specific**: existing photographic
  refs become invalid under the new style hash; `generate_character_refs` regenerates them in the
  chosen style for re-approval (keep the old ones archived).
- Thumbnail generation uses the same style and the protagonist's approved reference; brand
  typography unchanged.
- **Stock fallback is incompatible with illustration:** in `dark_illustration` mode, disable stock
  for shots; on generation failure retry with two new seeds, then hold the previous shot longer
  (record the fallback). Stock remains available only in `cinematic_photo` mode.
- Overlay: illustrated images are already low-key — default overlay much lower in this mode and
  keep the luminance-adaptive rule from 3.0.1.
- Synthetic-content checklist item: clearly non-photorealistic illustration generally doesn't need
  the realistic altered/synthetic disclosure; keep the checklist item but word it as a reviewer
  decision, defaulting to "not realistic" in this mode.

---

## C. Out of scope

No local LLM / Ollama integration in this phase (the user decided to fix Gemini resilience first;
a fallback provider may come later). Keep the Gemini client behind its current interface so a
second provider can be added without touching the stages.

---

## D. Report

Per section: changes, deviations, tests before/after. User commands, in order:

```powershell
python -m scripts.calibrate_style                 # choose the variant (send the sheet for review)
python -m scripts.generate_style_refs             # approve 1–2 style anchors in the GUI
python -m scripts.generate_character_refs         # re-approve characters in the new style
python -m scripts.generate_shots --limit 5
```
