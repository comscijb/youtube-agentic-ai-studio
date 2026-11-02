# Private Gravity — Phases 2.1.1 → 2.4

**Generation reliability · Channel identity · Published-topic ledger · Pre-publication review gate**

> Implementation prompt for Claude Code. Read this whole document before changing anything.
> Work in the repository root (`youtube-agentic-ai-studio`). Report to the user in **Portuguese**.

---

## 0. Context

Private Gravity is an English-language YouTube anthology of original cinematic psychological
fiction. This fork of `youtube-agentic-ai-studio` already has:

- Phase 1: Pixabay (primary) + Openverse (fallback) stock providers, cache, license log.
- Phase 2: narrative pipeline — Story Brief → Character Bible → Story Outline → scene batches →
  deterministic review + editorial review (`agents/researcher.py`, `agents/scriptwriter.py`,
  `agents/narrative.py`).
- Phase 2.1 (commit `c7d0cb6`): narrative contract v3, canonical `section_type` separated from
  free-text `narrative_function`, per-scene word budgets (85–115 %), targeted expansion,
  15-dimension evidence-based editorial review, blocking/major findings, opt-in benchmark script
  `scripts/regenerate_narrative_benchmark.py`, regression fixture
  `tests/fixtures/architecture_of_envy_benchmark.json`.

Offline suite currently: 79 tests, all passing (one requires `moviepy`).

The first live run of the Phase 2.1 benchmark **failed at the outline stage**:

```
Gemini generation failed: model=gemini-3.8-flash; category=temporary_server; HTTP=504;
fallback_attempted=yes; attempted_models=gemini-3.6-flash, gemini-3.8-flash.
Deadline expired before operation could complete.
```

`gemini-3.6-flash` (the configured model) failed with `temporary_server` on **every** call
(brief, bible, outline); the brief and bible only succeeded through the 3.8 fallback.

---

## 1. Ground rules

1. Create and work on branch `feat/phase-2.1.1-2.4`. Never push. Commit per phase with clear
   messages. Ask before any destructive git operation.
2. **No live API calls** (Gemini, YouTube, Pixabay, Openverse, Edge TTS) in tests or during
   implementation. Mock every external client. The user runs live checks.
3. Never read, print, or modify `.env`, `client_secret.json`, or `youtube_token.pickle`
   contents. Never log API keys or OAuth tokens.
4. Keep existing artifacts readable: schema v2 and contract v3 files in `output/` and the
   fixture must still load. Bump versions only where the shape changes and keep readers
   backward-compatible.
5. Do not modify `output/` (it holds the preserved v1 benchmark) except via new isolated
   subdirectories created by code at runtime.
6. Verify SDK details against the **installed** `google-genai` and `google-api-python-client`
   versions (inspect the package source / signatures). Do not guess field names.
7. After each phase run:
   ```
   python -m unittest discover -s tests -p "test_*.py"
   python -m py_compile agents/*.py uploader/*.py gui.py pipeline.py scripts/*.py
   git diff --check
   ```
8. **Checkpoint:** after Phase 2.1.1 is committed and green, STOP and report. The user will run
   the live benchmark to measure Phase 2.1 before identity changes alter the output. Continue with
   2.2 → 2.4 only when the user says so.

---

## 2. Phase 2.1.1 — Generation reliability (blocker)

### 2.1 Root cause to fix: 30-second server deadline

`agents/gemini_client.py::_get_client()` builds the client with
`types.HttpOptions(timeout=30_000, retry_options=HttpRetryOptions(attempts=2, ...))`.
The SDK forwards this timeout to the server as the request deadline. Long-form JSON stages
(outline with 9–14 fully specified scenes, scene batches, editorial review with the full
screenplay in the prompt) on Gemini 3.x Flash models — which spend time thinking before
answering — routinely exceed 30 s, so the server answers `504 DEADLINE_EXCEEDED`. The SDK retry
repeats the same 30 s deadline, and the fallback model hits the same limit.

Required changes:

- Make the timeout configurable: `GEMINI_TIMEOUT_SECONDS` (default **300**) in `config.py`,
  overridable from `.env`. Support a per-call override so short calls (model discovery,
  diagnostics) keep a short timeout and long-form stages get the long one. Prefer per-request
  `http_options` on `GenerateContentConfig` if the installed SDK supports it; otherwise keep a
  small cache of clients keyed by timeout.
- Classify `504` / `DEADLINE_EXCEEDED` as a distinct category `deadline_exceeded` (still
  retryable). On that category, retry the **same model once** with a longer deadline (×2, capped
  at 600 s) and exponential backoff with jitter, then fall back. Do not burn SDK-level retries on
  504 with an unchanged deadline.
- Log per call: stage label, model, prompt size (characters), elapsed seconds, outcome. No
  prompt content in logs.
- Keep the existing user-facing error format and "Next action" guidance.

### 2.2 Structured-output configuration per stage

- `generate()` accepts optional `temperature`, `response_mime_type`, `max_output_tokens`, and
  `thinking` settings (whatever the installed SDK exposes for Gemini 3.x — e.g. a thinking level
  or budget; verify).
- For every JSON stage set `response_mime_type="application/json"`.
- Stage temperatures (configurable constants): premise development 0.9, character bible 0.7,
  outline 0.6, scene writing 0.85, targeted expansion 0.7, editorial review 0.2, any repair 0.2.
- Expose `GEMINI_THINKING_LEVEL` (or budget) in config with a safe default; document it.

### 2.3 Repair prompts must never send truncated contracts or truncated JSON

In `agents/scriptwriter.py::_generate_json`:

- Parse repair currently sends `prompt[:14000]`. The outline prompt places brief + bible
  (~16 000 chars) **before** the schema instructions, so the repair loses the contract entirely.
- Semantic repair sends `json.dumps(value, indent=2)[:18000]`. The v1 outline was already
  17 986 chars compact; indented it is far larger, so the model receives a cut-off document.

Fix:

- Split every stage prompt into `contract` (role + schema + rules) and `context` (brief, bible,
  outline, scenes). Build prompts as contract first, context second. Repairs always include the
  **full contract** and send JSON **compact** (`separators=(",", ":")`).
- Never slice JSON strings. If a repair payload would exceed a configurable budget
  (`GEMINI_REPAIR_MAX_CHARS`, default 120 000), repair at a finer granularity instead: for
  outlines and screenplays, send only the invalid scenes plus their immediate neighbours and
  merge the corrected scenes back by `scene_id`, preserving all valid scenes byte-for-byte.
- Validation error messages passed to the model must name scene IDs and fields.

### 2.4 Diagnostics and resumability

- On any stage failure (generation error, parse failure, failed repair), persist the raw model
  response(s) and the validation error to `<run_dir>/debug/<stage>-<attempt>.txt` (create the
  folder; add `debug/` patterns to `.gitignore` if needed). Never write keys.
- `scripts/regenerate_narrative_benchmark.py`: add `--resume <dir>` that reuses valid cached
  artifacts in an existing benchmark directory (the stages already support `source_hash`
  caching when `force=False`). A failed outline must not force regenerating brief and bible.
- Print a final per-stage timing table.

### 2.5 Repository hygiene

- Add `.gitattributes` so line endings stop polluting diffs (`* text=auto`, `*.py text eol=lf`,
  `*.md text eol=lf`, `*.json text eol=lf`, `*.html text eol=lf`, `*.txt text eol=lf`,
  `*.ps1 text eol=crlf`). `config.py` was recently saved with CRLF; renormalize it in its own
  commit so the only semantic change (`GEMINI_MODEL` default → `gemini-3.6-flash`) stays visible.

### 2.6 Tests (mocked)

- 504 on first call → same-model retry with a larger deadline → success.
- 504 twice → fallback model is tried; error message lists both models.
- Repair prompts contain the complete contract text and never a truncated JSON document
  (assert the embedded JSON parses).
- Oversized repair switches to scene-scoped repair and merges by `scene_id` without touching
  valid scenes.
- Debug files are written on failure and contain no key-like strings.
- `--resume` skips cached brief/bible.

**Commit, run the full check, STOP and report** (what changed, test count, the exact live
commands for the user):

```powershell
python -m scripts.regenerate_narrative_benchmark --confirm-live --minutes 12
# if it fails mid-way:
python -m scripts.regenerate_narrative_benchmark --confirm-live --minutes 12 --resume output/benchmarks/<dir>
```

---

## 3. Phase 2.2 — Channel identity adherence

### 3.1 Single source of truth

Create `agents/identity.py` (or `identity/private_gravity.json` + loader) holding the editorial
identity, injected into the researcher, outline, scene writer and editorial review prompts.
Replace scattered identity text where it duplicates this source. Content:

**Channel promise.** Original cinematic fiction in which psychological and philosophical ideas
emerge through character choice, consequence, image and subtext — never lectures, explainers,
listicles or self-help. Atmospheric, mature, accessible. Anthology of standalone stories about
the hidden forces that shape human behaviour.

**Episode types.**

- **Type A — Realistic Psychological Fiction** (`realistic_psychological_fiction`): detailed
  guidance below. *Guidance, not a rigid schema*: the outline should normally express these
  movements, but the model may merge, reorder or compress them when the story demands; missing
  movements produce review findings, not hard failures.
- **Type B — Philosophical Thought Experiment** (`philosophical_thought_experiment`): one
  speculative rule introduced early and applied consistently; the story tests the rule through a
  character's choices; consequences escalate logically; the philosophical question is felt
  through outcomes. Flexible structure.
- **Type C — Dark Human Parable** (`dark_human_parable`): compact, fable-like, timeless or
  stylised setting; a clear moral mechanism without stating a moral; an ironic or inverted
  ending. Flexible structure.

**Type A — the seven movements (guidance).**

1. **Narrative hook (≈ first 30 s / first 60–75 narrated words).** Open on a situation, not an
   explanation or atmosphere. Establish who, a striking fact, and an open question. Model:
   *"Daniel had spent twenty years trying to become the kind of man people noticed. At
   forty-two, he finally succeeded. That was when his life began to fall apart."*
2. **Character construction.** Show the desire *and* the wound behind it, through concrete
   past or present detail (e.g. a father treated as irrelevant → the belief that respect must
   be bought). The wound must appear in the screenplay, not only in the bible.
3. **The mechanism.** The character makes choices consistent with an inner belief they cannot
   see. The viewer should start recognising the pattern in themselves.
4. **Escalation — the strategy works first.** The status-seeker gains status; the workaholic
   earns; the avoider avoids pain. Early success makes the story better. The tragedy lives in
   the **hidden cost** of the strategy, revealed progressively through consequences.
   *(The Architecture of Envy failed here: the protagonist began already ruined.)*
5. **Reversal.** An event — ideally caused by the character's own choices, not a convenient
   discovery — forces confrontation with the governing belief. Usually the most shareable
   moment.
6. **Philosophical layer.** Only after the reversal, briefly, the meaning surfaces more
   explicitly. A thinker or concept (Jung, Kierkegaard, Nietzsche, Becker, Fromm, Girard, social
   psychology) may be evoked in one or two sentences when it genuinely fits — never a lecture,
   never the protagonist of the episode.
7. **Ending.** No moral of the story. Prefer partially open endings that leave an
   uncomfortable question. The protagonist should make a final choice or reach a recognition
   that reveals something new — not merely suffer the expected consequence.

**Craft rules (all types).** Dramatize with direct, specific dialogue and subtext; vary rhythm
between description, action, dialogue, plain observation and silence; avoid repeating the same
sensory vocabulary (the v1 benchmark overused *glass, heavy, stone, polished, silence,
shadow*); avoid ambiguous poetic phrasing that misleads a listener (v1: *"the glass shatters
with a silent pulse"* when only a phone vibrated); supporting characters need independent
agency and contradictions; ages, relationships and objects must stay consistent with the bible.

### 3.2 Outline guidance field (Type A)

Add an optional per-scene field `arc_movements` (array; values: `hook`, `character_wound`,
`mechanism`, `escalation_success`, `hidden_cost`, `reversal`, `philosophical_layer`, `ending`).
Keep the canonical `section_type` unchanged. For Type A, the deterministic review emits a
**major** (not blocking) finding listing movements no scene declares. Types B/C: field optional,
no finding. Bump the narrative contract version; older artifacts without the field stay valid.

### 3.3 Title and thumbnail-text policy (enforced)

Principle: **the title must not explain the thesis.** It should present a person and a
situation that creates curiosity; the audience discovers the psychological theme while watching.

| Weak (reject) | Strong (target) |
|---|---|
| The Psychology of Envy: A Story About Social Comparison | He Secretly Wanted His Best Friend to Fail |
| The Psychology of People Pleasing | She Said Yes to Everyone for 20 Years |
| Why Success Doesn't Make You Happy | He Got Everything He Wanted at 37 |
| The Architecture of Envy *(v1)* | The Man Who Needed Everyone to Envy Him |
| The Poison of Being Envied *(2.1 run)* | — |

Thumbnail text pairs with the title, ≤ 5 words, often a line of inner voice: *"I hope he
loses."*, *"Now what?"*

Implementation:

- The Story Development Agent returns `title_candidates` (5) and `thumbnail_text_candidates`
  (3) in addition to `video_title`, plus a short rationale per title.
- New deterministic `validate_title(title, brief) -> list[finding]` in `agents/narrative.py`.
  Reject (severity **blocking** for publication) when the title:
  - contains thesis/explainer patterns (case-insensitive): `psychology of`, `science of`,
    `philosophy of`, `a story about`, `explained`, `the truth about`, `why `, `how `,
    `lesson`, `what happens when`; or a colon subtitle;
  - matches the abstract-noun form `^The <Noun> of <...>$` (e.g. *The Architecture of …,
    The Anatomy of …, The Poison of …, The Cost of …*);
  - names the psychological theme or its direct derivatives (from `psychological_theme`, e.g.
    *envy / envied / envious*), unless spoken inside a character's quoted line;
  - exceeds 70 characters or 12 words (ignore the `#Shorts` suffix).
  Warn (major) when the title has no concrete human anchor (a person/role noun or pronoun,
  an age, a number, a time span, or a quoted line).
- Selection: pick the first candidate that passes; if none passes, one bounded repair call
  asking only for new titles with the failures listed; if still none, status
  `revision_required` with the title finding. Store the chosen title, thumbnail text, all
  candidates and validation results in the brief and the review report.
- `thumbnail_text` validation: ≤ 5 words, no theme word, no hashtags.
- GUI title edits run the same validator and show findings inline before saving.

### 3.4 Editorial review additions

Extend `EDITORIAL_DIMENSIONS` with: `channel_identity_adherence`, `hook_strength`,
`title_curiosity`, `philosophical_integration`, `ending_openness`, `strategy_success_then_cost`
(Type A; scored "n/a"-equivalent guidance for B/C — choose a representation that keeps the
validator strict but meaningful). Update the validator (currently requires exactly 15 keys),
the prompt, the report merge and tests. Keep reading old reports. The reviewer must cite scene
evidence for each new dimension; a lecture-like passage is at least a **major** finding.

### 3.5 Tests

- Title validator: every weak example above rejected; every strong example accepted; theme word
  inside a quoted line accepted; `#Shorts` suffix ignored.
- Type A outline missing `escalation_success` → major finding; Type B/C → none.
- Identity text present in all four prompts (assert on built prompt strings with mocks).
- Editorial validator accepts the new dimension set and still reads the old 15-key reports.
- Fixture-based regression: the v1 benchmark title fails, the requested premise passes.

Commit.

---

## 4. Phase 2.3 — Published-topic ledger and automatic banned topics

### 4.1 Requirements from the user

- A video's topic enters `banned_topics.txt` **only after it is actually published** (public on
  YouTube), never at upload or generation time.
- Entries must be **specific**: the same theme may return from a different psychological angle;
  what must not repeat is the same theme + angle + premise.
- Uploads normally go through the pipeline/GUI; occasionally a manual upload (after a late
  failure) must be registrable. Sync must run frequently.

### 4.2 Brief field

Add `psychological_angle` to the Story Brief contract: one specific sentence naming the
mechanism explored (e.g. *"needing to be envied as a substitute for self-worth"*), distinct
from the broad `psychological_theme` (*"envy"*). Validate non-empty for new briefs; tolerate
absence in old ones.

### 4.3 Unify the upload path

`gui.py::/api/upload` and `uploader/youtube.py::upload_to_youtube` duplicate the upload logic.
Consolidate into one function in `uploader/youtube.py` (OAuth, insert, scheduled `publishAt`,
thumbnail, progress callback) returning the `video_id`. Both GUI and `pipeline.py` call it.
Preserve current behaviour: default privacy `public` (`config.VIDEO_PRIVACY`), scheduling
uploads as private with `publishAt`.

### 4.4 Publication ledger

`data/publications.json` (tracked in git; no secrets), atomic writes, schema-versioned. One
record per upload:

```
video_id, url, run_dir, uploaded_at, scheduled_publish_at, title, thumbnail_text,
premise (exact), logline, psychological_theme, psychological_angle, narrative_type,
central_contradiction, source_hashes {script, video}, state, state_checked_at, published_at
```

States: `uploaded_pending_publication` → `published` | `failed` | `removed`.
Record is written immediately after a successful upload (before any sync).

### 4.5 Sync

`uploader/publications.py::sync_publications()`:

- `videos.list(part="status,snippet", id=<pending ids, batches of 50>)`.
- **Published** when `status.privacyStatus == "public"` and `status.uploadStatus == "processed"`
  (for scheduled videos this naturally happens after `publishAt`). → append to
  `banned_topics.txt`, set `published`, `published_at`.
- `uploadStatus` `failed`/`rejected`/`deleted`, or the ID is no longer returned → `failed` /
  `removed`; never banned.
- Still private/unlisted/processing → unchanged.
- Idempotent: keyed by `video_id`; re-running never duplicates lines.

OAuth scope: verify in the YouTube Data API docs / client discovery whether `youtube.upload`
permits `videos.list` on the channel's own videos. If not, add
`https://www.googleapis.com/auth/youtube.readonly` to `YOUTUBE_SCOPES` and detect a stored token
lacking a required scope → trigger re-consent with a clear message. Never print tokens.

**When sync runs:** on GUI start; every `PUBLICATION_SYNC_INTERVAL_MINUTES` (default 30) in a
background thread while the GUI runs; at the start of `pipeline.py` and before automatic premise
generation; on demand via `python -m scripts.sync_publications`. Network/auth failures log a
warning and never block generation.

**Manual registration:** `python -m scripts.register_publication --video-id <id> --run-dir <dir>`
reads title/premise/theme/angle from the run's artifacts and creates a pending record (then
sync decides).

### 4.6 Banned-topics format

Keep the file human-editable and backward-compatible. Plain lines (current format) remain valid.
Automatic entries use one line per published video:

```
theme: envy | angle: needing to be envied as a substitute for self-worth | premise: The Man Who Needed Everyone to Envy Him | title: <title> | video: <id> | published: 2026-10-21
```

`load_banned_topics()` returns structured entries (plain lines → `premise` only). The researcher
prompt must state the rule explicitly: *do not reuse a listed premise or the same theme + angle
combination; the same broad theme from a clearly different psychological angle is allowed.*
In exact-premise mode, warn (do not block) when the premise matches a published entry.
The GUI banned-topics editor keeps working and shows structured entries readably.

### 4.7 Tests (mocked YouTube client)

Pending→public+processed bans once; re-sync no duplicates; private/unlisted/processing stays
pending; scheduled before `publishAt` stays pending; failed/rejected/missing → no ban; plain
legacy lines still load; researcher prompt contains the angle rule; manual registration reads
artifacts; sync failure does not raise into generation; missing-scope token triggers re-consent
path.

Commit.

---

## 5. Phase 2.4 — Pre-publication human review gate

Uploads stay **public by default** (the user wants early traction). Safety comes from a gate
before upload, not from private uploads.

- Approval record `<run_dir>/review_approval.json`: reviewer checklist with explicit booleans —
  watched the full video; read the narrative review report; title and thumbnail text pass the
  validator (or overridden with a written reason); stock license log has no flagged items (or
  reviewed); description and tags reviewed. Plus `approved_at`, optional notes.
- The approval is bound to hashes of `script.json`, the video file, title, description and
  thumbnail. Any change after approval invalidates it.
- Upload (GUI and pipeline) is refused unless a valid approval exists.
  - Narrative status `structurally_invalid` or `revision_required` → upload blocked; allow an
    explicit override only with a typed reason, recorded in the approval and the ledger.
  - `human_review_required` / `ready_for_human_review` → allowed after the checklist.
- GUI: a review panel with the checklist; the Upload button stays disabled until complete; a
  final confirmation states clearly *"This video will be PUBLIC immediately"* (or the scheduled
  time). `pipeline.py`'s Flask review dashboard writes the same approval record.
- Tests: no approval → refused; stale approval after editing title → refused; blocked status
  without override → refused; with override reason → allowed and recorded; valid approval →
  upload function called (mocked) and ledger record created.

Commit.

---

## 6. Final report (Portuguese)

For each phase: files changed, design decisions, deviations from this document and why, test
count before/after, known limitations, and the exact commands the user should run live:

1. Benchmark (if not already run after 2.1.1).
2. Benchmark again after 2.2 to measure identity adherence.
3. `python -m scripts.sync_publications` (will request re-consent if a scope was added).

Do not run live commands yourself. Do not push.
