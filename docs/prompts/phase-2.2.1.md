# Private Gravity — Phase 2.2.1

**Review reliability · Targeted semantic revision · Style rule · Docs · First-render preparation**

> Implementation prompt for Claude Code. Read the whole document first. Work on branch
> `feat/phase-2.2.1` created from `main` (currently `dbad0e1`). Report to the user in
> **Portuguese**. Never push. No live API calls in tests or during implementation.
> Never read or print `.env`, `client_secret.json` or token files.

---

## 0. Evidence: benchmark `output/benchmarks/private-gravity-20261021T001448Z`

Phase 2.2 produced the strongest screenplay so far (*The Man Who Needed Everyone to Envy Him*,
1 536 words, target 1 500, 38 quoted lines, all seven Type A movements declared). Independent
review score ≈ 8/10. Three defects remain in the **system**, plus two in this specific text.

1. **False-positive duration finding.** The editorial reviewer (LLM) reported a `major` finding
   `issue_type: "duration_mismatch"` claiming *"The actual screenplay narration totals only 1,219
   words"*. The real count is 1 536 (`estimated_duration_seconds` 737 is correct). This
   hallucinated finding alone downgraded the status to `revision_required`, which the Phase 2.4
   gate treats as blocking. LLMs cannot count words reliably; duration is owned by the
   deterministic checks (`scene_word_budget`, `total_word_budget`).
2. **No repair path for semantic findings.** The reviewer correctly flagged a `major`
   `lecture` finding in s08 (an academic Girard citation in the narrator's voice), but only
   word-budget deficits have an automatic, bounded revision pass. Semantic majors just end the run.
3. **Adjective stacking** survives review (*"pure, unmitigated estrangement"*, *"unadulterated,
   sorrowful pity"*, *"devastating, unhurried calm"*) and reads badly aloud; `literary_style`
   was scored 9.
4. Text-specific: s08 lecture sentence; guests appear in s06–s08 ("the guests", "a guest",
   "the remaining guests") though s01 sets up the dinner as if only Marcus were invited.
5. Text-specific (minor): s07 has Elena take keys "from her cardigan pocket"; s03 dresses her in
   an ivory silk shirt and no cardigan is ever mentioned.

---

## 1. Editorial findings must not contradict deterministic facts

In `agents/narrative.py` (`merge_editorial_review` and helpers):

- **Duration ownership.** Discard editorial findings whose `issue_type` or `claim` concerns
  word count, duration, runtime, length or pacing-by-count (case-insensitive match on
  `duration|word count|words|runtime|length|minutes|wpm`, applied to `issue_type`, and to
  `claim` only when it contains a number). Duration verdicts come exclusively from the
  deterministic report.
- **Numeric claim verification.** For any remaining finding whose `claim` or `evidence`
  asserts a number of words, scenes, characters or an age, verify it against the artifacts
  (script word count, scene count, Character Bible ages). If contradicted, discard it.
- Discarded findings are kept for transparency in `discarded_editorial_findings`, each with
  `reason` (`deterministic_owner` | `contradicted_by_artifacts`) and the verified value. They
  never affect `status`, the 2.4 gate, or `manual_review_scene_ids`.
- Editorial prompt: state explicitly that word counts and duration are pre-computed and supplied
  (pass the deterministic per-scene and total counts in the prompt) and must not be re-assessed.
- Tests: the exact s0001448Z false positive (claim "1,219 words" vs actual 1 536) is discarded
  and status no longer degrades; a genuine deterministic shortfall still produces its own
  finding; an age contradiction claim that matches the bible is kept, one that doesn't is
  discarded; old reports without `discarded_editorial_findings` still load.

---

## 2. Bounded targeted semantic revision

Add one optional revision pass after the editorial review, mirroring the existing
`_expand_underlength_scenes` design:

- Triggered only by remaining (non-discarded) `major` or `blocking` findings with
  `certainty` `confirmed` or `probable` and issue types in a configurable set:
  `lecture`, `continuity`, `unintroduced_character`, `character_consistency`, `agency`,
  `causality`, `repetition`, `style` (normalize reviewer spellings; map unknown types to
  "not eligible").
- Rewrites **only** the cited scenes (plus read-only neighbours as context), in one call per
  batch (`NARRATIVE_SEMANTIC_REVISION_MAX_CALLS`, default 1; batch size 3), with the findings,
  recommendations, identity rules, Character Bible and each scene's word allocation. Each
  revised scene must stay within its 85–115 % budget; unaffected scenes stay byte-for-byte.
- After revision: re-run deterministic review and **one** editorial re-review
  (`NARRATIVE_SEMANTIC_REREVIEW`, default true). Never loop further.
- Record everything in `narrative_review.json` under `semantic_revisions`: findings addressed,
  scene ids, before/after word counts, and the re-review outcome. Keep the pre-revision script
  as `script.pre_revision.json` in the run directory.
- Config switch `NARRATIVE_SEMANTIC_REVISION_ENABLED` (default true); document call cost
  (+1 revision, +1 re-review in the worst case).
- Tests (mocked): eligible lecture finding → only that scene changes; out-of-budget revision is
  rejected and the original kept with a recorded failure; discarded findings never trigger
  revision; max-calls respected; disabled switch → no calls.

---

## 3. Style rule: no stacked modifiers

- Add to the identity craft rules and the scene-writer/expansion/revision prompts: *avoid
  stacking two or more adjectives or an adverb + adjective on one noun for emphasis ("pure,
  unmitigated estrangement", "devastating, unhurried calm"); prefer one precise word or a
  concrete action; never write intensifier pairs.*
- Deterministic lint (warning-level, `minor`): count occurrences of
  `\b(\w+ly\s+)?\w+,\s+\w+\s+(\w+)` style stacked-modifier patterns and of a small intensifier
  list (`utterly, unmitigated, unadulterated, absolute, sheer, devastating, profound,
  immaculate, impeccable`). Report counts per scene in the deterministic report; above a
  configurable threshold (default 3 per episode) emit a `minor` `style` finding listing
  examples. Never blocking. Keep false positives low; test against the three examples above
  and against plain sentences that must not trigger.
- Pass the lint counts to the editorial reviewer as context for `literary_style`.

---

## 4. Documentation (pending from 2.1.1–2.4)

Update `README.md` and `SETUP.md` to cover: Gemini timeouts/thinking/temperature settings;
`--resume`; debug folder; channel identity and title policy; `psychological_angle`;
publication ledger, sync schedule, `scripts.sync_publications`, `scripts.register_publication`,
the `youtube.readonly` scope and re-consent; the pre-publication approval gate and override;
the new semantic revision pass and its cost; `scripts.promote_benchmark` (section 5). Keep it
concise. Also commit `docs/prompts/` (currently untracked) so the phase prompts are versioned.

---

## 5. Promote the benchmark to a production run, with two editorial edits

The GUI and `pipeline.py` work in `output/`. `output/` currently holds the **preserved v1
benchmark** (*The Architecture of Envy*), which must not be lost.

### 5.1 `scripts/promote_benchmark.py`

```
python -m scripts.promote_benchmark --source output/benchmarks/<dir> [--edits <file.json>] [--rerun-editorial --confirm-live]
```

- Before writing anything, archive the current contents of `output/` (all JSON artifacts and
  `images/`, excluding `benchmarks/`) into `output/benchmarks/archive-<timestamp>/` unless that
  exact content is already archived (compare hashes). Refuse to continue if archiving fails.
- Copy the benchmark artifacts into `output/`: `story_brief.json` (also as `research.json`,
  matching what the GUI/pipeline expect — verify the expected shape in `gui.py`/`pipeline.py`),
  `character_bible.json`, `story_outline.json`, `script.json`, `narrative_review.json`.
- Apply edits from a JSON file: list of `{scene_id, find, replace}`; each `find` must occur
  **exactly once** in that scene's `narration`, otherwise abort with a clear error and change
  nothing. Store the applied edits and their author (`human_editorial`) in
  `script.json["editorial_edits"]` and in `narrative_review.json`.
- After edits: re-run the deterministic review offline and re-merge with the existing
  editorial assessment, marking findings whose scenes were edited as
  `addressed_by_human_edit` (not removed). Invalidate downstream artifacts (narration, images
  timeline, video, thumbnail, any approval) using the existing invalidation helpers.
- `--rerun-editorial --confirm-live` performs one live editorial review (user runs it; tests mock).
- Tests with a temp directory: archive created once; ambiguous `find` aborts atomically;
  edits applied and recorded; downstream invalidated; v1 artifacts recoverable from the archive.

### 5.2 Edits file: `docs/prompts/edits/envy-2026-10-21.json`

Create this file with exactly these edits (verify each `find` string matches the benchmark
text exactly, including punctuation and curly/straight quotes; if a mismatch is purely
typographic, adapt the `find` string, never the meaning):

**Edit 1 — s01: establish the other guests and Marcus as the target.**

- find:
  `Yet as he aligned the smoked decanters two hours before their fifth anniversary dinner, he could not look at Elena without calculating how she would appear to Marcus Croft.`
- replace:
  `Yet as he aligned the smoked decanters two hours before their fifth anniversary dinner—eight guests, place cards written in his own hand, Marcus Croft seated directly across from Elena—he could not look at his wife without calculating how she would appear to the one man whose envy he wanted most.`

**Edit 2 — s08: replace the academic citation with a dramatized realization.**

- find:
  `René Girard wrote that human desire is mimetic—we rarely want what we possess; we only want what another person covets. Julian had never truly loved the loft, the bespoke joinery, or even his marriage for what they were. He had loved them because Marcus Croft looked at them with an aching greed.`
- replace:
  `Julian looked past him at the bespoke joinery, the framed ferns, the empty chair where Elena had sat, and understood that none of it had ever been his. Its value had always lived in another man's eyes. He had not wanted the loft, or the table, or even the marriage. He had wanted Marcus to want them.`

**Edit 3 — s07 (minor continuity; include, and list it separately in the report):**

- find: `from her cardigan pocket`
- replace: `from the pocket of her trousers`

---

## 6. Offline render-readiness check (no TTS, no downloads, no render)

The next session will run the first complete audiovisual render with **Edge TTS** (voice
`en-US-AndrewNeural`, current config) and **Pixabay/Openverse** stock only; ComfyUI comes later.
Before that, add `python -m scripts.render_preflight` that reads `output/script.json` and
reports, without network access:

- estimated narration duration per scene and total (configured WPM and `VOICE_RATE`);
- image slots needed per scene given the renderer's current pacing, versus image queries
  available (3 per scene today) — flag scenes that will reuse the same image for long stretches
  (configurable threshold, default > 20 s on one image);
- presence and versions of `moviepy` (must be 1.0.3), `numpy < 2`, `ffmpeg` on PATH, fonts,
  music assets, free disk space in `output/`;
- whether `edge-tts` is importable and the configured voice is in the known word-timing list;
- whether stock API keys are configured (presence only, never values).

Print a clear PASS/WARN/FAIL table and exit non-zero on FAIL. Tests mock the environment.
Do **not** change renderer pacing in this phase; just report.

---

## 7. Checks, commits, report

After each section: full offline suite, `py_compile` of changed modules, `git diff --check`.
One commit per section. Final report in Portuguese: changes per section, deviations and why,
test count before/after, and the exact commands for the user, in order:

```powershell
python -m scripts.promote_benchmark --source output/benchmarks/private-gravity-20261021T001448Z --edits docs/prompts/edits/envy-2026-10-21.json --rerun-editorial --confirm-live
python -m scripts.render_preflight
python gui.py   # run steps: images → narration → music → video, then review
```

Do not merge into `main` and do not push; the user will review first.
