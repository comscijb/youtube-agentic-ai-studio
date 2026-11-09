# Private Gravity — Phase 3.9.2

**Narrative QA that understands the brief: semantic faithfulness, no pasted brief text, honest scene budgets, stricter continuity, heard-thought delivery**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests (mock Gemini, TTS). Never read `.env`/tokens. Never push. One commit per section.

## 0. Evidence: project `2026-11-03-everyone-heard-what-he-thought` (Type B, brief `briefs/thought-town.md`)

The review status was `revision_required`, with 13 major findings, six repair attempts and the editorial
scores at 9–10/10. Reading the screenplay shows the opposite picture:
- the real defects were missed;
- most of the major findings are false positives;
- the automatic revision **made the script worse**.

1. **Brief text pasted into the narration.** The checker reported "the brief's line is not spoken as
   direct dialogue: "clean thinking"" and the same for "what a lovely evening". The targeted semantic
   revision for scenes 2–4 then pasted whole beat sentences from the brief:
   - scene 3: "The town lives by "clean thinking". Children learn to think gently; there is no lying, no
     crime, and Thomas and Nell have never argued in 26 years. **Next morning** he thinks warm, perfect
     thoughts…". The scene already started with "The next morning".
   - scene 4: "Now he can hear the effort. An editor in his head rewrites every thought before it forms…
     **At supper** Nell thinks "what a lovely evening"… **He starts walking to the Line at night.**" This
     sits inside an afternoon scene, *before* the supper scene, and breaks chronology.
   
   The editorial review still scored chronological continuity **10/10**.
2. **False positives in `agents/premise_brief.faithfulness`:**
   - `beat_quotes` treats **every** double-quoted span in a beat as dialogue that must be spoken in double
     quotes. That includes a term ("clean thinking"), a heard thought ("what a lovely evening") and the
     narrator's closing line. The closing line *was* in scene 9 word for word, and was still flagged,
     because it is narration and not dialogue.
   - `ending_items` turns each sentence of the ending beat into a literal word-overlap check (ratio 0.6).
     "Nell asks quietly", "Cut before he answers", "A long silence; he opens his mouth" and "they stand
     in the dark beyond the stones" were all flagged, although all of them happened in the scene.
   - `must_include` uses lexical overlap. "heard thoughts as quoted inner lines, quieter than speech" (a
     **style/format** instruction) and "the unanswered final question" were flagged, although both are
     present.
   - `_people` read the "Townsfolk:" bullet as a named character missing from the Character Bible.
3. **The budget fights the brief.** The ending is meant to be abrupt ("cut before he answers"), but the
   budgets gave the aftermath scene 160 words. Six expansion attempts failed or were kept as "best".
   Padding pressure produced narrator lectures in scene 9 ("We are left suspended between… wondering where
   the boundary truly lies…") and screen directions inside the narration (scene 8: "…and the story cuts
   immediately to black").
4. **Real defects not caught:**
   - the narration contains a screen direction;
   - wardrobe markers leak into actions: Nell "smooths the fabric of her dark green wool coat" at the
     supper table indoors, and the coat and braid come back in scene 7;
   - the reversal's realisation ("he has never known her real mind") is spent in scene 5 and repeated in
     scene 7;
   - a customer is called "a baker";
   - stacked modifiers.
5. The brief asks for heard thoughts "quieter than speech". The pipeline has no way to deliver that in
   the audio.

The user's hand edits fix the screenplay: `docs/prompts/edits/thought-town-2026-11-03.json`, 18 edits,
1409 words. After those edits the deterministic review still says `revision_required`, purely because of
the false positives above and the scene-9 budget. Use that project and those edits as the **regression
fixture**:
- the fixed checker must pass the edited script;
- it must still flag the pasted-brief version (`script.pre_revision.json` and the current `script.json`).

## A. Brief-aware faithfulness (deterministic where safe, semantic where needed)

### A.1 Classify quoted spans in beats

`classify_quote(beat_text, span)` returns one of four kinds, using the words around the span:

| Kind | How it's recognised | Pass condition |
|---|---|---|
| `dialogue` | an attribution near it: says/asks/answers/tells/replies/speaks aloud/whispers + a character name or pronoun | the line appears as direct speech in double quotes (current `said`) |
| `thought` | thought/thinks/heard/escapes him/inner | the line appears in any quotes (single or double) |
| `narrated` | "narrated line", "narrator", "final line", "turns the question to the viewer" | the line appears anywhere in the narration (≥ 0.8 overlap), quoted or not |
| `term` | ≤ 3 words with no verb, e.g. "clean thinking", "the Line" | the term appears anywhere |

**Unknown → `term`** (the lenient kind).

### A.2 Semantic confirmation for everything that isn't literal

- `must_include` items, ending details and beat coverage are first checked lexically.
  - A **lexical miss is not a finding.** It becomes a candidate.
  - All candidates go to **one** low-temperature Gemini call (contract-first JSON), run together with the
    existing editorial review. For each item it returns
    `{item, present: true/false, scene_id, evidence_quote}`.
  - A miss is `major` only when Gemini says `present: false`. When Gemini says `present: true`, the
    `evidence_quote` must exist verbatim in the screenplay, or the answer is discarded and the item stays
    an unverified `minor`.
- Recognise **style/format instructions** in `must_include`: items describing *how* something is
  written or voiced ("as quoted inner lines", "quieter than speech", "in silence", "never shown").
  - Don't check them as content. Route them to a `style_constraints` list: the screenplay prompt
    receives them as instructions, and the TTS receives them in section E.
  - Deterministically, check only what can be checked, e.g. "heard thoughts appear in single quotes".
- **Stage directions** in the ending beat ("cut before he answers", "a long silence", "the screen goes
  dark") are **intent**, not narration. They are satisfied when the scene *ends at that point*:
  - the semantic check asks "does the final scene stop before Thomas answers?";
  - they must never be written into the narration (see C.2).
- `_people`: only treat a bullet as a character when it starts with a capitalised personal name (one or
  two capitalised words followed by a comma or an age). Skip collective or background entries:
  townsfolk, villagers, customers, crowd, extras, "never named".

## B. Revisions must never paste the brief

- **Brief-paste guard**, run after every revision or expansion call and after scene generation:
  - reject the new scene text if it contains any **≥ 9-word span** copied from the brief's beats,
    logline, setting or tone. Authorised quoted lines (dialogue/thought/narrated) are exempt;
  - on rejection, retry once with the instruction "dramatise, don't copy; these sentences are
    instructions, not prose", then keep the previous version.
  
  Add the deterministic finding `brief_paste` (major) with the copied span as evidence.
- Revision prompts receive the brief beat as **intent** ("what must happen"), wrapped as
  `<intent>…</intent>`, never as text to include. The only exception is the authorised quoted lines,
  passed separately as `<required_line kind="dialogue|thought|narrated">`.
- **Chronology guard:**
  - extract the time markers of each scene (dawn, next morning, afternoon, supper, night, "later",
    weekday names);
  - flag a scene that mentions an event planned for a **later** scene (outline order), e.g. "At supper…"
    inside the afternoon scene, or "walks to the Line" before the Line scene;
  - flag duplicate openers within one scene ("The next morning… Next morning…");
  - findings are `major`, with evidence.

## C. Prose lint additions (deterministic)

1. **Screen directions in narration** (major): "cut(s) to black", "the screen", "the story cuts",
   "camera", "fade", "scene ends", "we see". The narrator never describes the edit.
2. **Lecture lines** (major in the last two scenes, minor elsewhere): first-person-plural or abstract
   moralising sentences ("we are left", "we all", "wondering where the boundary truly lies", "the truth
   is that", "perhaps we"…).
   - A narrated line authorised by the brief is exempt.
   - When the brief contains a closing narrated line, everything else in the last scene must be concrete
     action, image or dialogue.
3. **Signature-marker leakage** (minor):
   - wardrobe and signature markers from the visual bible (coat, braid, apron, glasses) mentioned in
     more than 40 % of a character's scenes;
   - a wardrobe item used where the setting makes it implausible (a coat at an indoor supper table,
     using a simple indoor/outdoor flag on locations).
   
   Markers belong to the visual bible and shot prompts, not to every paragraph.
4. **Spent reversal** (minor, semantic, inside the editorial call): the reversal beat's realisation
   appears before the reversal scene.
5. **Role slips:** a character called by a role that contradicts the bible ("a baker" when the only
   baker is Thomas). This is semantic, part of the same call.

## D. Scene budgets that respect the brief

- The outline may mark scenes `pace: "brief"`, used for a coda or epilogue, or when the brief demands an
  abrupt ending (detect "cut", "abrupt", "before he answers", "one final line").
  - Brief scenes get a budget of 40–110 words. The remaining words are redistributed to the other scenes
    in proportion.
  - Brief scenes are excluded from the per-scene minimum ratio. They are still checked against their
    own maximum.
- Expansion policy:
  - at most **one** expansion attempt per scene per run;
  - never expand a `brief` scene;
  - never expand when the total is already within the accepted range;
  - if a scene stays short after one attempt, report it as `minor` with the reason, and don't loop.
- Type B and Type C: the accepted total range stays 90–110 %. Short scenes are acceptable when the total
  is within range.

## E. Heard thoughts sound different (audio and captions)

- Screenplay convention, also added to the scene prompts:
  - **heard thoughts** go in single quotes, `'like this'`;
  - **speech** goes in double quotes;
  - the narrator stays unquoted.
  
  The style constraint from A.2 enables this convention per project. The default is on for Type B
  briefs whose rule involves thoughts, voices or telepathy, detected from the brief's setting text.
- Narration post-process, using word timings: spans inside single quotes are delivered "quieter than
  speech".
  - Apply −5 dB gain with 30 ms fades at the boundaries, plus a very light high-pass at 120 Hz and a
    short, soft room reverb (send ≤ 12 %).
  - Implement it with ffmpeg `volume`/`highpass`/`aecho` on the span segments and splice them back; or
    produce a gain envelope applied in the mix.
  - It must work with every TTS backend (Kokoro has no style controls).
  - Config: `HEARD_THOUGHT_STYLE = "off" | "soft"`; default `soft` when the convention is on.
- Captions render thought spans in *italics*: ASS `\i1` in the burned captions, and `<i>` in the
  uploaded SRT.
- Keep word timings, the Shorts slicing and the alignment untouched. The effect is gain and filters on
  existing audio, with no time stretch.
- **Tests:**
  - quote-span extraction with apostrophes inside words ("don't", "Wickham's");
  - span timing mapping;
  - gain applied only inside the spans;
  - duration unchanged;
  - italics in ASS and SRT;
  - off by default for Type A.

## F. Editorial review calibration

- Pass the deterministic findings (brief_paste, chronology, screen directions, lectures) to the editorial
  Gemini call as context.
  - Instruct it that a dimension **cannot score above 6** while a related confirmed major finding exists:
    chronology for chronology findings, dialogue/subtext for lectures.
  - Clamp the score in code if it doesn't comply, and record the clamp in the review.
- Add a short, explicit checklist to the editorial prompt. It must answer each item with evidence:
  - time order of scenes;
  - any sentence that reads as an instruction rather than story;
  - the reversal spent early;
  - narrator explaining the theme.

## G. Regression tests and commands

Fixtures (copied into `tests/fixtures/thought_town/`): the brief, outline, character bible,
`script.pre_revision.json`, the current `script.json`, and the edited script (the current `script.json`
with `docs/prompts/edits/thought-town-2026-11-03.json` applied).

The tests (mock Gemini returns `present: true` with real evidence quotes for the edited script):

| Script | Expected |
|---|---|
| **Edited** script | no major brief-faithfulness finding; no brief_paste; no chronology finding; no screen direction; status `ready_for_human_review` or `minor_only` |
| **Current** script | brief_paste in scenes 3 and 4; chronology in scene 4 ("At supper" before the supper scene); screen direction in scene 8; lecture in scene 9 |

Also test the following:
- the quote classifier on the Thought Town, Only One and Envy briefs;
- the `_people` filter;
- `pace: brief` budgets;
- one expansion attempt only;
- the editorial score clamp.

Report:
- before/after finding counts on the fixture;
- whether any **published** episode's script would now get new major findings. Run the checker offline
  on the archived scripts and list them; don't change those scripts.

```powershell
python -m scripts.apply_edits --project 2026-11-03-everyone-heard-what-he-thought --edits docs/prompts/edits/thought-town-2026-11-03.json
python gui.py   # Projeto → Roteiro: revisão deve sair sem falsos positivos; depois Narração (Lewis + pensamentos suaves)
```
