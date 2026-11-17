# Private Gravity — Phase 4.3

**Shorts written as Shorts: own scripts (20–45 s), five proven formats, a hook in the first second, a fast cut, loops, a clear call to action, a swipe-first QA, and learning which formats work**

> Implementation prompt for Claude Code. Branch `feat/phase-4.3` from `main`. Report in **Portuguese**.
> No live calls in tests (mock Gemini, TTS, ComfyUI, YouTube). Never read `.env`/tokens. Never push. One
> commit per section. Reuse 4.1 (attention), 4.2 (rhythm, key lines, SFX, music) and 3.9.5 (TTS per
> sentence) instead of duplicating them.

## 0. Evidence and decision

- Today's Shorts are **cuts of the episode narration**: 45–75 s (`SHORTS_MIN_SECONDS=45`,
  `SHORTS_MAX_SECONDS=75`), the calm episode delivery, a first second that depends on context.
- **YouTube Analytics** (2026-11-16): average viewed 31–80 % per Short. Studio shows **40 % "continued
  watching" vs 60 % swiped**. Most Shorts stall at 0–20 views; the two that took off (136 and 195 views)
  opened on a strong, self-explanatory moment.
- **Decision (user):** the Shorts engine is reworked as its own product: **impactful Shorts related to the
  episode, strong hooks, short duration, a clear call to action**. Several per day are fine. Shorts
  recommendations favour **original** content (October 2026). A new script plus a new edit qualifies;
  re-cut narration is weaker.

## A. Five Short formats (all derived from the episode, each with its own script)

Config `SHORTS_FORMATS` (enabled list and weights) and `SHORTS_PER_EPISODE` (default **4**, maximum 6).

| Format | Length | What it is | How it ends |
|---|---|---|---|
| **1. Hook** | 20–35 s | The episode's most dramatic moment (usually the cold open), rewritten tighter. | On the unanswered question. |
| **2. Dilemma** | 20–35 s | States the rule or choice in 2 lines and asks the viewer: "What would you do?" Best for Type B and C; Type A uses "Who was right?". | The question on screen plus a comment prompt. |
| **3. The Line** | 12–25 s | One quotable line or moment with big on-screen text, built to **loop**: the last words flow back into the first. | Loop. |
| **4. Countdown to the turn** | 30–45 s | Rising tension toward the reversal. | **Stops one beat before the payoff** (the existing twist rule). |
| **5. POV** | 15–30 s | Second-person framing of the premise: "POV: everyone in your town can hear your thoughts." Present tense, immersive. Never claims the viewer did something real. | A sting and the CTA. |

**Rules for every format:**
- Must be **true to the episode**: no event, line or outcome that doesn't happen in it.
- Must never reveal the final twist or the ending.

## B. Script generation and its gate

- **One Gemini call per episode** (contract-first JSON) proposes `SHORTS_PER_EPISODE` scripts across the
  enabled formats, each with:
  - `format`, `hook_line` (≤ 10 words), `script` (40–110 words), `on_screen_hook` (≤ 5 words, shown in the
    first 1.5 s), `cta_text`, `title` (≤ 50 characters plus `#Shorts`), `description_line`;
  - `source_scene_ids` and `evidence_quotes` from the screenplay (validated to exist).
- **Deterministic checks:**
  - the **first spoken words are the hook**: no "In a town…", "Once…", or scene-setting before the
    hook;
  - sentences ≤ 12 words on average, none over 18;
  - duration estimate inside the format range (wpm of the Shorts voice, below);
  - the CTA is present;
  - no twist reveal (reuse `video.shorts` twist logic and the cold-open `withheld`);
  - the title passes the title validator and doesn't repeat the on-screen hook.
- **Swipe simulation** (4.1-D adapted): the "swiper" persona reads each script in **3-second blocks** and
  answers `swipe? yes/no + reason`. The gate requires:
  - no swipe in the first 3 s;
  - at most one block with "swipe" before the end;
  - for The Line, the loop judged natural.
  
  One targeted rewrite round on failure, then human review with the reasons.

## C. Voice and audio for Shorts

- The **same channel voice** (Kokoro Lewis), synthesized per sentence (3.9.5), at a **faster Shorts
  speed** (`SHORTS_TTS_SPEED`, default 0.95, versus 0.88 for episodes), with short pauses (sentence
  0.15 s, no scene pauses).
- **Audio from the first frame:** a sound accent at 0.0 s (impact or whoosh from the 4.2 SFX library),
  then the hook.
- Music at higher energy than the episode (the music index: Dramatic first), ducked under the voice; a
  sting under the CTA.
- **Heard-thought** treatment and **pronunciations** as in the episode.
- Loudness normalised to −14 LUFS (existing).

## D. Visual edit (1080×1920)

- **First frame:** the most striking image of the Short's source scenes (highest detector salience, or a
  face with emotion), already moving, with `on_screen_hook` in large brand type in the upper third. Never
  a black or empty first frame.
- **Rhythm:**
  - 1–2.5 s per view, using the 4.2 derived views reframed to 9:16 (subject-centred crop, blur-fill
    fallback, existing);
  - a hard maximum of 3 s without a visual change.
- **Captions:** big, centred, 1–3 words at a time, word-by-word highlight synced to word timings, inside
  the Shorts safe area (existing margins); key words in Antique Gold.
- **CTA end card** (last 1.5–2 s): the episode's thumbnail image blurred behind the text "Full story on
  the channel ↑" (the related-video link appears above the title in the Shorts player). Use **no fake
  "Part 2"**.
- **The Line format:** the last 0.5 s visually matches the first frame so the loop is seamless.
- Keep the film look and the photosensitivity guard.

## E. Packaging, CTA and scheduling

- **Title:** a curiosity hook naming the concept (4.0-A topic rule), ≤ 50 characters, `#Shorts` at the
  end.
- **Description:** the hook line; "Full story: <episode URL>"; the type hashtag plus 1–2 topical tags.
- **Pinned-comment draft** per Short: the Dilemma's question, or "The full story is linked above".
- The post-upload checklist (4.0-E) keeps **"related video = the episode"** at the top for every Short.
- **Scheduling (`SHORTS_SCHEDULE`):**
  - the first Short with the episode, the rest spread across the week, several per day allowed;
  - never within 3 h of each other (existing);
  - the format order alternates (Hook first, then Dilemma, The Line, Countdown, POV).

## F. Learning which formats work

- Each Short records its `format`. The analytics refresh aggregates by format: views, average viewed %,
  average view duration, subscribers, and clicks to the episode where available (traffic source "Shorts"
  on the episode).
- A "Formatos de Shorts" panel in Desempenho ranks formats over the last 30 days.
  - After ≥ 3 Shorts per format, suggest new weights for `SHORTS_FORMATS` (more of what works, never
    below 1 trial per 2 episodes for any format).
  - The user applies them with one click.

## G. GUI: a Shorts studio

- In the project, the Shorts stage shows the proposed scripts as cards: format, hook, script, the
  simulation verdict, estimated duration.
- Per card: **edit the text**, "Gerar outra versão", play the TTS preview, render, then the 9:16 player.
- Approval and upload use the existing gate (each Short has its own card and hashes).
- Episode cut-downs (the old mode) remain as an option ("Recortes do episódio"), off by default. The
  standalone "Short original (história própria)" mode remains.

## H. Backfill for published episodes

`python -m scripts.shorts_backfill --project <slug> [--formats hook,dilemma,line]` creates new-format
Shorts for already published episodes:
- it restores only the needed files from the archive (script, narration words, shots, references);
- it renders and adds the Shorts to the approval gate with the episode URL from the ledger.

The 6 published episodes are a ready test bed for the formats.

## I. Tests

- The format contracts and ranges.
- The hook-first rule (it rejects scene-setting openers).
- Evidence quotes exist.
- The twist guard per format (Countdown stops before the payoff).
- The CTA present.
- The swipe-gate logic.
- TTS speed and pauses for Shorts.
- First-frame selection, never black.
- The view-duration cap.
- Captions in the safe area.
- The loop match for The Line.
- The CTA card text.
- Scheduling with alternating formats and the 3 h gap.
- Per-format analytics aggregation and the weight suggestion.
- The backfill restoring only the needed files (mocked archive).

## J. Report and commands

Report:
- the scripts proposed for the current parable, with gate results;
- one rendered example per format (paths);
- the backfill plan for the 6 published episodes;
- the per-format baseline from the existing Shorts (all of them "recorte", as the reference).

```powershell
python gui.py   # Projeto → Shorts: roteiros por formato, simulação de deslize, prévia, renderizar, aprovar
python -m scripts.shorts_backfill --project 2026-11-03-everyone-heard-what-he-thought --formats hook,dilemma,line
```
