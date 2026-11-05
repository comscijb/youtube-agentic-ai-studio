# Private Gravity — Phase 3.9.1

**GUI repair and cleanup: broken tab structure, voice previews in the GUI, a clean "Novo projeto" flow, a visual audit**

> Implementation prompt for Claude Code. Continue on `feat/phase-3.9`. Report in **Portuguese**. No live
> calls in tests. Never read `.env`/tokens. Never push. One commit per section.

## 0. Evidence (user report, 2026-11-02, plus inspection)

User report:
- the **Review & Edit** and **Settings** tabs show nothing;
- there is no way to listen to the voices;
- the voice dropdown still lists only the old Edge voices;
- "Novo projeto" seems to open an old project;
- several visual items are out of place.

Root cause of the empty tabs, **already patched by hand** (keep the patch, and add the test from section A):
- `gui_templates/index.html` had a corrupted line in the "Novo projeto" wizard, introduced in `93074a3`
  (phase 3.8-C):
  ```html
  <div class="field-label">Voice<br>          <div>
  ```
  This left two `<div>`s open. As a result `#tab-review` and `#tab-settings` were parsed **inside**
  `#tab-generate > .gen-layout`, and showed or hid together with the wizard.
- The **Vozes** panel from 3.9-A lives in Settings, so it was invisible too.
- The JS syntax check from 3.8-C couldn't catch this, because the HTML was the broken part, not the JS.

Other findings:
- On start the GUI auto-opens the last project, even when that project is **archived**
  (`projects/2026-10-29-…/ARCHIVED.txt`, only `project.json` left). Review & Edit then loads data that
  no longer exists.
- `homeNewProject()` only switches to the wizard tab. The project chip, Review & Edit, Shorts, thumbnails
  and so on keep pointing at the previous project until a new one is created. That is why "Novo projeto"
  looks like the old project.
- The wizard and Settings still have hard-coded Edge-only voice `<select>`s (`quick-voice` /
  `applyQuickVoice`). Since 3.9-A the voice is a **channel-level** choice made in Vozes, so these
  per-project dropdowns are stale and confusing.

## A. Structural guard for the template (tests first)

Add `tests/test_gui_template_structure.py`, using `html.parser` from the standard library with no new
dependency. It parses `gui_templates/index.html` and checks:
- every non-void tag is closed in order (report the line numbers of mismatches);
- every `.tab-content` is a **direct child** of `main.main`;
- no `.tab-content` contains another `.tab-content`;
- each tab button in `.tab` maps to an existing `#tab-<name>`;
- the `names` array in `switchTab` matches the button order. Better: make `switchTab` use
  `data-tab` attributes instead of the index-based array, then test that.
- Ids are unique.

Keep the existing JS syntax check. The test must fail on the old `93074a3` template and pass now.

## B. Voices in the GUI (listen before choosing)

- **Settings → Vozes:**
  - list every available voice with a **▶ play button** (`<audio>` with `preload="none"`) for its preview
    sample, plus the measured stats (RTF, alignment coverage/drift, licence);
  - a "Usar como voz do canal" button (uses the existing `/api/voices/channel`);
  - mark the current channel voice clearly;
  - give the whole panel an obvious entry point: a visible "🎙 Vozes" sub-heading at the top of Settings,
    and a link from the project's Narração stage card ("Voz: bm_george · trocar").
- **"Gerar prévias" button** runs the audition in the background, as a GUI job with progress and errors:
  - text: `briefs/voice-preview.txt` (~20 s, already in the repo); an optional textarea overrides it;
  - voices: a curated catalogue in `audio/tts/catalogue.py`, with a checkbox list in the panel:
    - **Edge:** `en-US-AndrewNeural`, `en-US-BrianNeural`, `en-US-ChristopherNeural`,
      `en-US-GuyNeural`, `en-US-EricNeural`, `en-GB-RyanNeural`, `en-GB-ThomasNeural`;
    - **Kokoro (local):** `am_michael`, `am_adam`, `am_eric`, `am_onyx`, `am_liam`, `bm_george`,
      `bm_fable`, `bm_lewis`, `bm_daniel`, plus `af_heart`, `bf_emma` for female narration;
    - check which ids actually exist in the installed `voices-v1.0.bin` and drop the ones that don't;
    - ElevenLabs only when it is configured.
  - Samples are cached by (backend, voice, text hash) and **skipped when they already exist**.
  - A voice that fails shows its error in its row and doesn't stop the others.
- `scripts/voice_audition.py`:
  - `--text-file` becomes optional (default `briefs/voice-preview.txt`);
  - add `--catalogue` (all catalogue voices) and `--list`;
  - keep `--voices backend:voice,…`;
  - write the HTML listening page (`debug/voice_audition/index.html`) with play buttons as now, and
    print its path at the end.
- **Remove** the per-project Edge `<select>`s (`quick-voice`, the old Settings voice select,
  `applyQuickVoice` and their dead JS/endpoint code). The wizard shows a read-only line instead:
  "Voz do canal: <voz> — trocar em Settings → Vozes".
- **Narration stale warning:** after the channel voice changes, the open project's Narração card shows
  "desatualizado (voz mudou)" (3.9-A already detects it; make it visible here).

**Tests:**
- catalogue filtering against a fake voices file;
- cache skip;
- per-voice error isolation;
- the `--catalogue` and default-text CLI;
- the template has no `quick-voice`;
- `/api/voices` returns play URLs that `/api/voices/sample/<name>` serves.

## C. Clean "Novo projeto" flow and project state

- **Startup:**
  - if the last project is archived or missing, don't open it. Show the home screen with the projects
    list;
  - archived projects appear with a "Restaurar" button (existing restore command, run as a GUI job) and
    can't be opened for editing until restored.
- **"Novo projeto"** (home button or tab) starts a **blank draft state**:
  - the project chip reads "Novo projeto (não criado)";
  - Projeto, Review & Edit and Desempenho show an empty state ("Crie o projeto para começar") instead of
    the previous project's data;
  - the wizard fields reset (premise textarea, type, duration), with an "Inserir modelo" button.
- After "Criar projeto e abrir": switch `config.OUTPUT_DIR` to the new project on the server (one
  function, `projects.activate(slug)`). Reload **every** panel from the new project: stages, review,
  Shorts, thumbnails, titles, variation, review gate. Write the active slug to `data/gui_state.json`.
- Every panel loader gets the current slug from the server response and ignores responses for a
  different slug. This avoids old data arriving late after a project switch.
- A single `refreshAll()` replaces the scattered reload calls.

**Tests (Flask test client):**
- startup with an archived last project → home and no project loaded;
- `/api/projects/new` activates the new project, and `/api/projects/current`, `/api/review-gate` and
  `/api/shorts` reflect it;
- opening an archived project is refused with a restore hint.

## D. Visual audit and fixes

- Run a Playwright screenshot audit (Chromium is available on Windows via `playwright install chromium`).
  Use a **fixture project** in `tests/fixtures/gui_project/` with small images: script, shots,
  thumbnails, Shorts plan.
  - Capture the home, every tab and the main dialogs/viewers at 1440×900 and 1280×720.
  - Save the images to `docs/reports/gui-3.9.1/before/` and, after fixes, `after/`.
- Fix what's out of place:
  - overflow and clipped panels;
  - buttons wrapping badly;
  - misaligned grids;
  - stage cards of different heights;
  - the console placement (it now sits inside the Projeto stages column; give it a consistent place);
  - leftover English/Portuguese label mix in the same panel (use Portuguese for UI labels; English stays
    for story content);
  - empty panels without an empty-state message.
- List every visual fix in the report, with before/after file names.
- Add `tests/test_gui_smoke.py`, a Playwright test that is **skipped automatically** when Playwright or
  Chromium isn't installed. It:
  - loads the GUI against the fixture project;
  - clicks every tab and checks that exactly one `.tab-content` is visible;
  - checks there are no `pageerror` / `console.error` events;
  - checks the Vozes panel renders play buttons.
- Remove dead code found on the way: unused routes, JS functions and CSS classes from the old flow.
  List what was removed.

## E. Report and commands

Report:
- the root causes you confirmed;
- the voice catalogue that actually exists on this machine;
- the screenshot pairs;
- the removed code;
- anything still pending.

```powershell
python -m scripts.voice_audition --catalogue          # all voices, ~20 s each; opens nothing, prints the page path
python gui.py                                         # Settings → Vozes: ▶ ouvir, "Usar como voz do canal"
python -m pytest tests/test_gui_template_structure.py tests/test_gui_smoke.py -q
```
