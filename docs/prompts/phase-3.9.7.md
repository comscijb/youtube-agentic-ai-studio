# Private Gravity — Phase 3.9.7

**Cold open: every episode starts at its most dramatic moment, then goes back and builds to it**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests (mock Gemini). Never read `.env`/tokens. Never push. One commit per section.

## 0. Decision (user, 2026-11-09)

Episodes should no longer start at the chronological beginning. They open on the **most dramatic moment**
as a short teaser, then the story starts and builds back up to it. The teaser creates expectation without
giving away too much.

**Current state:**
- The premise brief parser keeps only the six beat keys (`BEAT_KEYS` in `agents/premise_brief.py`). A
  `- Cold open:` beat in a brief is **silently dropped**. `briefs/filmed-childhood.md`, the next episode,
  already has one.
- The outline has no teaser concept.
- The 3.9.2 chronology guard would flag a flash-forward as "an event planned for a later scene".

## A. The cold-open contract

- A new **optional first scene of kind `cold_open`**, placed before the hook scene:
  - **Length:** 50–100 words (about 20–40 s at the channel voice). Hard maximum 110.
  - **Source:** a moment from the **reversal or climax** scene, chosen by the outline. When the brief
    gives a `cold_open` beat, use it (authoritative).
  - **Show the tension, not the outcome:**
    - stop at the instant before the payoff (a reaction, a discovery about to be seen, a line that raises
      a question);
    - never reveal the resolution, the ending, the final twist, or the content of the discovery.
  - **Echo:** the later scene must contain the same moment, and **one** matching line or image, so the
    viewer feels "here it is" when the story reaches it. Allow at most one sentence of overlap; the rest
    is new prose.
  - **Return:** the first scene after the teaser re-anchors in time without a stock phrase, through a
    concrete marker ("Three weeks earlier, Leo turned twelve."). Avoid "Let's go back to the beginning".
- **Per-type default**, set in `config.COLD_OPEN`, editable in Settings:

  | Type | Mode | What it is |
  |---|---|---|
  | **A** | `flash_forward` | Teaser from the reversal |
  | **B** | `flash_forward` | Teaser from the moment the rule's cost appears; keep the rule itself as a hook |
  | **C** | `omen` | One or two lines in the tale-teller's voice that foretell the ending without explaining it ("This is the story of how Low Marrow lost its bridge twice."); no scene jump, which preserves the parable feel |
  | any | `off` | No cold open |
- **Brief support:**
  - recognise `cold open` / `cold_open` / `teaser` / `omen` as a beat key, and add it to `BEAT_KEYS`,
    the aliases, the template ("Inserir modelo") and `brief_coverage`;
  - map it to a new arc movement `cold_open`;
  - when the brief has no cold-open beat, the outline proposes one and marks it `proposed` for the human
    review.

## B. Generation and review

- **Outline:** a `cold_open` scene with `source_scene_id` (the later scene it previews) and
  `withheld` (what must not be revealed, e.g. "what the clip shows").
- **Screenplay prompt:** write the teaser in the present tense or in a suspended past ("Hannah froze…")
  and end on the question.
- **Deterministic checks (findings):**
  - teaser length;
  - overlap with the source scene ≤ 1 sentence;
  - no ending or twist words: compare with the ending and reversal payoff and with the `withheld` text,
    plus a semantic check inside the editorial call ("does the cold open reveal X?");
  - the return line exists;
  - a cold open is present when the type's mode requires one.
- **3.9.2 chronology guard:** exempt the `cold_open` scene and its echo; still check the rest of the
  order.
- **Scene budgets:** the teaser has its own budget (50–100 words) and doesn't take words from the climax.
- **Variation guard:** `opening_device` now records the cold-open mode plus the kind of moment
  (discovery / confrontation / line / image). Warn when the last three episodes used the same kind of
  moment, not merely a cold open.

## C. Audio, video and packaging

- **Order:**
  1. cold open;
  2. ~1.5 s pause with a short music sting or swell;
  3. **title card or first chapter title**, if the video has one;
  4. the story.
  
  Chapter 0:00 becomes a neutral title (e.g. "Prologue" or the episode's motif), never a spoiler.
- **Visuals:**
  - the teaser's shots may reuse the source scene's images (`reuse_of`) with a different crop or motion,
    or get 2–3 shots of their own;
  - apply a subtle distinct treatment (slightly darker, slower motion) so the jump in time reads;
  - never show the withheld content, e.g. the clip on the monitor stays a soft glow.
- **Shorts:** the cold open is an ideal Short opener. The planner may propose a Short that starts with
  the teaser, as long as it still never reveals the payoff (existing twist rule).
- **Thumbnail:** the cold-open moment is a strong default concept (the 3.9-E coherence check passes
  automatically, since it is in the video).
- **Description hook:** it must not repeat the teaser's line.

## D. Tests and fixture

- Use `briefs/filmed-childhood.md` as the fixture. Its `- Cold open:` beat must be parsed and kept, and the
  coverage map must include it.
- The teaser checks: length, overlap, a reveal detected via the `withheld` text, the return line.
- The chronology exemption.
- Type C omen mode produces no scene jump.
- `COLD_OPEN = off` reproduces the old behaviour.
- Chapter 0:00 is neutral.
- Shorts twist rule with a teaser start.

## E. Report and commands

Report:
- the outline and teaser generated for the fixture brief, offline with a recorded mock;
- which published episodes would have had which cold open (one line each, as an example list only).

```powershell
python -m scripts.project new --brief-file briefs/filmed-childhood.md --minutes 11
python gui.py   # Roteiro: cena "cold_open" no topo da revisão; Settings → Cold open por tipo
```
