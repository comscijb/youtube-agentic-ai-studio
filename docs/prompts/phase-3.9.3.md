# Private Gravity — Phase 3.9.3

**Style anchors are never copied: hotfix verification, copy detector, style swatches · No paper margins · Exterior locations without interior architecture**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests. Never read `.env`/tokens. Never push. One commit per section.

## 0. Evidence

Project `2026-11-03-everyone-heard-what-he-thought`. The character sheets came out right. Every
**location** candidate was a near-copy of the approved style board `board_6.png`: the empty room with a
gold-seated chair, the same doorway and the same stairs on the right.
- `village_bakery_1..4` are the room, barely edited.
- `the_moor_1..2` are the moor with the chair and the doorway pasted in.

**Cause.** Since `917e8be` (fix(references): location references with nobody in them):
- `_location_job` sends `kind="ref"` with the **empty style board as the only reference**, and the prompt
  says "Rendered in the drawing style of the first image". With a single reference, FLUX Klein treats
  the image as the thing to edit, not as a style hint.
- `compose_shot` does the same for **figure-free shots without a location reference** (inserts, objects,
  empty rooms): their only slot is the style board.
- `tests/test_visual_style.py` even asserted that an insert is `kind="ref"` with a single `style` slot.

Character sheets are t2i, and their style was right from the prompt suffix alone.

**Hotfix already applied by hand (uncommitted). Review, keep and commit it:**
- `video/shot_generation.py::_location_job` no longer passes anchors, so locations are t2i.
- `agents/shot_list.py::compose_shot` adds a style anchor **only when there is already a character or
  location reference**.
- `tests/test_location_people.py` and `tests/test_visual_style.py` were updated to the new behaviour.

## A. Verify the hotfix

- Run the full test suite and fix anything else that assumed the old behaviour.
- Regenerate this project's location candidates ("Gerar novos candidatos" on both locations) and
  confirm they no longer resemble `board_6` (use the detector from B on them).
- Check the style consistency of t2i locations against approved shots from episode 2: a contact sheet in
  `docs/reports/3.9.3/`. If the style drifts noticeably, use B.2 for locations instead of plain t2i.

## B. Make copying impossible to miss

### B.1 Copy detector

- `video/qa/copy_check.py`: compare a generated image with every reference it was given (and with every
  approved style board).
  - Use a structural similarity measure that ignores the shared drawing style: grayscale 256 px, edge map
    (Canny) or a blurred luminance layout, then SSIM or a perceptual hash, plus a layout-histogram
    distance.
  - Calibrate the threshold on real pairs:

    | Pair | Expected verdict |
    |---|---|
    | `board_6` ↔ `village_bakery_1..4` / `the_moor_1..2` | copies: must flag |
    | approved episode-2 shots ↔ their style board | must not flag |
    | episode-2 location refs ↔ boards | must not flag |
- Run it on location candidates, character candidates (against boards), every shot and thumbnail
  backgrounds.
- A flagged image is regenerated once with a new seed **and without the style anchor**. If it is still
  flagged, keep it, with the label "cópia da referência" in the GUI and the grid.
- Store the score in the manifest. The advisory QA panel shows it.

### B.2 Style swatches instead of whole boards (optional, only if A shows style drift)

- Build a **style swatch** per approved style: a 1024×1024 collage of 4–6 small crops of hatching and
  texture, taken from the approved boards, shuffled and rotated so no composition survives.
- Cache it in `assets/style_refs/approved/<hash>/swatch.png`.
- Use the swatch, never a whole board, wherever a style reference is still sent: shots with characters or
  locations, and locations if needed.
- Prompt wording: "use only the line and hatching texture of the last image; ignore its content".
- Check with B.1 that outputs don't reproduce swatch tiles.

## C. Tests

- The location job is t2i.
- The style slot only follows a content slot.
- Copy-detector thresholds on small committed fixtures: downscaled copies of `board_6` and two bakery
  candidates, plus an unrelated episode-2 shot.
- Regeneration without the anchor on a flag.
- Swatch construction is deterministic, and no crop is larger than 25 % of the swatch.

## E. White paper margins around images

**Evidence.** In the same project, 18 of the 62 shots have a light paper margin. The margin is ivory/white,
2–4 px at 480 px width, so about 8–16 px at 1920. It is uneven: thicker on some sides, sometimes with a
drawn rule line. Affected shots: `s06_01, s06_02, s06_04, s06_05, s06_07, s07_01, s07_02, s07_04,
s07_05, s07_06, s07_08, s07_09, s08_01, s08_02, s08_03, s08_06, s09_02, s09_03`.

Almost all of them are the night/moor scenes. The approved location reference `location_refs/the_moor.png`
**itself has the margin** (t2i, made after the hotfix), and every moor shot receives it as a reference,
so the margin propagates. The cause in the text-to-image output is that the style suffix asks for
"visible paper grain… hand-drawn" and the model sometimes draws *a print on paper*. `FULL_BLEED`
("…edge to edge") is not enough, and the negative is zeroed.

Fixes:
1. **Border detector** in `video/qa/borders.py` (deterministic, CPU):
   - measure, per side, the run of outer rows/columns that are much brighter than the interior and
     nearly uniform: mean > interior mean + 60 and a high share of pixels above 200; tolerate paper
     texture;
   - also detect a thin straight frame line parallel to the edge within the outer 4 %.
   
   Calibrate on the 18 affected shots (positive) and on the other shots of this project and episode 2
   (negative: bright windows, fog and light shafts touching an edge must not trigger).
2. **Automatic fix:**
   - when the margin is ≤ 5 % per side: crop to the inner content, then re-crop to 16:9 around the
     centre and resize to the target size. Record it in the manifest as `border_cropped` with the
     pixels removed;
   - when it is larger, or there is a margin on a reference: regenerate with a new seed (once). If it
     persists, flag it "moldura" in the grid.
   
   Run the detector on every generated image: shots, location and character candidates, thumbnail
   backgrounds and edits (crop→edit→composite must not reintroduce it).
3. **References are cleaned before use.** An approved reference with a margin is cropped (as above)
   before it is sent to ComfyUI, so a margin never propagates. Apply this to `the_moor.png` now.
4. **Prompt wording:**
   - replace "visible paper grain" with texture words that don't suggest a sheet of paper (e.g.
     "fine grain and ink texture across the whole picture");
   - put `FULL_BLEED` **first** in every prompt, not at the end;
   - run a small probe (3 prompts × 8 seeds, before vs after wording) and report the margin rate.
     Keep the wording only if it lowers the rate without changing the look (contact sheet in
     `docs/reports/3.9.3/`).
   - The style hash must not change just because of this wording fix: keep the approvals valid, as
     was done for `figures=False`.
5. **Repair command for existing projects:**
   `python -m scripts.fix_borders --project <slug> [--dry-run]`
   - detects and crops affected shots and references, keeping originals in `history/`;
   - lists what was fixed and what needs regeneration;
   - updates the manifest (and marks the video stale if it was already rendered).

## F. Exterior locations get interior architecture

The approved moor (`the_moor.png`) and its shots contain a monumental building with a doorway and a
staircase in the middle of the open moor. Nothing in the screenplay or the location descriptor asks for
it. The cause is the **engraving variant** of the style text (`agents/identity.py`), which always asks
for "vast rooms, towering doorways and endless staircases", and for "figures small within towering
architecture".

- Give each visual-bible location an `interior: true/false` flag (Gemini proposes it; validated by
  keywords like moor, field, lane, street, sea, forest, garden, cemetery). Backfill it for this project.
- Make the environment clause of the style conditional:
  - interiors keep the rooms/doorways/staircases clause;
  - exteriors get a landscape version (vast sky, scale of the land, distance, weather) with no
    architecture unless the descriptor names it (e.g. the stone cottages of the lane).
  - The style hash stays the same (wording variant, same visual identity).
- Regenerate the moor reference candidates, plus the moor shots after the user re-approves.
- Tests:
  - an exterior location prompt has no "doorways"/"staircases"/"rooms";
  - an interior one keeps them;
  - the flag is backfilled by keyword;
  - the style hash is unchanged.

## G. Small extra check: red accents on characters

In `s03_01` Thomas's apron and shoulder carry Dusty Crimson patches that read as blood (against the art
direction rule). Add an advisory check: saturated crimson regions inside a person's box larger than ~1 %
of the box get the hint "mancha vermelha na roupa (parece sangue?)". Never auto-fix.

## H. Report and commands

Report:
- the calibration numbers for the detector;
- whether t2i locations hold the style;
- which shots of the archived episodes would have been flagged;
- the border detector calibration and the probe results;
- the moor before and after.

```powershell
python -m pytest -q
python -m scripts.fix_borders --project 2026-11-03-everyone-heard-what-he-thought --dry-run
python gui.py   # Referências → locais → "Gerar novos candidatos" (bakery, moor); then Planos
```
