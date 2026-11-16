# Private Gravity — Phase 4.2

**The video must feel alive: visual rhythm (3–5 s per view from the same images), on-screen key lines, light and colour that follow the beats, sound design, music that follows the story, and a viewer-experience QA for the cut**

> Implementation prompt for Claude Code. Branch `feat/phase-4.2` from `main`. Report in **Portuguese**.
> No live calls in tests (mock ComfyUI and Gemini). Never read `.env`/tokens. Never push. One commit per
> section. Keep every new feature behind a setting with a sensible default on.

## 0. Evidence (real data now arriving, 2026-11-16)

- **Retention lesson from YouTube's curve** ("Everyone Heard What He Thought", 16.6 % average viewed):
  "**queda de 39 % aos 0:23**, no meio da abertura, frase: «Soft surface thoughts murmur like distant
  water.»" Phase 4.1 attacks the *text* of openings. This phase attacks what the viewer **sees and hears**
  in those same seconds and afterwards.
- **Measured on three archived episodes:**
  - **average 9.5–9.7 s per image** on screen (max 19 s), 60–71 images for 10–11 min;
  - one music track per episode at 12 % under the voice;
  - **no sound effects at all**;
  - the same grey-blue grade from start to end.
  
  On a phone this reads as a slideshow with a voice.
- **Shorts** cut from episodes keep 31–80 % viewed, which shows the same material holds better when it
  moves faster. Shorts get their own phase (4.3). Here, only the long-form edit.

GPU budget stays the same (8 GB VRAM, ~30 s per image). Rhythm must come mainly from **more views per
generated image**, not from many more generations.

## A. Visual rhythm: derived views from each image

- **Target on-screen time per view, by beat** (config `RHYTHM`, measured in seconds):

  | Beat | Seconds per view |
  |---|---|
  | cold open | 2–3 |
  | tension / reversal / climax | 2–4 |
  | development | 3–5 |
  | quiet reflection | 4–7 |

  Hard maximum: **7 s** without a visual change, except the final held image.
- **Derived views.** From each generated shot, create 2–3 views:
  - the full frame;
  - a **medium crop** around the main subject (layer-1 person/face boxes);
  - a **detail crop**: face or eyes, hands, the story object (the deed, the phone, the stone), or the
    light source.
  
  Each view gets its own motion (parallax, push-in, slow pan) and they cut **on word or sentence
  boundaries** from `narration_words.json`. Prefer cutting on attention events from 4.1's
  `attention_map.json` (question, reveal, dialogue turn).
- **Sharpness of crops.** A crop of 40–60 % of a 1280×720 image is too soft at 1920×1080. Two steps:
  - **Probe** the ComfyUI upscale models available (`ImageUpscaleWithModel`, any 2×/4× model such as
    UltraSharp / RealESRGAN). If none exists, list install options with licences and stop for the user;
    never install silently.
  - Upscale only the images that get crops, cached in `images/shots/upscaled/`. If no model is
    available, use Lanczos plus the film grain, which hides some softness, and limit crops to ≥ 60 % of
    the frame.
- **Composition-aware crops:** never cut through a face; keep the eye-line; the detail crop must contain
  a recognisable object (check it with the detector, or by contrast/edge density). Reject crops that are
  mostly empty texture.
- **Shot planner:**
  - the planner may request **one extra generated image** for the 2–3 key moments (hook, reversal,
    final image) when the derived views aren't enough;
  - the GPU budget per episode is shown in the GUI before rendering;
  - the cold open gets its own quick-cut sequence (4–6 views in 15–20 s).
- Show the **rhythm report** in the GUI: average and maximum seconds per view, per scene, and against the
  targets.

## B. Key lines on screen

- From the attention map, pick **1–3 lines per minute** (questions, reveals, the hook line, the final
  narrated question) and show them as **large on-screen text** for the duration of the spoken words:
  - brand typography, ivory on a soft dark gradient;
  - a word-by-word reveal synced to word timings;
  - never covering faces (detector boxes);
  - never at the same time as the burned captions. Hide the captions while a key line is up, or place
    the key line in the upper third.
- Never show text that reveals something the narration hasn't said yet. The cold open's key line is its
  question, not the answer.
- A setting lets you turn it off.

## C. Light and colour follow the beats

- A per-beat grade on top of the film look (config `BEAT_GRADES`):

  | Beat | Grade |
  |---|---|
  | calm | current palette |
  | tension | +contrast, deeper blacks, cooler midtones |
  | reversal | brief desaturation, a single crimson or gold accent kept |
  | ending | warmer, softer |
  | cold open | slightly darker and slower than the rest (3.9.7) |

- **Transitions:**
  - hard cuts inside tension;
  - a soft crossfade inside calm scenes;
  - a **dip to black (0.4 s)** on time jumps and on the return after the cold open.
  - Keep the photosensitivity guard: no flashes, luminance step ≤ 10 %.

## D. Sound design

- **SFX library** in `assets/sfx/`, organised by tags (`door`, `footsteps`, `phone_buzz`, `bell`,
  `paper`, `wind`, `rain`, `river`, `fire`, `clock`, `crowd_murmur`, `heartbeat`, `impact`, `riser`,
  `whoosh`…):
  - **only licences that allow commercial use without attribution** (e.g. CC0, or the YouTube Audio
    Library's sound effects);
  - record the source and licence per file in `assets/sfx/LICENSES.json`;
  - a script lists missing tags so the user can download them; never scrape;
  - report what the user must download.
- **Cue sheet per episode.** One semantic pass over the screenplay (or extend 4.1's attention map) produces
  `sfx_cues.json`:
  - **actions** with sound (door, buzzing phone, bell, coins, pages, steps);
  - **ambience beds** per location (room tone, rain, river, wind; looped and crossfaded at scene changes);
  - **accents**: a soft impact on reveals, a riser into the cold open's cut, a heartbeat or low drone
    under tension.
  
  Each cue carries the quote and is timed from word timings. At most ~4 accents per minute.
- **Mix:**
  - beds about 26 dB under the voice, actions about 18 dB under, accents about 14 dB under;
  - everything ducked by the narration (the same sidechain as the music);
  - final loudness stays −14 LUFS / −1 dBTP;
  - speech intelligibility check (D2 of F).

## E. Music that follows the story

- **Tag the library** in `music library/` using the mood already in the file names (Sombrio, Melancólico,
  Dramatic), plus a tempo/energy analysis (RMS and onset density), stored in `music_index.json`. Report
  which moods are thin, so the user can add tracks from the YouTube Audio Library.
- **Per episode, a music plan:**
  - 2–3 tracks matched to sections (setup → tension → reversal → ending);
  - crossfades at scene boundaries with an emotional turn;
  - a **sound drop**: music fades to near-silence 1–2 s before the reversal's key line and returns after
    it;
  - a **swell** under the final narrated line, then silence over the end card except the logo sting.
- Keep the 4.0 Hype line and the end card.

## F. Viewer-experience QA for the cut (the video attention map)

Before rendering the final file, compute a **video attention map** in 5-second bins:
- narration events (from 4.1);
- visual changes (view cuts, motion changes, text on screen);
- audio events (SFX accents, music changes).

**Checks:**
1. no bin run longer than **7 s** without a visual change (except the final image);
2. no **20 s** stretch without any narration, visual or audio event;
3. **the first 5 seconds** contain image motion **and** a sound event **and** the first spoken words (no
   black screen, no slow fade-in, no title card before the cold open);
4. key lines are readable: ≥ 1.2 s on screen, contrast ≥ 4.5;
5. **speech intelligibility:** in every 1-second window, narration loudness stays at least 10 dB above
   SFX + music in the 1–4 kHz band;
6. no SFX overlapping another accent within 1 s.

Findings show in a "Mapa de atenção do vídeo" next to 4.1's screenplay map, with one-click fixes:
- add a derived view;
- shorten a hold;
- remove an SFX;
- lower the music.

Rendering is allowed with warnings; major findings (checks 1, 3, 5) need a typed override.

## G. Preview and measurement

- Use `python -m scripts.preview_cut --project <slug> --seconds 60 [--from 0]` for a quick render of any
  minute with all of the above, to compare with the old cut. Default: the first 60 s, which is where the
  drop is.
- After the next episodes are published, the report compares **retention at 0:30 and at 1:00** for new
  versus old episodes (from `data/analytics`). Add the comparison to Desempenho.

## H. Tests

- Derived views:
  - crop boxes from detector boxes;
  - no face cut;
  - the minimum crop size without an upscaler;
  - cuts aligned to word boundaries;
  - per-beat duration targets met on a fixture timeline.
- Key lines: choice from a fixture attention map; no overlap with faces or captions; no spoiler (only
  already-spoken words).
- Grade per beat, and the dip-to-black on time jumps.
- The photosensitivity guard holds with the grades.
- SFX: the cue sheet's quotes must exist in the narration; accent density cap; the licence registry is
  required for every file used.
- Music plan: track choice by mood, the sound drop before the reversal line.
- Video attention map: each check on synthetic timelines.
- The intelligibility check on synthetic mixes.

## I. Report and commands

Report:
- the upscaler probe;
- the rhythm report for the current parable (before and after);
- the SFX tags still missing;
- the music index gaps;
- the video attention map of the preview minute.

```powershell
python -m scripts.preview_cut --project 2026-11-12-the-man-who-bought-the-sunrise --seconds 60   # old vs new opening minute
python gui.py   # Vídeo → Mapa de atenção do vídeo; Settings → Ritmo / Som / Música
```
