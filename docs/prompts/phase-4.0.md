# Private Gravity — Phase 4.0

**Discovery: localized metadata, a packaging-swap loop, retention learning, fixed publishing slots, Community drafts, Shorts for other platforms, a post-upload checklist**

> Implementation prompt for Claude Code. Branch `feat/phase-4.0` from `main`. Report in **Portuguese**.
> No live calls in tests (mock YouTube Data/Analytics APIs, Gemini). Never read `.env`/tokens. Never push.
> One commit per section. Stay inside YouTube's policies: no fake engagement, no misleading metadata, no
> keyword stuffing, no mass link posting.

## 0. Why now (decisions of 2026-11-12)

Production quality is good. The effort now goes into **delivery, SEO and discovery**. Facts that shape
this phase (sources in the 2026-11-12 research):

- **The YPP entry bar doubles on 2027-02-01:** 1,000 subscribers plus **8,000** public watch hours in 12
  months (or 20M Shorts views in 90 days). Existing partners are grandfathered. **Long-form watch time** is
  now the main metric.
- **New channels are recommended largely by topic matching**, since there is no viewer history (YouTube,
  September 2026). A clear topic in the title, description and chapters matters.
- **Test & Compare** decides by **watch time per impression**, not CTR.
- **Auto-dubbing** has been open to all creators since February 2026, in 27 languages, and works best on
  narration.
- **Shorts** recommendations favour original content (October 2026). Our cuts with hooks qualify.
- **Hype** gives small channels a viewer-driven route.
- The **analytics cache is empty** (`data/analytics/` doesn't exist). The Desempenho panel has never been
  refreshed, so every decision in this phase must be able to run on real data once it arrives.

## A. Topic clarity in every package

- The title scoring (3.6-B) gains a **topic criterion**: the title must contain **one searchable concept**
  a viewer might look for ("thought experiment", "parable", "AI clone", "family vlogger", "mirror"), plus
  the curiosity hook. Keep the existing validator (no thesis titles).
- **Description template:**
  - the first line states the premise **with the topic words** (no spoilers);
  - the **series line**, by type: "A Private Gravity philosophical thought experiment." /
    "A Private Gravity dark parable." / "Private Gravity · realistic psychological fiction.";
  - then the existing hook, chapters, CTA and credit.
- **Chapter titles** keep their 3.9.7 rules: chapter 0 neutral, no spoilers. At least one chapter title
  carries the topic word.
- **Tags:** at most 8 relevant ones, generated from the topic plus the type; never other channels' names.
  **Hashtags:** 2–3, including the type tag (`#thoughtexperiment` / `#parable` / `#psychologicalfiction`).
- Validator: topic words present in title or first line; no repeated keyword more than twice in the
  description (anti-stuffing).
- Tests: topic criterion scoring, template lines per type, the anti-stuffing check.

## B. Localized titles and descriptions (+ auto-dubbing reminder)

- New config `LOCALIZE_LANGUAGES = ["es", "pt", "fr", "de", "hi"]`, editable in Settings.
- **At upload:**
  - Gemini translates the **title and the description** into each language at low temperature, with a
    contract-first JSON;
  - keep the meaning and the hook, never add claims, keep the chapters' timestamps intact, and keep
    `#Shorts` for Shorts;
  - **validate** length limits (title ≤ 100), the timestamps unchanged, and no English left untranslated
    except proper names;
  - upload via `snippet.defaultLanguage="en"` plus the `localizations` object (videos.insert/update;
    `part=snippet,localizations`).
- GUI: a "Traduções" panel in the review gate, editable per language. **Portuguese is shown first**, so
  the user can review it.
- `python -m scripts.youtube_backfill --video-id <id> --localize` adds translations to published videos.
  Run it on the 4 long-form videos and their Shorts (with approval).
- The post-upload summary reminds the user to **enable auto-dubbing** in Studio → Subtitles (the API
  can't) and to review the Portuguese track.
- Tests: translation contract validation (timestamps preserved, length limits), the localizations
  payload, the backfill (mocked).

## C. Packaging-swap loop (with confirmation)

- `analytics.refresh_due_safely()` already exists. Make sure the publication sync triggers it, and show
  the last refresh time and errors in Desempenho. Start by fixing whatever left `data/analytics/` empty.
- **Rule per long-form video**, configurable:
  - at **72 h**, if impressions ≥ 300 and CTR is below the channel median (or below 3 % when there is
    no median yet), propose a **swap**;
  - first the next thumbnail variant from the 3.8 gallery, keeping the title;
  - after another 72 h, if it is still low, propose the next title from the scored list.
  - A **swap is never automatic**: the GUI shows "Trocar miniatura?" with before/after and the numbers;
    one click applies it via `thumbnails.set` / `videos.update`.
  - Log every swap with timestamps in the ledger, so before/after can be compared.
- When the channel has Test & Compare access, suggest using it in Studio with the 3 combinations,
  exported ready to upload. Don't try to call it via the API.
- Tests: the rule thresholds, the median computation, the ledger log, the update payloads (mocked).

## D. Retention learning loop

- Fetch the **audience retention curve** per long-form video from the YouTube Analytics API
  (`audienceWatchRatio` / `relativeRetentionPerformance` by `elapsedVideoTimeRatio`), cached with the
  rest.
- Map drops to the **script**: find the biggest drops (slope) and the time they happen; map each time to
  the scene/section (`scene_timeline.json`) and the sentence (`narration_words.json`); compare with
  section types (cold open, hook, setup…).
- Produce a short "Lições de retenção" report per video ("queda de 18 % aos 0:42, no fim do cold open;
  a volta no tempo demorou 2 frases").
- Keep a **channel-level digest** (`data/analytics/retention_lessons.json`, the last 10 videos). Feed its
  3 most consistent lessons into the outline and screenplay prompts as **soft guidance** ("viewers leave
  when the return line takes more than one sentence"), never as hard rules, and show them in the GUI.
- Tests: drop detection on a synthetic curve, time → scene/sentence mapping, digest aggregation, prompt
  injection limited to 3 lessons.

## E. Session building: end screens, cards, related video, pinned comment

The API can't set end screens, cards or the Shorts "related video". Make them a **checklist the user
can't miss**:
- After upload, the GUI shows a "Depois do envio" checklist per video, with ready-to-copy values:
  - **end screen:** "next episode of the same type" (the video ID and title) plus subscribe;
  - **card** at the curiosity peak (default: the reversal's start time);
  - for each Short, the **related video** = its episode;
  - the **pinned comment** text (exists);
  - **auto-dubbing**;
  - the playlist (added automatically via `playlistItems.insert` to the type's playlist; ask the user
    for the 3 playlist IDs once, in Settings).
- The checklist items are tickable and stored in the ledger. The Desempenho panel shows unticked items
  for videos older than 24 h.
- The end card text (rendered in the video, 3.9-D) adds the **Hype** CTA: "If this stayed with you, hype
  it and share it." It never asks for likes or subs in exchange for anything.
- Tests: playlist insertion (mocked), checklist persistence, end-card text.

## F. Fixed publishing slots

- Config `PUBLISH_SLOTS = {"realistic_psychological_fiction": "TUE 18:00", "philosophical_thought_experiment":
  "THU 18:00", "dark_human_parable": "SAT 18:00"}` in **America/New_York** (the main audience is in the
  US), editable in Settings.
- The upload dialog proposes "**Agendar para o próximo horário do tipo**" (upload private plus `publishAt`)
  as the default. "Publicar agora" stays available.
- Shorts follow `SHORTS_SCHEDULE`, starting at the episode's slot and spaced through the week (several
  per day allowed, per the user). Never two Shorts within 3 h of each other.
- Tests: next-slot computation across week boundaries and DST, Shorts spacing.

## G. Community drafts

- For each episode, generate **drafts** (never posted automatically; the Community API isn't available):
  - a **poll** from the episode's dilemma (Type B: "What would you do?"; Type A: "Who was right?"; Type C:
    "Which part of this village are you?"), with 2–4 options;
  - a short **teaser post** for the day before the episode, with the thumbnail and one line, no spoiler.
- Show them in the checklist (E), ready to copy.

## H. Shorts for other platforms

- A "Exportar para TikTok / Reels" button per Short: the same 9:16 file (already safe-area aware) plus a
  `.txt` with a caption per platform (hook line, 3–5 hashtags, "Full story on YouTube: Private Gravity").
  Write them to `exports/social/<slug>/`.
- No automatic posting.
- Tests: export package contents.

## I. Report and commands

Report:
- why `data/analytics/` was empty and the first real refresh;
- the translations generated for one episode;
- the retention lessons for the published videos, if the data is available;
- the next slots;
- the checklist for the latest episode.

```powershell
python -m scripts.youtube_backfill --video-id <id> --localize      # each published long-form + its Shorts
python gui.py   # Desempenho (atualizar), Traduções no envio, "Depois do envio", agendamento por tipo
```
