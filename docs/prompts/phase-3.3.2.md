# Private Gravity — Phase 3.3.2

**Identity-only references: clean visual bible descriptors and neutral character sheets**

> Implementation prompt for Claude Code. Continue on the current branch. Report in **Portuguese**.
> No live calls in tests. Never push. One commit.

## Evidence (project `2026-10-21-the-man-who-needed-everyone-to-envy-him`)

All shots are 16:9 now, but Elena still shows bloody marks on her forearm. Root cause is upstream:

- `visual_bible.json` → `elena_vance.descriptor`: "*weary* expressive dark eyes… hair loosely pinned
  in a low knot *with escaping wisps*" — emotional state and grooming baked into the immutable
  descriptor (she looks dishevelled and sad in every shot).
- `wardrobe` for s01–s02: "… bare hands with *faint traces of dark soil on knuckles*" (from the s02
  potting-soil moment). FLUX renders it as **bloody wounds**, and it reached the approved portrait.
- `portrait_prompt` uses the first scene's wardrobe plus the full environment style suffix, so the
  approved portraits contain a seated pose, staircase, smoke and clothing. The reference then
  dominates: shots s01_03 and s01_05 are near copies of the portrait (same pose, bench, background),
  and Elena wears the olive tunic in every scene instead of the per-scene wardrobe (ivory silk shirt
  from s03).
- Separate issue: s01_01 has a man in a suit in the background; s01_04 (over-the-shoulder) shows an
  extra woman seen from behind — a third person in a scene with only Julian and Elena.

## Changes

1. **Visual bible contract** (prompt + validator):
   - `descriptor` = immutable physical identity only: apparent age, ethnicity, build, face shape,
     eye colour, hair colour/length and default style, distinguishing features. **No emotions, moods,
     states, grooming disorder, injuries, dirt or story details.** Validator rejects mood/state words
     (`weary, tired, sad, tearful, anxious, dishevelled, messy, escaping wisps, bruised, scarred` unless
     the screenplay establishes a permanent scar).
   - `wardrobe[].outfit` = clothing and accessories only. **No skin, hands, dirt, stains, marks or
     body states.** Validator rejects `soil, dirt, stain, blood, mark, bruise, scratch, wound, skin`
     inside outfits. Story-state details (soil on hands while repotting) belong only to the shot whose
     narration contains them, phrased concretely ("a smudge of dark potting soil on her fingertips")
     — never "traces/marks on knuckles".
2. **Neutral character sheets** (`portrait_prompt`): head-and-shoulders, facing the viewer and a
   three-quarter variant, **plain flat dark-grey backdrop with no architecture, smoke or props**,
   neutral relaxed expression, simple plain dark top (not scene wardrobe), even soft key light, same
   drawing technique (use a style suffix variant *without* environment/atmosphere clauses). Purpose:
   the reference carries identity, not pose, clothing or setting.
3. **Shots apply wardrobe and state**: every character shot prompt states the scene's outfit and the
   grooming/expression for that moment (from 3.3-B). Add a test that s03+ shots for Elena contain the
   ivory silk shirt and none contain the tunic.
4. **Extra people**: verify the composed prompt for every shot with `others_present=false` includes
   `alone_hint(len(characters))`; over-the-shoulder shots must name whose shoulder it is (a listed
   character) and the other listed character facing camera. Tests for both.
5. **Invalidate and regenerate**: bump the visual-bible contract version and the portrait-prompt
   version so the visual bible (1 Gemini call) and **all** character references regenerate and require
   re-approval (archive the old ones); location references stay valid; shots regenerate because the
   reference hashes change.

## User commands afterwards

```powershell
python -m scripts.generate_character_refs        # rebuilds the visual bible, 4 neutral sheets per character
# approve one per character in the GUI (Review tab)
python -m scripts.generate_shots --limit 5
```
