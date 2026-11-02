# Private Gravity — Phase 3.9

**Authenticity hardening: a channel voice · motion beyond slideshow · variation guard · visible human authorship · thumbnail–video coherence · disclosure bookkeeping**

> Implementation prompt for Claude Code. Branch `feat/phase-3.9` from the current head. Report in
> **Portuguese**. No live calls in tests (mock Gemini, ComfyUI, TTS engines, YouTube). Never read
> `.env`/tokens. Never push. One commit per section. Existing behaviour must remain available behind
> settings: each new feature can be switched off.

## 0. Context and evidence

YouTube's monetization policies, as of November 2026
([official page](https://support.google.com/youtube/answer/1311392)):

- **Inauthentic content** (renamed from "repetitious" in July 2025). Not allowed:
  - "similar or repetitive content with low … narratives, or minimal variation across videos";
  - "AI-generated content made with generic or unoriginal templates giving the impression of mass
    production".
  
  Explicitly allowed:
  - "a series following a set of characters across episodes";
  - the same intro/outro when "the bulk of your content is different".
- **Unsatisfying or off-putting content** (new). Not allowed:
  - "repeatedly uses disturbing themes (such as violence or loss) without building a cohesive narrative";
  - "realistic visuals tricking viewers".
  
  Allowed: "a cohesive storyline" and "using AI to visualize a unique character".
- **Synthetic-content disclosure** ([help page](https://support.google.com/youtube/answer/14328491)) is
  required only for **realistic** content. Animation, fantasy and stylised content are exempt. Disclosure
  doesn't limit reach.
- **Reviewers.** When the channel applies to YPP, a human reviewer looks at the channel's main theme, most
  viewed videos, newest videos and watch-time share. Since March 2026 YouTube has also been testing a
  viewer prompt asking whether a video "feels like AI slop".

**Where Private Gravity stands.** The stories are original, with cohesive arcs, and each one passes a human
review gate before publishing. That is on the allowed side. The remaining signals that read as
"mass-produced AI" are:
1. the narrator is **Edge TTS `en-US-AndrewNeural`**, a stock voice used by thousands of generic AI
   channels;
2. visuals are **still images with Ken Burns**, the slideshow pattern;
3. the first two episodes both centre on **loss/grief** and share the title shape "He …";
4. human authorship is **not visible** anywhere on the channel;
5. the disclosure decision is **not recorded** in the ledger (`contains_synthetic_media: null`), and Shorts
   hard-code `contains_synthetic_media=False` in `uploader/shorts_upload.py` instead of using the episode's
   decision.

Hardware limits (keep everything within them): RTX 3070 Ti **8 GB VRAM**, 32 GB RAM, Windows, ComfyUI
Desktop on `localhost:8188`.

## A. A distinctive channel voice (pluggable TTS)

### A.1 Backend abstraction

- Move the TTS call out of `video/narrator.py` into `audio/tts/`:
  - a `TTSBackend` protocol: `synthesize(text, out_path, *, voice, rate, pitch, style) -> {audio, words}`;
  - implementations: `edge` (current, default until the user switches), `local` and `elevenlabs`.
  
  Config: `TTS_BACKEND`, `TTS_VOICE`, plus per-backend options.
- **Word timings are mandatory** for captions, shot sync and Shorts cuts.
  - Backends that don't return word boundaries go through **forced alignment** against the known script:
    use a local aligner such as faster-whisper/WhisperX word timestamps, matched to the script words
    (choose the lightest reliable option and document it).
  - Validate the result: monotonic, coverage ≥ 98 % of script words, max drift vs audio ≤ 80 ms on a
    fixture.
- Keep the voice calibration file (`data/voice_calibration.json`, words per minute) **per backend+voice**,
  so preflight estimates stay right.
- Pronunciation: one shared `data/pronunciations.json` (word → respelling/IPA where supported), applied for
  every backend. Migrate the existing fixes.

### A.2 Candidate engines (verify license and VRAM at implementation time; don't assume)

- **Local, offline, free:** evaluate 2–3 current open models that run on 8 GB VRAM (or on CPU in acceptable
  time) and allow **commercial use**. Prefer expressive models with a zero-shot reference voice.
  - Reject any model whose weights license forbids commercial use. Record the license in the report.
  - Run TTS **outside** the ComfyUI process, or through ComfyUI only if a maintained node exists. Free
    ComfyUI VRAM (`/free`) before loading a local TTS model, and unload it afterwards.
- **Paid (optional):** ElevenLabs via API key from `.env` (never print it). Make it a backend that's only
  used when configured.
- **Voice identity rules** (in code comments and the GUI):
  - a reference voice for cloning must be **the user's own voice or one licensed for this use**;
  - never clone a real public person;
  - store the reference clip in `assets/voice/` with a `LICENSE.txt` / provenance note;
  - the GUI refuses a reference clip that has no provenance note.

### A.3 Voice audition

- `python -m scripts.voice_audition --text-file <file> [--backends edge,local,...]` renders the same
  ~60 s passage with each available backend/voice:
  - use one fixed passage from episode 2, with dialogue and a quiet line;
  - write `debug/voice_audition/<backend>_<voice>.mp3`, plus an HTML page (or GUI panel "Vozes") to
    listen side by side;
  - for each sample report: render time, real-time factor, peak VRAM, alignment coverage and drift, and
    loudness.
- The user picks the voice in the GUI. It becomes the **channel voice** (`TTS_BACKEND`/`TTS_VOICE`) and is
  used for all future episodes and Shorts. Changing it later warns that it marks narration as stale.
- Delivery: support simple per-line style hints if the engine allows them (dialogue vs narration, quieter
  for intimate lines), driven by the screenplay's existing speaker/emotion fields. Never add emotional
  tags the script doesn't support.

**Tests:**
- backend selection;
- the alignment matcher on a synthetic fixture (inserted/missing words);
- the calibration key per backend+voice;
- pronunciation applied per backend;
- refusing a reference clip without provenance;
- narration marked stale on a voice change.

## B. Motion beyond the slideshow

Goal: a viewer should never feel they are watching stills with a zoom. Everything must render on 8 GB VRAM
and stay optional.

### B.1 2.5D parallax (default for wide and medium shots)

- Depth map per shot:
  - use a depth model through ComfyUI (a Depth Anything–class node), or a local Python model if lighter;
  - cache it in `images/shots/depth/<shot>.png`;
  - run it in the shots stage after the image is final, and redo it when the image is edited.
- Render: split the image into 2–3 depth layers, inpaint the background holes, and move the layers with a
  slow camera dolly/truck so near layers move more than far ones.
  - Inpaint with a cheap method: OpenCV inpaint or a blurred fill. Don't call the image model per layer.
  - Movement magnitude: subtle, at most 2–3 % of the frame width over the shot, to avoid rubbery edges.
  - Fall back to Ken Burns when:
    - the depth map is flat or noisy;
    - there are large thin structures at layer edges;
    - faces cross a layer boundary.
- Shot-type mapping:

  | Shot type | Motion |
  |---|---|
  | wide / establishing | parallax dolly |
  | medium | gentle parallax |
  | close-up | very slow push-in (current Ken Burns, smaller) |
  | insert / object | slow push or static with micro-drift |

### B.2 Ambient animated elements (cheap overlays)

- A small library of looping, palette-graded overlays in `assets/ambient/`, procedurally generated or
  rendered once:
  - candle/lamp flicker (a radial light mask with gentle intensity noise, placed at a light source the
    shot list names);
  - dust motes in light beams;
  - rain on window;
  - drifting fog/haze;
  - slow smoke/steam (a kettle or mug: useful for episode 2's motifs).
- The shot list gets an optional `ambient` field. Gemini may propose at most one per shot, and only if the
  scene text supports it (e.g. "rain", "kettle", "candle"). It is validated against a whitelist.
- Respect photosensitivity (no flashes; flicker ≤ the film-look guard from 3.8).

### B.3 Short animated clips for key moments (optional, off by default)

- Optional `KEY_MOMENT_CLIPS = 0..3` per episode. Use image-to-video for the hook, the reversal and the
  final image, starting from the approved still, through a local ComfyUI workflow that fits 8 GB VRAM.
  - First **probe** what actually runs: model, resolution, seconds, minutes per clip. Report it.
  - If nothing runs in reasonable time (< 10 min per 3–4 s clip), leave the feature off and say so.
- Clips must keep identity and style (compare against the reference sheet with the existing layer-1
  checks). On failure, fall back to B.1.
- Never a realistic look: the clips stay in the engraving style, so no disclosure is triggered.

### B.4 Pacing variety

- Shot durations vary with the narration rhythm (sentence boundaries and pauses), within the existing
  min/max. Avoid the metronomic "every shot ≈ same length" pattern: report the coefficient of variation
  and target ≥ 0.35.
- Use a hard cut on dialogue turns and a crossfade only on scene changes or time jumps.

**Tests:**
- layer split and parallax offset math;
- fallback rules (flat depth, face across a boundary);
- ambient whitelist validation;
- the photosensitivity guard on overlays;
- shot-duration variation metric;
- the key-moment feature gated off by default.

**Measure** render time for a 12-min episode with B.1 + B.2 vs the current Ken Burns. Report it.

## C. Variation guard (channel-level)

New deterministic and Gemini-assisted checks in the story stage. All of them are **warnings** shown in the
GUI review. None of them blocks.

- **Theme rotation:**
  - each story brief gets a `theme_tags` list (e.g. loss/grief, loneliness, status/envy, self-erasure,
    control, technology-intimacy);
  - warn when the new episode's **primary** tag equals the primary tag of either of the last 2 published
    episodes;
  - warn when "loss/death of a loved one" is central in 2 of the last 3 episodes.
- **Structure rotation:**
  - record the narrative type (A/B/C) and the opening device (cold-open line, object, dialogue, question)
    per episode;
  - warn when the last 3 episodes share the same type **and** the same opening device;
  - suggest an alternative opening device in the outline stage.
- **Title shape:**
  - compare the candidate titles with published titles: same first word, same syntactic frame
    ("He … to …"), and embedding/character similarity;
  - penalise near-duplicates in the 3.6 title scoring;
  - show "parecido com: <title>".
- **Thumbnail composition:** warn when the selected composition template equals the previous episode's.
- **Cadence advisor** in the publication dialog:
  - show episodes per week and Shorts per day;
  - warn above 2 episodes/week or 1 Short/day (the Shorts schedule already spaces them; just surface it).
- Source of truth: the publication ledger plus archived `story_brief.json`. Backfill `theme_tags`,
  `narrative_type` and `opening_device` for the published episodes with one Gemini call each, cached and
  editable in the GUI.

**Tests:**
- the tag-repeat rules;
- the structure rule;
- title similarity (fixture with "He Lied About…" / "He Cut Off…");
- the cadence math;
- backfill caching.

## D. Visible human authorship

- **Description template** (3.6-C). Add a short, honest credit line before the hashtags, editable in
  Settings, e.g.:

  > Original story written and edited by Private Gravity. Illustrations and narration produced with AI
  > tools under human direction.
  
  The validator requires the line to be present.
- **End credit card** (last ~4 s of the episode, after the CTA):
  - "Written & directed by Private Gravity · Illustrations & narration: AI-assisted";
  - brand typography, film look applied, captions off;
  - optional, default on.
- **Channel "Sobre" text:** generate a suggested About-section paragraph saying the same thing (anthology of
  original stories; AI as a production tool). Show it in the GUI for manual paste: no API write needed.
  It must comply with the Community Guidelines (enforceable on channel descriptions since 2026).
- **Editorial record:** the approval gate already records the human review. Also store which edits the
  human made per episode (script edits applied, shots regenerated or edited, title chosen). Keep it in
  `review_approval.json` and in the archive, as evidence of human creative input if a YPP review is ever
  appealed. Make it a short human-readable summary as well as the raw data.

**Tests:**
- description validator requires the credit line;
- end card is added and timed;
- the editorial summary is generated from fixture edits.

## E. Thumbnail–video coherence ("no deceptive imagery")

- For the selected thumbnail, check that its **focal element** (the concept's `focal_element`: character,
  object or location) actually appears in the video:
  - the character appears on screen (from the shot list `characters`), or the object or location appears
    in at least one shot prompt or visual-bible entry;
  - if the concept relies on an element that is never shown, show a warning in the approval gate.
- The thumbnail text must reflect something in the story. Run a Gemini check against the screenplay at low
  temperature: "does the story contain this situation?". If not, warn.
- Shorts: the hook text must come from the segment's own content (same check, cheaper: string and semantic
  match against the segment's words).
- Never realistic visuals: the 3.6 thumbnail grading must not push the image toward photorealism. Keep the
  style suffix in thumbnail prompts, and assert it in a test.

## F. Disclosure bookkeeping

- Single decision function: `uploader.approval.realistic_synthetic_media(run_dir)`, stored once per
  episode as `synthetic_disclosure` with a reason, e.g. "stylised engraving illustrations; non-realistic;
  disclosure not required".
  - The **episode and all its Shorts** use that same value: remove the hard-coded `False` in
    `uploader/shorts_upload.py`.
  - The ledger records `contains_synthetic_media` and the reason for every upload (episode and Shorts).
  - Backfill the 3 published entries from their archived approvals (expected: `false`, stylised).
- Rule kept as is: `true` whenever any shot, clip (B.3) or thumbnail is flagged realistic, or a voice
  imitates a real identifiable person (which A.2 forbids anyway).
- The GUI approval card shows the decision and the reason, with a one-click override that requires a
  typed reason.

**Tests:**
- Shorts inherit the episode decision;
- ledger fields are written;
- backfill;
- the override requires a reason.

## G. Report and commands

Report:
- the voice audition results table (backend, voice, license, RTF, VRAM, alignment) with your
  recommendation;
- parallax and ambient render overhead, plus the key-moment clip probe result;
- variation-guard findings on the 2 published episodes and the people-pleaser brief;
- the disclosure backfill;
- anything you had to skip because of the 8 GB VRAM limit.

```powershell
python -m scripts.voice_audition --text-file briefs/audition.txt      # listen in GUI → Vozes, pick the channel voice
python -m scripts.preview_motion --project <slug> --seconds 30        # parallax + ambient vs Ken Burns
python gui.py                                                         # variation warnings in review; credit line; disclosure card
```
