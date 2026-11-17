# Private Gravity — Phase 4.4

**Full quality audit of everything built in 3.6–4.3: requirement-by-requirement verification, an end-to-end "golden episode" dry run with a QA dossier, a single quality bar, and fixes for whatever falls below it**

> Implementation prompt for Claude Code. First merge `feat/phase-4.3` into `main` (see 0). Then branch
> `feat/phase-4.4` from `main`. Report in **Portuguese**. No live uploads. Gemini, ComfyUI and Kokoro may run
> live for the golden episode only (section C), with costs reported. Never read or print `.env`, tokens or
> `client_secret.json`. Never push. One commit per fix or check.

## 0. State before starting (checked on 2026-11-17)

- **Phases 4.1, 4.2 and 4.3 are implemented.** Reports are in `docs/reports/4.1`–`4.3`; the suite has
  1298 tests OK (6 skipped).
- **`feat/phase-4.3` isn't merged.** Local `main` = `7c3d9be` (the 4.2 docs), and neither local `main` nor
  `origin/main` contains 4.3. There are also uncommitted changes: `data/publications.json`,
  `music library/music_index.json`, `docs/prompts/phase-4.3.md`.
  - Commit them on `feat/phase-4.3`, merge into `main` with a fast-forward if possible, and confirm
    before going on.
- **Known gaps found in the reports and the code:**
  1. **No way to upload Shorts for an already published episode.** The 4.3 report says so ("Falta o
     envio"), and the 3.8-F "Enviar Shorts pendentes" action doesn't exist in the code (no match for
     it). The `shorts_backfill` renders Shorts that can't be published.
  2. **Reach data (impressions and CTR) never arrived.** The Reporting job
     `channel_reach_basic_a1` was created on 2026-11-16T02:36Z, but `data/analytics/reach/` is empty,
     and every cached video shows `impressions 0`, `ctr None`. Either the reports aren't downloaded yet,
     or the sync doesn't fetch them.
  3. **The attention calibration against real retention hasn't run.** The 4.1 report: the APIs were off.
     Retention curves now exist.
  4. **Intelligibility findings remain** on the Sunrise cut: the voice only 5.5–9.7 dB above the music at
     0:45–0:51. The current fix is manual ("Baixar a música").
  5. **The SFX library is incomplete.** The user is downloading it; `scripts.sfx_library` lists the
     missing tags.
  6. **Missing staging sheets after a Gemini 503** (3.9.9 report: scenes 5–7 had no staging, so weaker
     adherence questions).
  7. **The in-pipeline thumbnails still use the old formula** (grey engraving plus a question in white
     caps). They were replaced by hand in GPT Image for the A/B test. 4.0 changed metadata, not the
     design.

## A. One quality bar (`docs/QUALITY_BAR.md` + `studio/quality_bar.py`)

- Put **every threshold** introduced in 3.6–4.3 in one documented place and **one code module that the
  checks import** (no duplicated magic numbers). Group them by viewer moment:
  1. **Click:** title topic rule, title validator, thumbnail rules (contrast, head in frame, text
     ≤ 4 words, variety vs the last 3 episodes).
  2. **First 5–30 s:** first-5-seconds rule, inciting event by 0:45, cold open, first-5-seconds AV
     check.
  3. **Momentum:** dead-air gap ≤ 25 s, live loop, holds ≤ 7 s, quiet stretches ≤ 20 s, seconds per view by
     beat.
  4. **Craft:** identity score, prompt/image adherence, margins, copies, generic figures, removal ghosts
     and novelty, stutter hits, narration joins, loudness and intelligibility.
  5. **Shorts:** hook-first, swipe gate, ≤ 3 s per view, duration by format, CTA, loop.
  6. **Packaging and delivery:** description template, localizations, checklist items, schedule.
- Add a **readiness score** per episode and per Short, computed from these checks with severities. The
  approval gate shows it: **"Pronto" / "Pronto com ressalvas" / "Abaixo do padrão"**, with the failing
  items and one-click links to each fix.
  - Below standard requires a typed override (as now), recorded in the editorial record.

## B. Requirement-by-requirement audit (3.6 → 4.3)

For each phase prompt in `docs/prompts/` (3.6, 3.7, 3.8, 3.9, 3.9.1–3.9.9, 4.0–4.3):
- list every requirement as a row: **implemented / partial / missing / superseded**, with the code
  location, the test name, whether it is wired to the **GUI and the CLI**, and its default setting;
- flag requirements later phases superseded (e.g. the tape glitch replaced by the film look, the
  3.9.6 ladder by 3.9.9's), and check the old code is removed, not left dormant;
- produce `docs/reports/4.4/audit.md` (the table) and a short list of the **partial or missing** items.
  Fix them in this phase unless they need a decision from the user; list those.

## C. Golden episode: end-to-end dry run with a QA dossier

- Run the **whole pipeline** on one new episode, without uploading:
  - use `briefs/life-not-chosen.md` if the user has created it (the backlog's next Type B: "Everyone Got
    a Video of the Life They Didn't Choose");
  - otherwise use `briefs/everyone-was-him.md` in a fresh project.
- The run covers every stage: brief → story → outline (attention beats) → screenplay (attention map,
  simulation, revision) → narration (stutter scan, joins) → music plan → visual bible, sheets (face +
  body) → staging → shots (adherence, identity, margins, copies) → cut (rhythm, key lines, grades, SFX,
  music) → video attention map → Shorts (formats, swipe gate) → packaging (titles, thumbnail concepts,
  description, localizations) → readiness score.
- Produce a **QA dossier** as a self-contained HTML in `docs/reports/4.4/golden/`, readable by a person:
  - every check with its value against the bar;
  - the screenplay and video attention maps;
  - the simulation curves;
  - a contact sheet of all views;
  - links to the preview minute and each Short;
  - timings and costs per stage (Gemini calls, GPU minutes);
  - every human decision the run needed.
- Anything below the bar must be **explained** (cause and owner: code, model limit, or a needed asset)
  and fixed when it's in code.

## D. Close the known gaps (0)

1. **Upload Shorts for published episodes.** Add an "Enviar Shorts" action for a project whose episode
   is already in the ledger:
   - uploads only the approved Shorts, linked to the episode URL;
   - follows the schedule (spread across the week, ≥ 3 h apart);
   - writes `parent_video_id` to the ledger;
   - adds the checklist items (related video, pinned comment, dubbing);
   - never re-uploads the episode or an already uploaded Short (idempotent by hash).
   
   Also add it to `scripts.shorts_backfill --upload` (asking for confirmation in the CLI).
2. **Reach data:**
   - check the Reporting API job;
   - list and download the available reports with their creation times;
   - parse impressions and CTR per video into the cache;
   - show "relatório ainda não disponível (o YouTube leva até 48 h)" when there is none;
   - a test with a recorded CSV.
3. **Attention calibration:** run `scripts.attention_calibration` on the published episodes with
   retention. Report the correlation between simulated and real drop points; adjust the persona prompts
   or thresholds if they disagree systematically. Never overfit to a single episode.
4. **Intelligibility:**
   - make the music level **dynamic**: per 1-second window, lower the music automatically where the
     speech band would fall under the 10 dB margin, with smooth 300 ms ramps;
   - re-check the Sunrise cut;
   - the check must pass with no manual button.
5. **SFX:** when tags are missing, the cue sheet uses the closest available tag, or skips the cue with a
   note. The report lists the downloads that matter most, ranked by how often the screenplays need them.
6. **Gemini resilience:**
   - every multi-call stage (staging, attention map, simulation, Shorts scripts, localizations) resumes
     from checkpoints after a 503;
   - when a stage gives up, the stage card says exactly which parts are missing, with a "Completar"
     button. A partial result must never look complete.
7. **Thumbnails at the new standard.** Replace the old in-pipeline design:
   - thumbnail concepts follow the A/B-test principles: an emotional face **or** a symbolic object,
     **or** a scale contrast; one saturated accent; ≤ 4 words; text never over faces;
   - a **variety rule** against the last 3 episodes' compositions and text formulas;
   - every episode gets two concepts, as **ready-to-paste GPT Image prompts** in the channel style
     (`thumbnail_prompts.md` in the project), plus the optional render through the 3.9.9 external
     provider (budgeted, off by default);
   - the local Klein render remains as a fallback;
   - if the user shares the A/B results from Studio (ask for them; never scrape), record the winning
     concept type per video in the ledger for the 4.0 swap loop.

## E. Code health and UX pass

- **Static checks:** `python -m pyflakes` (or ruff, if installed) on the codebase. Fix unused imports and
  names and unreachable code. Remove dead routes and JS left by superseded features (list them).
- **Settings:** every 4.x switch is visible in Settings with a one-line explanation in Portuguese and its
  default. Check nothing reads `os.getenv` directly for these settings outside `config.py`.
- **Logs:** no secrets, no tracebacks shown to the user without a plain-Portuguese message, and a per-run
  log file (3.8) for every stage.
- **GUI:** a Playwright screenshot audit of every tab after 4.x (as in 3.9.1), with a before/after for any
  fix. Check the right column scrolls, the Shorts studio fits at 1366×768, and the attention maps are
  readable.
- **Performance:** time each stage of the golden episode; flag any stage slower than 2× its 3.9 timing,
  and fix the obvious ones (caching, repeated model loads).
- **Docs:** README and SETUP describe the current pipeline (4.x stages, data diagnosis, Shorts studio,
  quality bar), with no outdated instructions.

## F. Tests

- The quality-bar module is the single source (a test fails if a check hardcodes a threshold that
  exists in the bar).
- Readiness score aggregation.
- Upload of Shorts for a published episode: idempotency, scheduling, ledger links (mocked).
- Reach CSV parsing.
- Dynamic music level keeps the 10 dB margin on a synthetic mix.
- Missing-SFX fallback.
- Resume after a 503 per multi-call stage.
- The thumbnail variety rule.
- GPT Image prompt export format.
- A smoke test of the GUI tabs (Playwright, skipped without Chromium).

## G. Report

`docs/reports/4.4/README.md` in Portuguese:
- the merge status;
- the audit summary (implemented, partial, missing, superseded);
- the golden episode dossier link and its readiness score;
- every item that was below the bar and what was done;
- the decisions left for the user;
- the full-suite result.

```powershell
git checkout feat/phase-4.3; git add -A; git commit -m "chore: 4.3 data and prompt"; git checkout main; git merge --ff-only feat/phase-4.3
python gui.py   # Aprovação → pontuação de prontidão; Projeto → Enviar Shorts; Desempenho → alcance e calibração
```
