# Private Gravity — Phase 3.6

**Packaging for clicks (thumbnails, titles, descriptions with CTAs and chapters) · English language signals and captions · Analytics panel · Tape (VHS) playback effect**

> Implementation prompt for Claude Code. Branch `feat/phase-3.6` from the current head. Report in
> **Portuguese**. No live calls in tests (mock Gemini, ComfyUI, YouTube APIs). Never read `.env`/tokens.
> Never push. One commit per section.

## 0. Evidence

- Episode 1 (`w-XazyuEUv8`, archived in `D:\Private Gravity\Archive\...`): **220 impressions in ~12 h, 0
  clicks**. Small sample, but packaging is weak.
- Published thumbnail review: uniformly mid-grey with no contrast or accent colour (disappears at feed size,
  ~170 px wide on phones); the text "ARE THEY WATCHING?" sits over busy guests' faces; Julian's head is
  cropped at the top and his expression is neutral; the image doesn't show the idea (guests laugh among
  themselves, nobody looks at him); the "PRIVATE GRAVITY" wordmark takes space without helping.
- Title = the raw premise ("The Man Who Needed Everyone to Envy Him"): acceptable, but not the strongest
  hook available in the story (the reversal — he turns his wife's grief into a performance — is far more
  compelling).

## A. Thumbnail system (2–3 variants per episode)

- New `thumbnail_concepts` stage (Gemini, contract-first, JSON): 3 distinct concepts per episode, each with
  `composition` (from the templates below), `emotion`, `focal_element`, `text` (≤ 4 words, validated by the
  existing thumbnail-text rules), `text_side`, and why it creates curiosity without explaining the thesis.
- Composition templates (pick per concept, never the same twice):
  1. **Big face, strong emotion**: protagonist close-up (head fully in frame, eyes in the upper third),
     a readable expression (fear, contempt, forced smile, realisation), one warm light on the face.
  2. **Small figure vs. overwhelming presence**: tiny protagonist against a huge shadow, a crowd of
     silhouettes with glinting eyes, or a towering room — the scale is the hook.
  3. **The telling object**: one symbolic object from the story large and lit (the key ring on the table,
     the untouched necklace box, the raised glass) with the protagonist small or out of focus.
  4. **Two-person tension**: protagonist and one other character, faces turned, clear conflict.
- **Thumbnail-specific grading** (only for thumbnails, not the video): lift midtones, stronger local
  contrast and clarity on the subject, darker clean background, **one saturated accent** (Antique Gold or
  Dusty Crimson) on the focal element. Brand stays recognisable through the drawing style and typography.
- **Text layout**: ≤ 4 words, very large (cap height ≥ 18 % of the image height), on a clean dark area with
  a soft shadow; never over faces; auto-check text/background contrast (WCAG-style ratio ≥ 4.5) and that the
  subject's head is fully inside the frame (detector box). Drop the "PRIVATE GRAVITY" wordmark from
  thumbnails (keep an optional tiny logo corner mark, default off).
- **Mobile check**: render a 168×94 preview next to each variant in the GUI and in the review report.
- Generate the variants with the protagonist/character references (existing reference path) and run the
  existing advisory QA. The GUI review shows the 3 variants side by side; the user picks the one uploaded
  by the API and can download the others for YouTube Studio's **Test & compare** (manual upload there; not
  available through the API — don't try).
- `python -m scripts.thumbnails --project <slug> [--restore]` regenerates variants for an existing or
  archived project (restore only the needed files).

## B. Titles

- Story Development keeps generating 5 candidates; add a scoring pass (Gemini, low temperature) on:
  curiosity gap, concreteness (a person, an act, a number), emotional charge, clarity at a glance, length
  ≤ 60 characters (front-load the hook within ~45). Keep the existing validator (no thesis titles).
- Encourage candidates built on the story's **most shareable moment** (the reversal), not only the premise.
- The GUI review shows the scored list; the user can pick or edit; the chosen title and the alternatives are
  stored for later swaps.
- Title + thumbnail must complement each other (the thumbnail text must not repeat the title).

## C. Description template with CTAs and chapters

Every upload description is assembled from a template (editable in Settings) and validated:

1. **Hook** (1–2 lines, from the logline, no spoilers of the ending).
2. **Chapters** from `scene_timeline.json`: first at `0:00`, at least 3 chapters, each ≥ 10 s, short
   evocative scene titles (no spoilers) — YouTube's chapter rules.
3. **CTA block** (always): subscribe, like, share with someone who'd recognise themselves, and a
   **comment prompt** specific to the episode (generated: an open question from the story's philosophical
   question, e.g. "Have you ever performed happiness for someone else's eyes?").
4. Short channel line (from the channel description), music/licence attributions when required, hashtags
   (2–3, at the end).

Validator: total ≤ 5000 chars, CTA block present, chapters valid. Also generate a **pinned-comment
suggestion** (same question + invitation) shown in the GUI for manual pinning (the API can't pin).

## D. English language signals and captions

- Upload sets `snippet.defaultLanguage="en"` and `snippet.defaultAudioLanguage="en"`.
- Upload `narration.srt` as English captions with `captions.insert` (track name "English"). This needs the
  `https://www.googleapis.com/auth/youtube.force-ssl` scope: add it, detect tokens without it and trigger
  re-consent (the user's OAuth app is in *Testing*; the channel's account is a test user).
- Backfill command for already published videos: `python -m scripts.youtube_backfill --video-id <id>
  [--captions] [--language] [--description]` (reads the archived project for SRT and metadata).

## E. Analytics panel

- YouTube Analytics API (`yt-analytics.readonly` scope, same re-consent flow): per published video —
  impressions, impressions CTR, views, average view duration, average percentage viewed, subscribers gained,
  **views by country** (top 10 + share of US), **traffic sources**, daily series since publication.
- GUI "Desempenho" tab per project and a channel overview; cache results (`data/analytics/<video_id>.json`),
  refresh on demand or with the publication sync (max every 6 h). Note that Studio data lags 24–48 h and
  small numbers are bucketed.
- Simple flags: CTR < 2 % after ≥ 1 000 impressions → suggest a new thumbnail/title; average % viewed < 30 %
  → flag the opening.

## F. Tape (VHS) playback effect

Optional post-process (`VIDEO_TAPE_EFFECT = "off" | "subtle" | "medium"`, default `subtle`), applied to the
video frames **before captions are burned** (captions stay crisp):

- film/tape grain, very light chromatic aberration (1–2 px), faint scanlines, soft vignette, slight
  luminance wobble, gentle horizontal gate weave;
- **tracking glitch** (brief horizontal tear + noise band, 0.2–0.4 s) only on scene cuts and optionally on
  the title card — not continuously;
- **photosensitivity safety**: no full-frame flashes, luminance changes ≤ 10 % between consecutive frames,
  never more than 3 flashes per second;
- implement with ffmpeg filters (`noise`, `rgbashift`/`chromashift`, `geq` or overlay for scanlines,
  `vignette`) in the final encode pass to keep render time low; measure the overhead;
- `python -m scripts.preview_tape_effect --seconds 20 --intensity subtle|medium` renders a 20-s sample
  side by side (before/after) for the user to choose.
- Thumbnails never get the tape effect.

## G. Tests and commands

Tests: thumbnail text/contrast/head-in-frame checks, chapter rules, CTA presence, description length,
caption upload and language fields (mocked), scope re-consent, analytics parsing and caching, tape filter
graph construction and the photosensitivity guard.

```powershell
python -m scripts.youtube_backfill --video-id w-XazyuEUv8 --captions --language --description   # re-consent first time
python -m scripts.thumbnails --project 2026-10-21-the-man-who-needed-everyone-to-envy-him        # 3 new variants for episode 1
python -m scripts.preview_tape_effect --seconds 20 --intensity subtle
python gui.py   # Desempenho tab
```
