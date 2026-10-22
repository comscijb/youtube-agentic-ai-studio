# Private Gravity — Phase 3.2.1

**Style calibration fixes before generating style anchors**

> Small implementation prompt for Claude Code. Continue on `feat/phase-3.2`. Report in
> **Portuguese**. No live calls in tests. Never push. One commit.

## Evidence: `output/style_calibration/contact_sheet.jpg`

The user ran `calibrate_style`. Review findings:

1. The three variants are nearly identical (same seed and composition; only texture changes).
   **Chosen variant: `engraving`** — set `VISUAL_STYLE_VARIANT = "engraving"` as the default.
2. **Torn-paper edges / light paper margins** around establishing shots (all variants) and
   around most `hybrid` images. In a 16:9 video this becomes a flickering white ragged frame.
3. The **object insert rendered as a multi-panel comic page** (engraving and charcoal).
4. **Cloned figures**: "establishing 2" shows four near-identical men in suits.
5. The look reads as clean graphic-novel noir rather than cosmic dread: even lighting, figures
   filling the frame, little negative space.

FLUX runs with a zeroed negative (cfg 1), so every fix must be phrased **positively** in the
style suffix and in Gemini's prompt-writing rules, and checked in code where possible.

## Changes

1. **Full-bleed single image.** Style suffix (all variants): "single full-bleed illustration
   filling the entire frame edge to edge, ink and hatching reaching every border". Prompt-writing
   rules for Gemini: never request pages, panels, frames, borders, vignettes, paper edges, or
   multiple views. Code check: reject shot prompts containing `panel`, `panels`, `comic page`,
   `storyboard`, `border`, `torn paper`, `paper edge`, `frame around`.
2. **Automatic border detection** on every generated image (calibration, style refs, character
   refs, shots, thumbnail): measure mean luminance and variance in 3 % strips along each edge;
   if an edge strip is markedly lighter and flatter than the adjacent interior (paper margin),
   regenerate with a new seed (max 2), then crop inward to the largest border-free 16:9 area and
   upscale; record the action in the manifest. Unit-test with synthetic images (bordered vs
   full-bleed vs legitimately bright windows near an edge — must not false-positive on the
   latter: require ≥ 3 edges or a uniform strip along a full edge).
3. **Single-panel check (heuristic):** detect long, straight, near-uniform light gutters crossing
   the image horizontally or vertically (typical comic gutters); regenerate if found. Keep it
   conservative and tested.
4. **No clones:** when a shot contains more than one person who is not an approved character,
   the prompt must give each a distinct age, build, posture and clothing; prefer "a few distant
   indistinct guests" in shadow over several detailed figures.
5. **More dread, less noir.** Strengthen the `engraving` variant suffix: "deep black negative space
   dominating most of the frame, a single small source of warm light, figures small within
   towering architecture unless the shot is a close-up, heavy shadows swallowing the edges of the
   room". Shot planner: at least 40 % of shots wide/establishing with small figures; close-ups
   keep a dark background with one light source.
6. **Variant-specific seeds:** `calibrate_style` uses a different seed per variant (recorded) so
   future comparisons show real differences, and gains `--variants` to rerun only some.
7. Re-run nothing yourself. Update README (style section) and tests.

## User commands afterwards

```powershell
python -m scripts.calibrate_style --variants engraving   # quick re-check of the fixes
python -m scripts.generate_style_refs                    # then approve 1–2 in the GUI (Review tab)
python -m scripts.generate_character_refs                # approve one portrait per character
python -m scripts.generate_shots --limit 5
```
