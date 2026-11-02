# Private Gravity — Phase 3.7

**Detailed premise briefs · Shorts cut from each episode, uploaded with the long video**

> Implementation prompt for Claude Code. Branch `feat/phase-3.7` from the current head (`feat/phase-3.6`).
> Report in **Portuguese**. No live calls in tests (mock Gemini, ComfyUI, Edge TTS, YouTube). Never read
> `.env`/tokens. Never push. One commit per section.

## 0. Context

- Today the exact premise is a **single sentence** (`research_topic(topic_override=...)`, GUI field "exact
  premise", `--premise` in the benchmark script). Story quality depends on Gemini inventing everything else.
  The user wants to supply a **detailed description** (characters, setting, beats, tone, must-include and
  must-avoid) to steer the screenplay.
- Shorts exist only as a separate mode (`video_type="shorts"`: its own ~110-word story, fast slideshow in
  `video/creator.py`). Goal now: **cut 2–3 Shorts from every long episode** (same narration, same shots),
  review them in the same approval gate and upload them together with the long video. The first goal of the
  channel is the first 1 000 subscribers; Shorts are the main discovery engine for new channels.
- Next episodes planned: (1) "The Only One Who Never Got Tired of Him" — a lonely man and an AI companion;
  (2) "She Said Yes to Everyone for 20 Years" — a people-pleaser. Use them in examples/fixtures.

## A. Detailed premise brief

### A.1 Input

- Accept a **premise brief** as free text (up to ~4 000 characters) or a structured file:
  `--premise-file briefs/<name>.md` (Markdown with optional headings) or `.json`.
- Recognised optional sections/fields (all optional; free text still works):
  `working_title`, `narrative_type` (A/B/C ids), `logline`, `protagonist` (age, situation, wound, desire),
  `other_characters` (≤ 2 recommended — production works best with ≤ 3 characters and few locations),
  `setting` (places, time), `beats` (`hook`, `strategy_works`, `hidden_cost`, `reversal`,
  `philosophical_layer`, `ending`), `tone`, `must_include` (list), `must_avoid` (list),
  `target_minutes`, `title_ideas`, `thumbnail_idea`.
- GUI: replace the one-line "exact premise" field with a large textarea plus a **"Inserir modelo"** button
  that fills the template above (headings in English, since stories are in English), a character counter,
  and "Carregar arquivo". Keep a single sentence working exactly as before.
- Add `python -m scripts.project new --brief-file <file> [--minutes 12]` (new subcommand) that creates the
  project from a brief.
- Save the brief in the project (`premise_brief.md` + parsed `premise_brief.json`); it becomes part of the
  project artifacts, of the archive (`text\\`) and of the stage `source_hash` chain.

### A.2 Use in generation

- Story Development Agent: the brief is **authoritative**. Fields provided must be preserved; Gemini only
  fills what is missing and may improve wording, never replace decisions. The story brief records
  `requested_brief_hash` and a `brief_coverage` map (field → how it was used).
- Character Bible, outline and scene prompts receive the relevant parts (characters, beats, tone, must
  include/avoid) — not the whole raw text — to keep prompts focused.
- **Faithfulness check** (deterministic where possible + one Gemini pass inside the existing editorial
  review): every `must_include` item appears in the outline/screenplay; no `must_avoid` item appears;
  provided beats map to outline scenes (`arc_movements`); provided names/ages/relationships are unchanged.
  Violations are `major` findings with the evidence; the targeted semantic revision may fix them.
- Banned topics: compare the brief's theme + angle with published entries and warn (not block) on a match.
- Tests: free-text and structured briefs parsed; one-sentence premise unchanged behaviour; coverage map;
  must-include/avoid detection; names preserved; stage invalidation when the brief changes.

## B. Shorts cut from the episode

### B.1 Selection

- New stage `shorts_plan` after narration (needs real timings), Gemini contract-first JSON with
  deterministic validation. Select **2–3 segments** (config `SHORTS_PER_EPISODE`, default 3), each
  **45–75 s** (config; YouTube allows up to 3 min, but shorter retains better), cut on **sentence
  boundaries** from the word-timed captions, chosen for:
  - a hook in the first 2 s (a striking line or situation, not a scene-setting sentence);
  - self-contained clarity (understandable without the episode);
  - an ending on tension/cliffhanger — **never reveal the episode's final twist**; at most one Short may
    approach the reversal, ending before its payoff.
- Each segment gets: `title` (curiosity, ≤ 60 chars incl. `#Shorts`, passes the title validator),
  `hook_text` (≤ 6 words, on-screen for the first ~2 s), `end_text` ("Full story on the channel" or
  similar, ≤ 6 words), `description` (1–2 lines + the CTA block + link to the full episode).
- GUI review shows the proposed segments on a timeline with play-from/to controls; the user can adjust the
  in/out points (snapped to sentence boundaries), drop or add a segment, and edit texts.

### B.2 Rendering (1080×1920)

- Reuse the episode's narration audio slice (exact), music under it with the same ducking, and normalise
  each Short to −14 LUFS / −1.5 dBTP.
- Visuals: the shots covering the segment, **reframed to 9:16**:
  - crop window centred on the detected subject(s) (layer-1 person/face detector boxes) with a slow pan
    following the shot's Ken Burns direction; if the subject doesn't fit, use a **blurred, darkened copy of
    the same image as background** with the 16:9 image centred (no letterbox bars);
  - shots shorter than ~2.5 s are not split further; long holds get a slow push-in.
- Captions: Shorts style — large, centred in the lower-middle third, 2–4 words per cue, word-timed,
  safe from the YouTube UI (avoid the bottom ~20 % and right edge ~15 %).
- `hook_text` overlay in the first ~2 s (top third, brand typography); `end_text` card in the last ~2 s.
- Same tape effect setting as the episode (before captions), same photosensitivity guard.
- Output: `shorts/short_01.mp4`, … plus `shorts/shorts_plan.json`; included in preflight estimates.

### B.3 Approval, upload and publication

- The pre-publication approval gate covers the episode **and** its Shorts: each Short has its own card
  (play, texts, include/exclude checkbox) and its hashes are part of the approval.
- Upload order: long video first (get its `video_id`/URL), then each Short with the episode URL in its
  description and `#Shorts` in the title; same language fields (`en`), same captions upload (sliced SRT).
- **Publication schedule** for Shorts (config `SHORTS_SCHEDULE`): default first Short public right after
  the episode, the others spaced 24 h apart via `publishAt` (uploaded as private + scheduled). Show the
  planned times in the confirmation dialog ("o episódio será PÚBLICO agora; Shorts: agora, amanhã 18:00,
  depois de amanhã 18:00").
- The "related video" link for Shorts must be set manually in YouTube Studio (not available via the API):
  remind the user in the post-upload summary, with the episode URL ready to copy.
- Ledger: Shorts recorded with `parent_video_id`; banned topics are written only from the parent episode;
  the analytics panel lists Shorts under their episode; the archive gets a `shorts\\` folder (videos,
  plan, captions); restore brings them back.

### B.4 Standalone Shorts mode

Keep the existing standalone mode but rename it in the GUI to "Short original (história própria)" so it is
not confused with Shorts cut from an episode.

### B.5 Tests

Segment validation (duration, sentence boundaries, no final-twist reveal flag), reframing math (subject box →
9:16 crop, blur-fill fallback), caption safe areas, audio slice exactness, loudness, approval hashes including
Shorts, upload order and scheduling (mocked), ledger parent link, archive layout.

## C. Commands for the user

```powershell
python -m scripts.project new --brief-file briefs/ai-companion.md      # or GUI → Novo projeto → Inserir modelo
python gui.py   # run stages; review episode + Shorts in the approval gate; upload all
```
