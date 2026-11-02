# Private Gravity — Phase 3.5.2

**Shot review UX: large viewer, cleaner grid, less noise**

> Implementation prompt for Claude Code. Continue on the current branch. Report in **Portuguese**.
> No live calls. Never push. One commit per section. Test the GUI endpoints with the Flask test client and
> keep the JS syntax-checked (`node --check` on extracted scripts).

## 0. Evidence (user screenshot of the Shots grid)

- Thumbnails are ~230 px wide: impossible to judge glasses, hands, duplicated faces or marks.
- Every card lists several `(unclear)` hints (missing_marker, unstated_injury, text) — noise on almost all
  shots, burying the real warnings.
- Duplicated actions: the old `new seed` / `edit` buttons coexist with the new `Gerar de novo` /
  `Instrução livre` set; ~10 buttons per card make the grid very tall.

## A. Full-size shot viewer (lightbox)

- Clicking a card (or pressing Enter on the focused card) opens a viewer that fits the shot to the window
  (full-resolution file, cache-busted after edits), with:
  - header: shot id, time range, shot type, scene; the **narration excerpt** for that moment;
  - side panel: characters present with their **approved reference portraits** (small) for identity
    comparison, QA hints (real warnings first), the composed prompt (collapsible), version history with
    before/after toggle;
  - overlay toggle for detector boxes and a box-drawing mode used by "Remover figura", "Adicionar
    marcador", "Remover marcas", "Instrução livre" (draw/adjust a box directly on the large image; boxes
    stored in normalized coordinates);
  - zoom: fit / 100 % / mouse-wheel zoom with pan.
- Keyboard: ← / → previous/next shot, `O` mark OK, `G` regenerate, `I` free instruction, `F` remove
  figure, `M` add marker, `B` toggle boxes, `Z` zoom fit/100 %, `H` history, `Esc` close. Show the
  shortcuts in a small legend.
- Edits requested in the viewer join the existing batch queue; the viewer shows "na fila / processando /
  pronto" and refreshes the image when done.

## B. Cleaner grid

- Thumbnail size control (S / M / L, e.g. 240 / 360 / 520 px wide), remembered in localStorage (wrapped in
  try/catch).
- Each card shows only: image, shot id + time + type, a status badge (`OK`, `n avisos`, `revisado`,
  `editado`, `na fila`) and at most the first real warning in one line. All actions live in the viewer.
- Remove the legacy `new seed` / `edit` buttons (map them to `Gerar de novo` / `Instrução livre`).
- Filter bar: Todos · Com avisos · Não revisados · Editados · OK; group by scene with scene headers;
  progress counter "34/66 revisados".

## C. Less noise from QA

- `unclear` answers are hidden by default (toggle "mostrar incertos"); never counted as warnings.
- Collapse repeated hints of the same type on a card into one line.
- Show the QA backend and date per shot in the viewer only.

## D. User check

```powershell
python gui.py   # Continuar projeto → Shots → click a shot → review with ← →, draw boxes, queue edits
```
