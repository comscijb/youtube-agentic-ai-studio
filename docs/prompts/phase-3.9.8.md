# Private Gravity — Phase 3.9.8

**The referenced character, not a generic stand-in: full-body sheets for distant shots, text that agrees with the sheet, a face-identity check**

> Implementation prompt for Claude Code. Branch `feat/phase-3.9.8` from `main`. Report in **Portuguese**.
> No live calls in tests (mock ComfyUI and Gemini). Never read `.env`/tokens. Never push. One commit per
> section.

## 0. Evidence: project `2026-11-09-she-filmed-every-moment-of-her-son-s-childhood-2`

The user reports that in `s02_04, s02_08, s03_02, s03_10, s06_04, s07_01` a **generic woman** appears
instead of Hannah.

**Approved sheet** (`character_refs/hannah_price.png`): a gaunt woman around 45–50, **long** straight red
hair past the shoulders, **brown** eyes, deep lines. **In the shots:** a younger, smooth-faced, pretty
woman with a darker auburn **bob**, different in every shot. Nobody would recognise Hannah.

**Causes found:**
1. **No character reference at all on distant shots (5 of 6).**
   - `config.FACELESS_PORTRAIT_REFERENCE = {"wide": "none", "silhouette": "none", …}` (decided in 3.9.4-A
     to avoid a portrait pulling a second, close-up figure into wide shots).
   - Hannah's shots in this project: **wide 14/14 and silhouette 7/7 without her reference**, so 21
     shots are drawn from text alone.
   - Text identity ("a 39-year-old Caucasian woman, slender, oval face, hazel eyes…") describes millions
     of women. The model draws its default woman.
2. **The text contradicts the sheet.**
   - The visual-bible descriptor says "**hazel** eyes, **shoulder-length** auburn hair", and a signature
     marker repeats "auburn shoulder-length hair". The approved sheet shows long red hair and brown eyes.
   - Even with the reference (`s02_08`, medium, slot 0 = Hannah), the model splits the difference and
     drifts toward the text and toward its own default face.
   - The descriptor was written *before* the sheet existed and was never reconciled with it.
3. **The sheet and the shots are drawn in different "registers."**
   - The sheet suffix asks for a "stylised but consistent face with **gaunt planes**", so the sheet is
     aged and haggard.
   - Shots use the scene style, where faces come out younger and smoother. Identity is lost between the
     two.
4. **Framing text fights the action.**
   - Every wide shot gets "seen small and from behind, far from the viewer", even when
     `face_visibility` is `partial` and the action has her turning her head or placing a cake.
   - `s02_04` asked for a silhouette and came out fully lit and frontal.
   - The model ignores contradictory framing and falls back to its defaults.
5. **Nothing measures identity.** Layer 1 already ships SFace (`models/qa/face_recognition_sface_2021dec.onnx`),
   but no check compares a shot's face with the character's sheet.

## A. A full-body sheet per character (identity at a distance)

- For every on-screen character, besides the face sheet, generate and approve a **full-body sheet**:
  - standing, full figure head to feet, neutral pose, three-quarter view;
  - plain backdrop, same style suffix as the shots (not the "gaunt" sheet suffix);
  - in the character's **default wardrobe** from the visual bible.
  
  Hair length and colour, build, height and silhouette carry identity when the face is small. Two or
  three candidates; approval in the References panel, like the face sheet.
- **Reference choice per shot:**

  | Shot | Reference |
  |---|---|
  | close-up / medium with a face | face sheet |
  | wide / silhouette / back view / over-the-shoulder | **full-body sheet** |
  | medium full or partial face | both, if slots allow |

  The slot order and the four-slot limit stay. When slots run out, drop the style anchor first, as now.
- Re-run the 3.9.4-A probe with the full-body sheet:
  - the six shots above plus six wide/silhouette shots from the Ferryman project;
  - 3 seeds each, A = no reference, B = face sheet, C = full-body sheet;
  - measure the identity score (C), the extra-person count and the framing compliance;
  - set `FACELESS_PORTRAIT_REFERENCE` from the table (expected: full-body for wide and silhouette).
  
  Report the table.
- Wardrobe changes per scene (from the shot list `appearance`) are expressed in text on top of the
  full-body reference: "the woman from the first image, now wearing a cream silk blouse".

## B. Text that agrees with the approved sheet

- **When a face sheet is approved, reconcile the descriptor with it.**
  - One Gemini vision call (or the local VLM if reliable enough) reads the approved image and returns
    hair length, colour and style, eye colour if visible, apparent age range, build, and distinctive
    features.
  - Show the differences from the current descriptor in the GUI ("descritor: hazel / folha: castanhos —
    atualizar?"). The user accepts or edits; the visual bible keeps both values with provenance.
  - Signature markers are checked the same way: "auburn shoulder-length hair" must not survive when the
    sheet shows long red hair.
- **In shot prompts with a character reference, the text no longer re-describes the face.**
  - Use "the woman from the first image" plus **only** what the reference can't carry: wardrobe for the
    scene, expression, pose, and the signature markers that agree with the sheet.
  - The full descriptor stays only for text-only prompts.
  
  This removes the tug-of-war between text and image.
- **Age register:**
  - generate face sheets with the **same style suffix as the shots** (keep "hand-drawn engraving"; drop
    "gaunt planes" from the sheet suffix);
  - the descriptor's age must match what the sheet shows.
  
  Existing approvals stay valid; offer "Gerar folha nova (estilo das cenas)" for characters whose sheet
  looks older or harsher than the shots.

## C. Identity check (layer 1, deterministic)

- For every shot with a listed character and a detectable face (YuNet face box ≥ ~40 px tall):
  - compute the SFace embedding of the face crop;
  - compare it with the embeddings of the approved face sheet (and of 2–3 approved shots of that
    character as more anchors);
  - calibrate the threshold on this project: `s02_08` (generic) must fail; Hannah close-ups the user
    accepts must pass.
- For shots where the face is too small or hidden: a **hair and wardrobe check**.
  - Compare the dominant colour of the head region (hair) and of the torso (clothing) with the full-body
    sheet and the scene's wardrobe text.
  - Example: "red/auburn hair expected, dark brown found". This is advisory only.
- Failures:
  - get the hint "**personagem genérico**: rosto não confere com a folha de Hannah" in the grid and the
    viewer;
  - are auto-regenerated once (new seed, the right sheet from A);
  - then remain for review;
  - batch action "Gerar de novo os genéricos".
- The scores go in the manifest and the QA report.

## D. Framing that agrees with the action

- Generate the faceless framing sentence ("seen small and from behind…") **only** when
  `face_visibility == "none"` **and** the action doesn't contradict it: no "turning her head", "looking
  at", "smiles", "placing" with the face toward the camera.
  - Otherwise, use a neutral distance phrase ("seen at full length, at a distance").
  - Use a small rules table plus a validator in the shot planner. Log any conflict it resolves.
- **Silhouette shots** must actually read as silhouettes:
  - "backlit, her shape dark against the bright corridor; face and clothes in shadow";
  - the full-body sheet as the reference;
  - a check that the figure's mean luminance is well below the background's (advisory).

## E. Tests

- Reference selection by shot type and face visibility, with the four-slot limit and the style anchor
  dropped first.
- A full-body sheet job uses the scene style suffix and the default wardrobe.
- The descriptor reconciliation diff (mocked vision output).
- Prompts with a reference don't contain the face descriptor; text-only prompts still do.
- The SFace identity scoring on fixture crops: a matching pair passes, a generic face fails.
- The hair-colour check.
- The framing rules for contradictory actions.
- Silhouette wording.

## F. Report and commands

Report:
- the probe table;
- the descriptor differences found in this project (Hannah, Leo);
- identity scores before and after for the six shots;
- how many of the project's 62 shots were flagged as generic.

```powershell
python gui.py   # Referências → "Folha de corpo inteiro" (Hannah, Leo) → aprovar → conferir descritor → Planos → "Gerar de novo os genéricos"
```
