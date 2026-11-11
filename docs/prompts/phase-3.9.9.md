# Private Gravity — Phase 3.9.9

**Images that follow the narration: per-shot locations, a staging sheet per scene, a prompt-vs-narration check before rendering, an image check after, saner removals, an optional "hard shot" provider**

> Implementation prompt for Claude Code. Branch `feat/phase-3.9.9` from `main`. Report in **Portuguese**.
> No live calls in tests (mock Gemini, ComfyUI and any image API). Never read `.env`/tokens. Never push.
> One commit per section.

## 0. Evidence: project `2026-11-09-she-filmed-every-moment-of-her-son-s-childhood-2` (published after heavy manual fixing in GPT Image)

**User report:**
- generic stand-ins (addressed in 3.9.8);
- characters fused **into objects** (a person inside a table);
- removals that sometimes paint in **things that make no sense**;
- shots that **drift from the narration**, e.g. Leo **outside the car** while Hannah drives.

**A concrete cause found in `shot_list.json` (the car sequence of scene 4):**

| Shot | Narration | Action | `location` |
|---|---|---|---|
| `s04_06` | "In the car on the drive home… rearview mirror" | — | `supermarket_aisle` |
| `s04_10` | "turned his face toward the passenger window" | — | `supermarket_aisle` |
| `s04_11` | "her fingers tightening on the steering wheel" | — | `supermarket_aisle` |
| `s04_12` | "Hannah turns toward the passenger seat…" | — | `supermarket_aisle` |

- The scene starts in the supermarket, and the planner gave **every shot the scene's location**. The
  visual bible has **no car interior** at all.
- So the location reference sent to Klein is a **supermarket aisle** while the text says "in the car".
  The model compromises, and Leo ends up standing in a store or outside a car.
- Nothing checks that a shot's location, positions and props agree with its narration excerpt.

## A. Locations follow the narration, per shot

- **Visual bible.** Extract locations at the **beat level**, not the scene level. When the narration moves
  ("In the car on the drive home", "back in the kitchen"), it creates or reuses a location (e.g.
  `car_interior`), recurring or not.
  - Vehicles and other enclosed spaces get an `enclosed: true` flag and a short "who sits where" note.
- **Shot planner.** Each shot's `location` is chosen from its **own narration excerpt** (plus the previous
  shot for continuity), not from the scene header.
  - Deterministic guard: location keywords in the excerpt ("car", "driving", "steering wheel", "passenger
    seat", "kitchen", "hallway", "outside", "street"…) must match the chosen location's descriptor or
    flags; otherwise re-plan that shot.
- **Missing reference.** A location without an approved reference (e.g. a one-off car interior) goes
  **without** a location reference (text-only setting). That is far better than a wrong reference. Offer
  "Gerar referência" for it.
- **Backfill.** Run the guard over this project's and the Ferryman's shot lists and report every mismatch.
  The scene-4 car shots must be found.

## B. A staging sheet per scene (blocking and continuity)

- Before shot planning, make **one Gemini call per scene** (contract-first JSON) to produce a **staging
  sheet**. It is derived only from the narration, with an evidence quote for each entry:
  - for each moment (sentence range): where each character is (location + position: "driver's seat",
    "passenger seat", "behind the kitchen island", "standing in the doorway");
  - posture (seated or standing);
  - what they hold;
  - who is **not** present;
  - spatial relations to objects ("behind the table, the table between her and the camera").
- Shots reference the staging moment they cover. The prompt composer turns the staging into explicit,
  physically clear phrases:
  - "Hannah in the driver's seat, hands on the wheel; Leo in the front passenger seat beside her, both
    inside the car";
  - "standing **behind** the desk, the desk edge in front of her waist", never just "at the desk".
- **Contact and occlusion wording** for furniture (the "person inside a table" failure). Whenever a
  character touches or is near a large object, state which side they are on and what overlaps what.
  - Prefer framings where the object is clearly in front of or behind the body.
  - Avoid "leaning into" or "sitting at" without a seat.
- **Continuity:** consecutive shots of the same moment keep the same staging (seats, props, wardrobe).
  Deterministic check across adjacent shots.

## C. Prompt-vs-narration check (before rendering, cheap)

- After planning a scene, one low-temperature Gemini call compares **each composed shot prompt** with its
  narration excerpt and the staging sheet. It checks:
  - who is present / absent;
  - the location;
  - positions and relations ("inside the car", "behind the table");
  - key props and actions;
  - nothing contradicts the narration (e.g. "outside", "standing" when the text says seated in a car).
- The output per shot is `{ok, problems: [{kind, evidence_quote, fix}], suggested_prompt_patch}`.
  - Problems are fixed **automatically** in the shot spec (location, staging phrase, headcount), with a
    log.
  - Anything uncertain is shown in the GUI before rendering: "Aderência do prompt: 3 planos ajustados,
    1 para revisar".
- Deterministic pre-checks too: location keyword guard (A), headcount vs the staging's present list,
  forbidden contradictions (inside/outside, seated/standing).
- Cost: one call per scene, batched with the shot planning where possible. Report the cost.

## D. Image-vs-narration check (after rendering)

- For every rendered shot, ask **focused yes/no questions** generated from the shot spec and staging:
  - "Is the woman inside a car, in the driver's seat?"
  - "Is the boy inside the car, beside her?"
  - "Is any person merged with or passing through furniture?"
  - "Is anyone present who is not listed?"
  - "Does the setting look like a car interior?"
- Engine: Gemini vision, as the optional second opinion that already exists; the local VLM is unreliable
  per 3.5.1. Off by default, with a per-project toggle ("Checar aderência das imagens").
  - Image size: 768 px.
  - Budget display in the GUI before running (calls × estimated cost; read prices from config, don't
    hard-code them).
- **"Person fused with an object" detector** (deterministic, advisory): a person box whose lower part is
  cut by a horizontal furniture edge without legs or chair visible, plus a depth discontinuity check
  using the depth maps the parallax already makes (`images/shots/depth/`). Combine it with the vision
  question.
- Failures get the hints "fora da narração: …" / "personagem fundido com objeto". There is a one-click
  "Gerar de novo com o prompt corrigido" (applying C's patch) and a batch action.
- Report the adherence rate on this project's published shots (before the manual fixes, from `history/`
  where available).

## E. Removals that don't invent things

- **Start conservative and escalate only if needed**, replacing the 3.9.6 ladder:
  1. small mask (< ~6 % of the crop): LaMa init + denoise 0.6;
  2. medium mask: 0.75;
  3. large mask: 0.85, or the "Gerar o plano de novo sem a figura" suggestion from 3.9.6-D.
- **Auto-fill "Substituir por" from the surroundings** when the user leaves it empty. Sample the ring
  around the mask and the location descriptor to produce a plain description ("bare wall and wooden
  floor"), and always add "plain, empty, uncluttered" wording so the model doesn't invent objects.
- **Novelty check** on each candidate:
  - the layer-1 object/person detector and an edge-density comparison inside vs outside the mask;
  - penalise new distinct objects or figures inside the repaired area that aren't in the ring;
  - with C/D enabled, ask "Does the repaired area contain anything that doesn't belong in this
    setting?".
  
  Rank candidates by ghost (3.9.6) **and** novelty. A candidate with a new object is never ranked first.

## F. Optional "hard shot" provider (off by default)

The user already fixes the hardest shots in GPT Image by hand. Make that a supported, budgeted option
instead of a manual round trip.
- Add a provider interface for **external image generation/editing**. First implementation: OpenAI's image
  API, behind `EXTERNAL_IMAGE_PROVIDER` and an API key in `.env` (never printed).
- **Per-shot button "Refazer com provedor externo".** It sends:
  - the composed prompt, with the staging phrases;
  - the character sheets and location reference as input images;
  - for an edit, the current image and mask.
  
  The result goes through the existing import path (3.9.4-D): fit to 1920×1080, margin and copy checks,
  identity check, version history, `source: "external"`.
- **Budget:**
  - a per-project cap and a running cost estimate in the GUI;
  - read the prices from config (the user fills them in; don't hard-code prices that may change);
  - confirmation before each batch;
  - never automatic.
- The editorial record (3.9-D) marks these shots.

## G. Tests

- Location guard on the scene-4 excerpts: car keywords against `supermarket_aisle` → mismatch.
- The staging sheet schema and validation (evidence quotes exist in the narration).
- Staging phrases in prompts: inside/outside, seats, behind/in front.
- The adjacent-shot continuity check.
- The prompt-vs-narration patch applied to the shot spec (mocked).
- Vision questions generated from a spec.
- The fused-with-object heuristic on synthetic boxes and depth.
- The removal ladder by mask size.
- The auto "Substituir por" text.
- The novelty penalty.
- The external provider is disabled by default, the budget cap is enforced, and results go through the
  import checks (mocked).

## H. Report and commands

Report:
- location mismatches found in the two existing projects;
- staging-sheet examples for scene 4;
- prompt-adherence fixes;
- image-adherence results, if enabled;
- the removal ladder outcomes;
- the external provider wiring (no live call).

```powershell
python -m scripts.check_adherence --project 2026-11-09-she-filmed-every-moment-of-her-son-s-childhood-2   # report only
python gui.py   # Planos: "Aderência do prompt" antes de gerar; "Checar aderência das imagens" depois (opcional)
```
