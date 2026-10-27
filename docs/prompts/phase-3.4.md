# Private Gravity — Phase 3.4

**Pre-publication polish: reference coverage, clean prompts, explicit headcount, automatic visual QA, thumbnail identity, encode settings · Archive published projects to the HDD**

> Implementation prompt for Claude Code. Branch `feat/phase-3.4` from the current head. Report in
> **Portuguese**. No live calls in tests (mock Gemini, ComfyUI). Never push. One commit per section.

## 0. Evidence — first complete illustrated episode (project `2026-10-21-the-man-who-needed-everyone-to-envy-him`)

Frame-by-frame review (every 8 s), shot list and thumbnail:

- Style, palette, Elena's per-scene wardrobe and Marcus's identity are consistent. Audio −14.3 LUFS,
  captions clean.
- **Duplicated protagonist**: two Julians in the same frame (≈3:04–3:12 next to Elena; ≈4:40 an extra
  young man; ≈10:00 Julian with and without glasses at the same table).
- **Julian without his glasses / with another face** in ~10 shots (≈0:48, 1:36, 6:08, 6:48, 8:00, 9:04).
- **7 of 50 character shots carry no reference for some listed characters** (`s01_01, s03_02, s03_05,
  s04_06, s06_05, s09_01, s09_02`): shots with `face_visibility: none` skip character refs, so the model
  invents a generic person; `s06_05` dropped Marcus because slots ran out.
- **Prompt composition is messy**: descriptors and grooming repeated, outfit sentences duplicated, and
  states from *other* scenes leak in (`s03_02` contains "wet tear streaks on pale cheeks in scene s05").
- No explicit headcount, so the model fills frames with copies.
- Extra people in the s02 kitchen (≈1:36) although no guests are present; a red mark on Elena's
  fingers (≈2:56).
- **Thumbnail shows a different man** (no glasses, younger, long hair) — not Julian's reference.
- `final_video.mp4` is 1.06 GB at 13.9 Mbps (`RENDER_PRESET="ultrafast"`); final true peak −0.9 dBTP
  (target −1.5 from 3.0.1 not reaching the final file).
- Wide/establishing 8/50 (target 30 %); several images hold 16–24 s.

## A. Reference coverage and slot priority

- Every listed character gets its approved reference **regardless of face visibility** (silhouettes and
  backs still need build, hair, glasses, wardrobe).
- Slot priority (max 4): listed characters (all, up to 3) → location → style anchor. Drop the style anchor
  first, then the location; never drop a listed character. Shots with 4+ characters: keep the 3 most
  important as refs and reduce the others to distant, indistinct figures in the prompt.
- Test: no shot in a rebuilt shot list lacks a ref for a listed character when ≤ 3 are listed.

## B. Clean prompt composition

- One sentence per character, built from: ordinal reference ("the man from the first image") + **identity
  markers that must always appear** (new `signature_markers` per character in the visual bible, e.g.
  Julian: "horn-rimmed glasses", Marcus: "black velvet jacket, gold signet ring") + this scene's outfit +
  this moment's expression/grooming. Never the full descriptor when a reference is present (the image
  carries identity); keep the descriptor only for characters without refs.
- Scene-scoped state only: strip any phrase mentioning other scene ids or "in scene sNN"; the visual bible
  must store per-scene states as structured fields (`scene_states: {s05: "wet tear streaks"}`), and the
  composer selects only the current scene's entry.
- De-duplicate repeated clauses; cap prompt length; unit tests on the s03_02 case.

## C. Explicit headcount

- Every prompt states the exact number and identity of people: "Exactly two people in the scene: the man
  from the first image and the woman from the second image." For `others_present=true`, add "plus a few
  distant, indistinct guests in shadow" only when the narration places them.
- Over-the-shoulder: "seen over the shoulder of the man from the first image; the woman from the second
  image faces the viewer" — never a third body.

## D. Automatic visual QA (Gemini vision)

After each generated shot (and thumbnail), send the image (downscaled ~768 px) plus a compact checklist to
Gemini Flash (multimodal, JSON mode) and record the answer in the manifest:

- `people_count` vs expected; duplicated person (two people who look like the same character);
- each listed character's signature markers present (e.g. Julian's glasses) when visible;
- injuries/blood/marks not stated in the scene narration; unexpected extra people; text/letters/watermark;
  borders/panels; aspect/legibility.

Policy: on a failed **blocking** check (headcount, duplicate, missing signature marker on a visible face,
unstated injury, text) regenerate with a new seed (max 2), then keep the best attempt and flag it in the GUI
shot grid with the QA notes. Config: `IMAGEGEN_VISUAL_QA=true`, budget/latency logged in the Gemini usage
summary; graceful skip (warning) if Gemini is unavailable. Tests with mocked QA responses (pass, fail →
regenerate, fail twice → flagged, Gemini down → skipped with warning).

Also add `python -m scripts.qa_shots [--only sNN]` to run QA on existing shots without regenerating, and a
GUI button "Revisar planos com QA".

## E. Thumbnail identity

- The thumbnail prompt must use the protagonist's approved reference + signature markers + the brief's
  thumbnail concept; run visual QA on it; composition leaves negative space on the text side but the figure
  should occupy ≥ 35 % of the frame height-wise (current one wastes the left half).

## F. Pacing, encode and loudness

- Shot planning target: one image every 6–9 s (≈ 70 shots for 10 min); `IMAGEGEN_MAX_UNIQUE_SHOTS` default 75;
  keep the wide-share guidance (warning only).
- Final encode: `RENDER_PRESET="veryfast"` (or `medium` if time allows) with CRF ≈ 18–20 instead of a fixed
  high bitrate; target ~4–8 Mbps for 1080p24. Log file size and duration.
- Loudness: measure true peak **on the final MP4**; if > −1.0 dBTP, re-run the audio limiter at −1.5 dBTP and
  remux (no video re-encode).

## G. Archive published projects to the HDD

The user works on the fast SSD `F:` and keeps the archive on the larger HDD `D:`. After a video is
**published**, its project must be archived to `D:\Private Gravity\Archive\<project_slug>\` so the
SSD doesn't fill up.

### G.1 Command and trigger

- `python -m scripts.archive_project --project <slug> [--dry-run] [--force --reason "..."]`.
- Allowed only when the publication ledger says `published` for that project (or with `--force` and a
  written reason, recorded in the archive manifest).
- **Automatic:** when `sync_publications` marks a video `published`, it archives that project
  (`ARCHIVE_ON_PUBLISH=true` by default). If `D:` is unreachable or short of space, queue it
  (`data/archive_queue.json`), warn, and retry on the next sync; never block generation.
- GUI: "Arquivar" button on published projects (and status "arquivado" in the project list).
- Config/.env: `ARCHIVE_ROOT="D:\Private Gravity\Archive"`.

### G.2 Layout (`<ARCHIVE_ROOT>\<slug>\`)

```
video\      final_video.mp4, thumbnail.jpg, thumbnail_bg.jpg
images\     shots\, character_refs\, location_refs\, style_refs\ (copies of the anchors used)
audio\      narration.mp3, narration_mixed.mp3, music\<track used>  (copied from music library)
captions\   narration.srt
text\       story_brief, research, character_bible, story_outline, script, narrative_review,
            visual_bible, shot_list, shot_list_report, youtube_metadata.json (title, description,
            tags, video id/url, published_at), review_approval, project.json
manifests\  asset_manifest (licenses, prompts, seeds), scene_timeline, archive_manifest.json
logs\       debug\ and the Gemini/ComfyUI usage summaries if present
```

Unknown project files go to `other\` (never silently skipped). Map every artifact explicitly in code so
new artifacts get a home.

### G.3 Move vs copy

- **Move** (copy → verify SHA-256 → delete source) everything that belongs only to the project: video,
  thumbnails, shots, character/location refs, narration, mix, captions, text artifacts, manifests, logs.
- **Copy only** shared assets: the music track(s) from `music library\` and the channel style anchors
  from `assets\style_refs\` that this project used.
- Regenerable caches (`.imagegen_cache`, `.tts_cache`, `.stock_cache`, `.checkpoints`) are **deleted**
  from the SSD after a verified archive (`--keep-cache` to skip); they are not archived.
- Leave on `F:` a stub project folder with `project.json` (status `archived`, archive path, archived_at)
  and `ARCHIVED.txt` pointing to the archive, so the GUI still lists it.

### G.4 Safety

- `archive_manifest.json`: every file with original path, archive path, size, SHA-256, action
  (moved/copied/deleted-cache) and timestamps. Resumable: an interrupted run continues and never
  deletes a source whose archived copy is missing or has a different hash.
- Check free space on the target first (need ≥ 1.2 × project size); refuse otherwise.
- Never overwrite an existing archive file: if the folder exists with different content, write
  `<slug>__2` and report it.
- Handle Windows long paths and paths with spaces (`D:\Private Gravity\...`).
- Deletions on `F:` happen only after the whole project verified successfully.
- `python -m scripts.restore_project --project <slug>` copies an archived project back to `F:` (for
  re-renders or Shorts), keeping the archive intact.
- Tests with temp dirs for source/target: dry-run lists actions; full run moves/copies correctly by
  category; hash mismatch aborts without deleting; resume after interruption; space check; ledger gate;
  auto-archive from a mocked sync; queue when target missing; restore round-trip.

## H. Report and commands

```powershell
python -m scripts.generate_character_refs --status        # refs unchanged; visual bible gains signature_markers (1 Gemini call)
python -m scripts.generate_shots --rebuild-shot-list      # ~70 shots, with visual QA
python -m scripts.qa_shots                                # optional re-check
python gui.py                                             # Video → Review → approval gate → upload
python -m scripts.sync_publications                       # after it is public: marks published and archives to D:\Private Gravity\Archive
python -m scripts.archive_project --project <slug> --dry-run   # manual check/run if needed
```
