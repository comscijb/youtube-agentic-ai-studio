# Private Gravity — Phase 3.9.5

**Kokoro without stutter: our own sentence chunking, no hidden batch joins, an ASR stutter check with automatic re-synthesis**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests (mock the Kokoro worker and faster-whisper). Never read `.env`/tokens. Never push. One
> commit per section.

## 0. Evidence

User report after 3.9.4: everything is almost perfect, but **some words stutter** in the narration. It is
evident and breaks the experience.

**Cause, found in the code.** `audio/tts/kokoro_worker.py` sends a **whole scene** (150–190 words, about
900–1200 phonemes) to `kokoro_onnx.Kokoro.create()` in one call (`.venv-tts`, kokoro-onnx 0.4.7). Inside
`create()`:
- `_split_phonemes` cuts the phoneme string into batches of fewer than 510 (`MAX_PHONEME_LENGTH`). It
  splits at any of `. , ! ? ;`, so it **often cuts mid-sentence at a comma**, wherever the length runs
  out.
- Each batch is synthesized **separately, with no context**. The prosody resets, and the first phonemes
  of a batch can be doubled or smeared.
- With `trim=True`, each batch goes through `trim_audio` (aggressive, can clip onsets and tails), then
  the batches are **concatenated with no crossfade**.

The result is 2–3 hidden joins per scene, about 15–25 in the episode. That matches "stutter in some
words". The Edge voice never had this because it synthesized server-side. Our own assembly (3.9.4) joins
only at sentence ends and is not the cause, but it can't fix joins that happen *inside* the audio Kokoro
returns.

## A. Our chunking, never Kokoro's

- In `audio/tts/local.py` (and the Kokoro worker protocol), synthesize **one sentence per call**:
  - split the scene text into sentences with the same sentence logic the captions use, and keep quoted
    dialogue and thoughts intact;
  - measure each sentence's phoneme length **in the worker**, with the model's own tokenizer
    (`model.tokenizer.phonemize`), through a new `{"op": "phonemes"}` request;
  - if a sentence exceeds **~350 phonemes** (safety margin under 510), split it at the best clause
    boundary: a semicolon, then a comma followed by a conjunction, then the comma nearest the middle.
    Never split inside quotes. Mark the join as `clause` (pause 0.12 s).
- Call `create(..., trim=False)`. Trim **ourselves** with `assembly.trim_bounds` (−50 dB, keep 40 ms)
  so onsets and tails are never clipped.
- Keep `speed` explicit per call (the channel speed, 0.88).
- Return a list of sentence clips with their text. `video/narrator.py` / `audio/assembly.py` place them
  directly with the configured pauses:
  - sentence 0.35 s, paragraph 0.7 s, scene 1.5/2.0 s, final 2.5 s;
  - clause joins 0.12 s, with equal-power fades of 8 ms.
  
  Word timing comes from forced alignment **per sentence clip**: shorter, so more accurate. Then offset.
- Cache per sentence: key = backend + voice + speed + model version + normalized sentence text. Editing
  one sentence re-synthesizes only that sentence.
- Assert in the worker that no request ever exceeds the limit, so a batch split can't happen silently:
  log an error and refuse if `len(phonemes) >= 500`.

## B. Stutter check (ASR) with automatic re-synthesis

- After synthesis, transcribe each sentence clip with faster-whisper (already installed for alignment;
  reuse the loaded model, word timestamps on) and compare with the sentence text, after normalizing
  numbers, punctuation and case:
  - **repetition:** a word or syllable sequence that appears twice in a row in the transcript but once
    in the text ("the the", "fla-flame");
  - **insertion or omission** of a content word;
  - **duration anomaly:** a word whose duration is more than 2.5× the median for its length.
- Also run an **acoustic check** that works even when the ASR normalizes a stutter away: a short window
  of 60–250 ms whose normalized cross-correlation with the window right after it is above 0.9 inside a
  word, using the word timings.
- On a hit, re-synthesize that sentence (at most 3 tries):
  1. the same text, with speed ±0.02;
  2. the text with a comma removed or added near the hit;
  3. the sentence split at the best clause boundary.
  
  Keep the best attempt (fewest hits). If it still fails, report it as a stage warning with the
  timestamp and the sentence, and make it playable in the GUI.
- **Narração stage report:** sentences synthesized, re-synthesized and still flagged, with ▶ play buttons
  for the flagged ones in the stage card.
- Calibrate the thresholds on this project:
  - run the check on the current `narration.mp3` (built with batch joins); it should find hits;
  - list the timestamps in the report, so the user can confirm by ear that they match what they heard;
  - the rebuilt narration must have no hits, or only explained ones.

## C. Cleanups

- The Vozes audition and previews use the same per-sentence path. Re-render the preview samples,
  because the old ones contain batch joins.
- `scripts/voice_audition.py` reports the stutter-check hits per voice.
- Document in the README (TTS section) why sentences are synthesized one at a time.

## D. Tests

- Sentence and clause splitting: quotes kept whole, the phoneme limit respected, long-sentence fixtures
  from this project's script.
- The worker refuses requests over the limit.
- `trim=False` is passed.
- Clause pause and fades.
- Per-sentence cache keys: editing one sentence re-synthesizes only it.
- Repetition, insertion and duration detectors on synthetic transcripts.
- The acoustic check on a synthetic clip with a duplicated 120 ms segment (flagged) and a clean one (not
  flagged).
- The re-synthesis escalation order and best-attempt choice.
- Re-timing of shots and Shorts after a rebuild still keeps images and approvals (3.9.4-G).

## E. Report and commands

Report:
- the number of hidden batch joins in the old narration: count the scenes whose phonemes exceeded 510,
  and where the cuts fell;
- the stutter-check hits before and after;
- the synthesis time per scene before and after;
- the new episode duration.

```powershell
python -m scripts.voice_audition --catalogue          # previews rebuilt without batch joins
python gui.py   # Projeto → Narração (Refazer) → relatório de gagueira → Vídeo
```
