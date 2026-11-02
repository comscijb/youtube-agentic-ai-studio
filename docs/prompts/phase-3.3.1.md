# Private Gravity — Phase 3.3.1

**Robust shot-list generation: code-owned IDs, per-scene calls, advisory content checks**

> Implementation prompt for Claude Code. Continue on the current branch. Report in **Portuguese**.
> No live calls in tests. Never push. One commit per section.

## 0. Evidence — why patching validators is not converging

Three consecutive `generate_shots` runs failed after one repair each (debug files in
`projects/<slug>/debug/shot-list-*.txt`), each time on a different regex false positive:

- `"Two figures stand…"` (the two listed characters) flagged as unnamed people.
- `s05_02` "tear-stained face… sobbed breaths" rejected as *tears the narration never mentions* —
  but scene 5 says *"her face blotched and wet"* and *"she choked out"*: she **is** crying.
- `s08_04` "faint urban **silhouettes**" (a skyline) flagged as extra people.
- Gemini again returned the wrong `shot_id` sequence (it has to copy 50 IDs in order).
- The 40 % wide-shot quota could never be met by a single repair.

Root cause: one 50-shot call, one repair, and keyword checks that **block the whole episode**.

## A. Code owns the skeleton

- The shot skeleton (IDs, scene, start/end, narration excerpt, sentence boundaries) is computed
  deterministically from the narration timeline, as today. **Gemini never returns IDs or timings**:
  it returns an ordered array of shot contents for the requested scene; code zips by position
  (length must match; on mismatch, a targeted retry for that scene only).
- Remove `shot_id`/timing fields from the Gemini contract; keep them in the stored `shot_list.json`.

## B. One call per scene

- Generate shot contents scene by scene (≈ 9 calls of 4–7 shots), giving each call the visual bible,
  the scene's narration, its skeleton and the previous scene's last shot for continuity.
- Each scene is validated and repaired independently (max 2 repairs per scene, sending only the
  failing shots + neighbours, merged by position). A scene that still fails keeps its best attempt
  with the offending shots **flagged** — it never aborts the other scenes.
- Checkpoint each completed scene (`.checkpoints/shot_list/sNN.json`) so a rerun only redoes
  missing/failed scenes. Mix targets (wide share, face share) are passed as guidance per scene
  and reported as warnings for the whole list.

## C. Content checks: blocking vs advisory

- **Blocking** (repair, then flag): forbidden subjects (creatures, tentacles, gore…), page-layout
  phrases, empty/missing fields, length mismatch.
- **Advisory** (never block; shown as warnings on the shot card in the GUI and in a
  `shot_list_report.json`; the prompt composer adds the positive hints from 3.3-B):
  - injuries/decay/tears not stated — compare against the **whole scene narration**, with broader
    families (e.g. tears ≈ `tear*|cry*|weep*|wept|sob*|wet (face|cheeks|eyes)|choked|blotched`);
  - unnamed people — count only explicit people terms (`guests, crowd, strangers, waiter, staff,
    onlookers, passers-by, people in the background`) or a stated number of people/figures larger
    than the listed characters; ignore `silhouette(s)` when the noun phrase is about buildings,
    skyline, city, trees, furniture or architecture.
- Unit tests with the real phrases above (all must pass without blocking) and with genuine
  violations (blocking subjects still block; advisory issues produce warnings, not exceptions).

## D. GUI and CLI

- Shot grid shows each shot's warnings; "edit prompt" and "regenerate (new seed)" already exist —
  make sure editing a flagged shot clears its warning after re-validation.
- `generate_shots` prints a short warnings summary and continues to image generation for all
  unflagged shots; flagged-blocking shots are skipped until edited (listed at the end).
- Also include, if not already committed: the `_run_pipeline` early-exit fix for `["shots"]` /
  `["visual_bible"]` from the previous instruction.

## User commands afterwards

```powershell
python -m scripts.generate_shots --rebuild-shot-list --limit 5
```
