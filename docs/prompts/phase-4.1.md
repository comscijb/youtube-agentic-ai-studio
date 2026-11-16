# Private Gravity — Phase 4.1

**Data that actually arrives (fix the Analytics 403) · Viewer-experience QA for the screenplay: an attention map, a skeptical-viewer simulation, rewrites until it holds**

> Implementation prompt for Claude Code. Branch `feat/phase-4.1` from `main`. Report in **Portuguese**.
> No live calls in tests (mock Google APIs and Gemini). Never read or print `.env`, `client_secret.json` or
> token files. Never push. One commit per section.

## 0. Diagnosis (review of 2026-11-15)

- **YouTube stopped expanding distribution.** There is no penalty (no restrictions in Studio). Small test
  batches get CTR of 0.7–2 %, and about 60 % leave in the first 30 s (episode 3: average 1:37 of 9:48,
  16 %). **People who pass the opening stay to the end** (flat curve). The stories hold; the entry and
  the pacing don't.
- **The pipeline optimises for "faithful, good literature", not for "a viewer on a phone who can't stop".**
  Measured on three archived episodes:

  | Metric | Value |
  |---|---|
  | Questions / open loops in the text | 1–2 in a whole episode |
  | Average sentence | 12–16 words |
  | Average image duration on screen | 9.5–9.7 s |
  | Sound effects | none |

  - The first 30–60 s after the cold open are scene-setting description: Sunrise opens with a 173-word
    exposition ("The old lord of Eastmere sat in the drafty study…").
  - The editorial review still scores these scripts 9–10.
- **The feedback loop has never had data.** `data/analytics/status.json` records
  `HttpError 403 … youtubeanalytics.googleapis.com/v2/reports?ids=channel==MINE…` (last success: never).
  The 4.0 retention loop learned nothing.

This phase does two things: **A.** make the data arrive; **B–F.** put the viewer at the centre of
screenplay QA. (Shorts get their own phase later. Editing rhythm and sound design are also later phases.)

## A. Analytics: diagnose and fix the 403

1. **Capture the real reason.** Parse the `HttpError` content (JSON `error.errors[].reason` and
   `error.message`) and store it in `status.json` as `reason` and `message`. Show it in Desempenho in plain
   Portuguese:

   | API reason | Message to show |
   |---|---|
   | `accessNotConfigured` / "has not been used in project … or it is disabled" | Ative a *YouTube Analytics API* e a *YouTube Reporting API* no projeto do Google Cloud (link direto com o número do projeto extraído da mensagem). |
   | `insufficientPermissions` / scope missing | Autorize de novo (the button already exists) and **pick the Private Gravity channel** on the Google account screen. |
   | `forbidden` with an authorized identity that isn't the channel | A conta autorizada não é a do canal: escolha o canal Private Gravity na tela de autorização (contas de marca aparecem como canais separados), ou dê a essa conta acesso ao canal em Studio → Configurações → Permissões. |

2. **Doctor command:** `python -m scripts.analytics_doctor`, read-only, never printing tokens. It reports:
   - which channel the stored credentials resolve to (`channels.list(mine=true)` → id and title); it must
     be `UCLXUyBGw-ojvJDAT5RSOjZQ` (Private Gravity);
   - the granted scopes (from the credentials object, not the file);
   - one minimal Analytics query (the last 7 days of channel views) and one Reporting
     `reportTypes.list`, with the parsed reason on failure;
   - a checklist of what to fix.
   
   The GUI button "Diagnóstico de dados" runs the same thing.
3. Query with `ids=channel==<channel id from channels.list>` instead of `channel==MINE` when the
   resolved channel is the expected one. This avoids ambiguity when the Google account has more than one
   channel.
4. After the fix, run the first full refresh. The report must include real numbers for the 6 published
   long-form videos and their retention curves, and the 4.0 "Lições de retenção" must be generated.
5. Tests: reason parsing for the three cases (recorded HttpError bodies), the doctor output with mocked
   services, the channel-id query.

## B. Attention contract for every screenplay

New module `agents/attention.py`. Timing is estimated from the channel voice's calibrated wpm plus the
narration pauses, so every check speaks in **seconds of video**.

**Deterministic checks** (findings with timestamps):
- **First 5 seconds** (about the first 12 spoken words of the episode): must contain a concrete
  strangeness, conflict or threat: an action, a line of dialogue, or an impossible fact. No setting,
  weather or appearance description.
- **Inciting event by 0:45.** After the cold open, the story's central problem or rule must be in motion
  within 45 s of narration.
- **Dead-air gaps:** no stretch longer than **25 s** without an *attention event*:
  - a new question or open loop;
  - a reveal;
  - an escalation or decision;
  - a line of dialogue that changes something;
  - a time jump with stakes.
  
  The events are tagged by the semantic pass (C) and checked here.
- **Description runs:** three or more consecutive sentences with no action, dialogue or question → a
  finding.
- **Ear-friendly sentences:** average ≤ 12 words; at most 15 % of sentences over 20 words; no sentence over
  30. Stacked adjectives keep their existing lint.
- **Open loops:**
  - each loop opened must close later, or be the deliberate final question;
  - **at least one open loop is alive at every moment** after the hook;
  - **one new loop or partial reveal per scene**, at least.

The findings feed the targeted revision (E). Severity is major for:
- the first-5-seconds check;
- the inciting event;
- a gap over 40 s;
- no live loop for more than 60 s.

## C. Attention map (semantic pass, once per screenplay)

- One low-temperature Gemini call, contract-first JSON, tags every sentence with zero or more of:
  `question`, `open_loop:<id>`, `close_loop:<id>`, `reveal`, `escalation`, `decision`, `dialogue_turn`,
  `description`, `reflection`. Each tag carries the exact quote.
  - Unverifiable quotes are discarded (the same rule as 3.9.2).
- Store `attention_map.json` with sentence timestamps.
- **GUI: "Mapa de atenção"** in the Roteiro review. A timeline of the episode shows:
  - colour blocks per 15 s, from green (several events) to red (description only);
  - live loops as horizontal bars;
  - deterministic findings as markers;
  - click a block → the sentences.

## D. Skeptical-viewer simulation (the gate)

- Three viewer **personas** in one call or three short calls, configurable:
  1. "25–34, scrolling on a phone at night, likes dark stories, impatient, will swipe the moment nothing
     is happening";
  2. "35–50, watches philosophy and psychology channels, patient with ideas, hates padding and
     repetition";
  3. "18–24, arrived from a Short, wants the hook paid off fast".
- Each persona reads the screenplay **in 15-second blocks**, with timestamps, and returns per block
  `{interest 1–10, would_leave: bool, reason, quote}`, plus the **first second they would leave**.
- **Gate:**
  - no majority "leave" in the first 30 s;
  - median block interest ≥ 7;
  - no two consecutive blocks with median < 5;
  - the reversal block ≥ 8.
  
  Store `viewer_sim.json`. Show the three curves on the attention map as a *predicted retention*.
- **These predictions are a filter, not the truth.** Calibrate them in F against real retention and
  report the correlation. Never claim them as forecasts in the GUI: label them "simulação".

## E. Rewrite until it holds

- When B or D fails, run a **targeted attention revision** on the failing scenes only. The instructions
  are built from the findings and the persona reasons:
  - open on action or dialogue;
  - cut description to one concrete image;
  - move the reveal earlier;
  - add a question or partial reveal at the scene end;
  - split long sentences;
  - keep the dialogue that turns the scene.
- Constraints carried from earlier phases:
  - brief faithfulness (3.9.2 quote kinds);
  - no brief paste;
  - the chronology guard;
  - the cold-open rules (3.9.7);
  - authorised quoted lines kept word for word.
- **Word budget:** shorter is allowed. Remove the ≥ 90 % total floor when the cut text improves the
  attention checks; report the new estimated duration.
- At most **2 rounds**. After that, the remaining findings and the simulation go to the human review
  with a clear "não passou no portão de atenção" status. The user may override with a reason (recorded).
- Re-run C and D after each round.
- **Editorial review update:**
  - add the dimensions `opening_grip`, `momentum`, `curiosity_management` and `ear_readability`;
  - clamp `dramatic_progression` and `pacing` to ≤ 6 while any major attention finding exists (same
    mechanism as 3.9.2-F).

## F. Prompts for writing, not only for checking

- **Outline:**
  - each scene gets `attention_beats`: the hook or question that opens it, the partial reveal or
    escalation inside it, and the question it ends on;
  - the open-loop plan: which loops open where and close where;
  - the outline validator checks the B rules on the plan before any scene is written.
- **Scene prompts.** Add a short "write for the ear, for a phone" style block:
  - start scenes in motion;
  - one concrete sensory detail rather than a paragraph of setting;
  - short sentences, with a varied rhythm;
  - end each scene on a question, a reveal or a turn;
  - no throat-clearing ("The morning was quiet…", "Days passed…").
  - Keep the channel's literary voice: precise, restrained, never clickbait-cheap.
- **Duration experiment** (config, default unchanged; the user decides):
  - `ATTENTION_TARGET_MINUTES = {"A": 9, "B": 9, "C": 7}`, used when a brief doesn't set minutes;
  - report the estimated duration before and after the attention revision.

## G. Calibration and regression

- Run B–D offline (recorded or mocked Gemini) on the archived screenplays: Envy, Only One, Thought Town,
  Ferryman, Filmed Childhood, Everyone Was Him, Sunrise. Report:
  - the attention findings per episode;
  - the simulated leave points;
  - for the episodes with real retention after A, the correlation between the simulated and real drop
    points.
  
  Expected: Thought Town and Sunrise are flagged in their first 45 s.
- **Fixtures:**
  - the Sunrise opening (173-word exposition) must fail the first-5-seconds or inciting-event checks;
  - a rewritten opening that starts on the deed being sealed must pass.
- **Tests:**
  - timing estimation;
  - each deterministic rule;
  - attention-map quote validation;
  - the gate logic;
  - the revision loop: at most 2 rounds, guards preserved, a shorter budget allowed;
  - the editorial clamp;
  - the outline `attention_beats` validation.

## H. Report and commands

```powershell
python -m scripts.analytics_doctor                    # tells exactly why data was blocked and what to do
python gui.py   # Desempenho → Diagnóstico de dados; Roteiro → Mapa de atenção + simulação + revisão de atenção
```
