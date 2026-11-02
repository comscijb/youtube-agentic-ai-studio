# Private Gravity — Phase 2.2.2

**Overload resilience · Proportionate word-budget severity · Editorial re-review command**

> Implementation prompt for Claude Code. Work on branch `feat/phase-2.2.1` (continue it).
> Report in **Portuguese**. No live API calls in tests. Never read `.env` or token files. Never push.

---

## 0. Evidence

1. `python -m scripts.promote_benchmark ... --rerun-editorial --confirm-live` failed:
   `gemini-3.6-flash` → `HTTP 503 "This model is currently experiencing high demand"` after
   2.9 s; fallback `gemini-3.8-flash` → same 503 after 2.4 s; run aborted. The SDK retry
   (`HttpRetryOptions(attempts=2, max_delay=2.0)`) plus one transient fallback gives up after
   ~5 s, while Gemini overload spikes typically last minutes. (Promotion correctly wrote
   nothing, because the review runs before any write.)
2. Offline promotion (without `--rerun-editorial`) returned `revision_required` only because the
   human edit made scene s01 165/140 words (117.9 %), a `major` `scene_word_budget` finding —
   while the screenplay total was 104 % of target. The edit was shortened (now 159/140, status
   `ready_for_human_review`), but the policy is disproportionate: a scene that is slightly
   **over** budget, inside a total that is within range, is a pacing note, not a revision trigger.
3. `docs/prompts/` is still untracked although Phase 2.2.1 §4 asked to commit it.

---

## 1. Overload and rate-limit resilience (`agents/gemini_client.py`)

- Classify `503` / `UNAVAILABLE` / "high demand" as `overloaded`, and `429` as `rate_limited`
  (respect `Retry-After` or the RetryInfo delay when present). Keep `deadline_exceeded` and
  other `temporary_server` handling as is.
- For `overloaded`: application-level retries on the **same model** with exponential backoff and
  jitter, configurable: `GEMINI_OVERLOAD_RETRIES` (default 4), `GEMINI_OVERLOAD_BASE_DELAY`
  (default 10 s), `GEMINI_OVERLOAD_MAX_DELAY` (default 90 s) — roughly 10/20/40/80 s. Then move
  to the fallback model and give it the same budget. Remove `503` from the SDK-level
  `http_status_codes` so the two retry layers don't compound unpredictably.
- Print each wait clearly (`waiting 20s before retry 2/4 (model overloaded)`), and log it in the
  per-call `[gemini]` line. Honour Ctrl+C during waits.
- Cap the total wall-clock per call with `GEMINI_MAX_CALL_WALLCLOCK` (default 600 s).
- Tests (mocked, `_sleep` patched): 503×2 then success on the same model; 503 exhausting the
  budget then fallback success; 429 with Retry-After honoured; wall-clock cap enforced.

## 2. Proportionate word-budget severity (`agents/narrative.py`)

- Under-target scenes outside 85 % stay `major` (unchanged: underdevelopment matters).
- Over-target scenes: `minor` when the screenplay total is within 90–110 %; `major` only when
  the scene exceeds 130 % or the total is out of range. Document this in README.
- Tests: s01 at 117.9 % with total 104 % → `minor`, status unaffected; scene at 135 % → `major`;
  under-target behaviour unchanged.

## 3. Editorial re-review without re-promoting

`python -m scripts.rereview_output --confirm-live` runs one live editorial review of the current
`output/` artifacts (honouring the 2.2.1 screening of findings), clears `editorial_review_stale`,
updates `narrative_review.json` atomically, and invalidates any existing `review_approval.json`.
On failure, leave every file untouched and print the retry command. Tests mock the reviewer.

## 4. Housekeeping

Commit `docs/prompts/` (including `edits/envy-2026-10-21.json`, whose Edit 1 was shortened by
the user's session to "eight guests, with Marcus Croft seated directly across from Elena").
Leave the user's pending `.gitignore` newline change alone unless it blocks `git diff --check`.

## 5. Checks and report

Full offline suite, `py_compile`, `git diff --check`; one commit per section; report in
Portuguese with test counts and the user's next commands.
