"""Private Gravity editorial identity: the single source injected into prompts.

The researcher, outline architect, scene writer and editorial reviewer all
receive text built here, so the channel promise, episode types, Type A
movements, craft rules and title policy are stated once.
"""
from __future__ import annotations

CHANNEL_PROMISE = (
    "Private Gravity is an English-language anthology of original cinematic fiction in which "
    "psychological and philosophical ideas emerge through character choice, consequence, image "
    "and subtext — never lectures, explainers, listicles or self-help. Atmospheric, mature and "
    "accessible: standalone stories about the hidden forces that shape human behaviour."
)

EPISODE_TYPES = {
    "realistic_psychological_fiction": (
        "Type A — Realistic Psychological Fiction. A realistic story built on the seven "
        "movements below. They are guidance, not a rigid schema: the outline should normally "
        "express them, but may merge, reorder or compress them when the story demands."
    ),
    "philosophical_thought_experiment": (
        "Type B — Philosophical Thought Experiment. One speculative rule is introduced early and "
        "applied consistently; the story tests the rule through a character's choices; "
        "consequences escalate logically; the philosophical question is felt through outcomes. "
        "Flexible structure."
    ),
    "dark_human_parable": (
        "Type C — Dark Human Parable. Compact and fable-like, in a timeless or stylised setting; "
        "a clear moral mechanism without ever stating a moral; an ironic or inverted ending. "
        "Flexible structure."
    ),
}

# Canonical values for the optional outline field ``arc_movements``.
ARC_MOVEMENTS = (
    "hook", "character_wound", "mechanism", "escalation_success",
    "hidden_cost", "reversal", "philosophical_layer", "ending",
)

TYPE_A_MOVEMENTS = (
    ("hook", "Narrative hook (about the first 30 seconds / first 60-75 narrated words). Open on a "
     "situation, not an explanation or atmosphere. Establish who, a striking fact, and an open "
     "question. Model: \"Daniel had spent twenty years trying to become the kind of man people "
     "noticed. At forty-two, he finally succeeded. That was when his life began to fall apart.\""),
    ("character_wound", "Character construction. Show the desire AND the wound behind it through "
     "concrete past or present detail (e.g. a father treated as irrelevant -> the belief that "
     "respect must be bought). The wound must appear in the screenplay, not only in the bible."),
    ("mechanism", "The mechanism. The character makes choices consistent with an inner belief "
     "they cannot see. The viewer should start recognising the pattern in themselves."),
    ("escalation_success", "Escalation — the strategy works first. The status-seeker gains "
     "status; the workaholic earns; the avoider avoids pain. Early success makes the story better. "
     "Never begin with the protagonist already ruined."),
    ("hidden_cost", "The hidden cost. The tragedy lives in the cost of the strategy, revealed "
     "progressively through consequences."),
    ("reversal", "Reversal. An event — ideally caused by the character's own choices, not a "
     "convenient discovery — forces confrontation with the governing belief. Usually the most "
     "shareable moment."),
    ("philosophical_layer", "Philosophical layer. Only after the reversal, briefly, the meaning "
     "surfaces more explicitly. A thinker or concept (Jung, Kierkegaard, Nietzsche, Becker, Fromm, "
     "Girard, social psychology) may be evoked in one or two sentences when it genuinely fits "
     "— never a lecture, never the protagonist of the episode."),
    ("ending", "Ending. No moral of the story. Prefer partially open endings that leave an "
     "uncomfortable question. The protagonist makes a final choice or reaches a recognition that "
     "reveals something new — not merely suffering the expected consequence."),
)

STACKED_MODIFIER_RULE = (
    "Avoid stacking two or more adjectives, or an adverb + adjective, on one noun for emphasis "
    "(\"pure, unmitigated estrangement\", \"devastating, unhurried calm\"); prefer one precise "
    "word or a concrete action; never write intensifier pairs."
)

CRAFT_RULES = (
    "Dramatize with direct, specific dialogue and subtext.",
    STACKED_MODIFIER_RULE,
    "Vary rhythm between description, action, dialogue, plain observation and silence.",
    "Avoid repeating the same sensory vocabulary (an earlier draft overused glass, heavy, stone, "
    "polished, silence, shadow).",
    "Avoid ambiguous poetic phrasing that misleads a listener (an earlier draft wrote \"the glass "
    "shatters with a silent pulse\" when only a phone vibrated).",
    "Supporting characters need independent agency and contradictions.",
    "Ages, relationships and objects must stay consistent with the Character Bible.",
)

TITLE_POLICY = """TITLE POLICY: the title must NOT explain the thesis. It presents a person and a
situation that creates curiosity; the audience discovers the psychological theme while watching.
- Reject: "The Psychology of Envy: A Story About Social Comparison", "The Psychology of People
  Pleasing", "Why Success Doesn't Make You Happy", "The Architecture of Envy", "The Poison of Being
  Envied", "The Cost of Being Looked At".
- Target: "He Secretly Wanted His Best Friend to Fail", "She Said Yes to Everyone for 20 Years",
  "He Got Everything He Wanted at 37", "The Man Who Needed Everyone to Envy Him".
- Never use: "psychology of", "science of", "philosophy of", "a story about", "explained",
  "the truth about", "why", "how", "lesson", "what happens when", a colon subtitle, the abstract
  form "The <Noun> of ...", or the psychological theme word (or its derivatives) unless it is inside
  a character's quoted line. At most 70 characters and 12 words.
- Anchor it in a concrete human: a person or role, a pronoun, an age, a number, a time span or a
  quoted line.
THUMBNAIL TEXT pairs with the title: at most 5 words, often a line of inner voice ("I hope he
loses.", "Now what?"), no theme word, no hashtags."""


def identity_block(narrative_type: str | None = None, *, include_movements: bool = True) -> str:
    """Return the channel identity text for one narrative type (or all types)."""
    lines = ["PRIVATE GRAVITY IDENTITY", CHANNEL_PROMISE, "", "EPISODE TYPES:"]
    types = [narrative_type] if narrative_type in EPISODE_TYPES else list(EPISODE_TYPES)
    lines += [f"- {EPISODE_TYPES[name]}" for name in types]
    if include_movements and "realistic_psychological_fiction" in types:
        lines += ["", "TYPE A MOVEMENTS (guidance; arc_movements value in brackets):"]
        lines += [f"{index}. [{key}] {text}"
                  for index, (key, text) in enumerate(TYPE_A_MOVEMENTS, 1)]
    lines += ["", "CRAFT RULES (all types):"] + [f"- {rule}" for rule in CRAFT_RULES]
    return "\n".join(lines)


# ── Visual identity ─────────────────────────────────────────────────────────
# Single source of truth for the image style. Bump STYLE_VERSION whenever the
# wording changes: the style hash is part of every image cache key and of every
# approved reference, so a new style never reuses old images.
STYLE_VERSION = 2   # 2: full-bleed single image, stronger engraving dread (Phase 3.2.1)
VISUAL_STYLES = ("dark_illustration", "cinematic_photo")
STYLE_VARIANTS = {
    "engraving": "scratchboard and engraved line work dominant, dense hatching and cross-hatching, "
                 "minimal tonal wash; deep black negative space dominating most of the frame, a single "
                 "small source of warm light, figures small within towering architecture unless the "
                 "shot is a close-up, heavy shadows swallowing the edges of the room",
    "charcoal_gouache": "painterly charcoal and dry-brush gouache masses, softer edges, visible "
                        "dry-brush texture, little line work",
    "hybrid": "engraved ink line work and cross-hatching laid over painterly charcoal and gouache "
              "washes",
}
# Brand colours used by the illustration palette, by config.COLORS key.
_PALETTE_ROLES = (
    ("background", "Midnight Ink", "dominant"),
    ("primary", "Fog Blue", "midtones"),
    ("white", "Warm Ivory", "highlights"),
)
_ACCENT_ROLES = (("highlight", "Antique Gold"), ("accent", "Muted Copper"))
_PUNCTUATION = ("red", "Dusty Crimson")

DARK_ILLUSTRATION = {
    "medium": "dark hand-drawn illustration: ink drawing with scratchboard and engraving-like "
              "hatching and cross-hatching, charcoal and dry-brush gouache washes, visible paper "
              "grain, hand-drawn imperfection, painterly smoke and fog; illustrated, not photographic",
    # Worded positively: with zeroed negative conditioning, naming "monsters" or
    # "creatures" here would invite them.
    "atmosphere": "cosmic dread carried only by space and light: oppressive scale with small figures "
                  "against vast rooms, towering doorways and endless staircases, deep pooled shadows, "
                  "a single dominant light source, smoke and fog tendrils and shadows that suggest an "
                  "unseen presence and stay formless, subtly wrong geometry (corridors too long, "
                  "ceilings lost in darkness), stillness, isolation",
    "people": "stylised but consistent faces with gaunt planes, long shadows and expressive hands",
    "exclusions": ("tentacles", "monsters", "creatures", "eldritch beings", "giant eyes", "gore",
                   "occult symbols", "text", "letters", "watermark", "signature", "photorealism",
                   "3D render", "glossy CGI", "anime", "cartoon", "bright saturated colour"),
}
CINEMATIC_PHOTO = {
    "medium": "cinematic still, low-key lighting, muted palette, 35 mm film grain, shallow depth of "
              "field, photorealistic",
    "exclusions": ("text", "letters", "watermark", "signature"),
}


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in rgb)


def _color(key: str) -> str:
    import config
    return _hex(getattr(config, "COLORS", {}).get(key, (0, 0, 0)))


def visual_style() -> str:
    import config
    style = str(getattr(config, "VISUAL_STYLE", "dark_illustration"))
    return style if style in VISUAL_STYLES else "dark_illustration"


def style_variant() -> str:
    import config
    variant = str(getattr(config, "VISUAL_STYLE_VARIANT", "engraving"))
    return variant if variant in STYLE_VARIANTS else "engraving"


def is_illustration() -> bool:
    return visual_style() == "dark_illustration"


def palette_text() -> str:
    """Palette rule built from config.COLORS (hex values are never hard-coded here)."""
    base = ", ".join(f"{name} {_color(key)} ({role})" for key, name, role in _PALETTE_ROLES)
    accents = " or ".join(f"{name} {_color(key)}" for key, name in _ACCENT_ROLES)
    key, name = _PUNCTUATION
    return (f"near-monochrome duotone palette: {base}; a single warm accent per image, {accents}, "
            f"only on the light source or the story's focal object; {name} {_color(key)} only for "
            "rare emotional punctuation (wine, a wound, a ring)")


def negative_terms(style: str | None = None) -> tuple:
    style = style or visual_style()
    return (DARK_ILLUSTRATION if style == "dark_illustration" else CINEMATIC_PHOTO)["exclusions"]


def style_suffix(style: str | None = None, variant: str | None = None) -> str:
    """Appended to every image prompt.

    The FLUX graphs zero out the negative conditioning (cfg 1), so naming the
    exclusions here would *invite* them ("tentacles" in a prompt tends to draw
    tentacles). Exclusions are rules for the prompt writers (``image_prompt_rules``)
    and are checked in code (``forbidden_terms``); the suffix stays positive.
    """
    style = style or visual_style()
    if style == "cinematic_photo":
        return f"{CINEMATIC_PHOTO['medium']}, no lettering, no watermark"
    variant = variant or style_variant()
    spec = DARK_ILLUSTRATION
    return (f"{FULL_BLEED}. {spec['medium']}; {STYLE_VARIANTS[variant]}. {spec['atmosphere']}. "
            f"{spec['people']}. {palette_text()}. Clean image without lettering or watermark.")


# Every illustration is one picture covering the whole 16:9 frame (no paper
# margins, no comic pages); phrased positively because the negative is zeroed.
FULL_BLEED = ("single full-bleed illustration filling the entire frame edge to edge, ink and "
              "hatching reaching every border")
# Words that ask for page layouts; rejected in shot descriptions (not in the suffix,
# which itself says "every border").
# Only phrases that mean a page layout: bare "panel"/"border" are legitimate architecture and light.
LAYOUT_TERMS = ("comic panel", "comic panels", "multiple panels", "panel layout", "split panel",
                "split panels", "split-screen", "grid of images", "triptych", "diptych", "comic page",
                "storyboard", "white border", "decorative border", "border around", "torn paper",
                "paper edge", "frame around")
CROWD_RULE = ("When a shot contains more than one person who is not a named character, give each a "
              "clearly different age, build, posture and clothing; prefer a few distant, indistinct "
              "figures in shadow over several detailed ones.")


def layout_terms(text: str) -> list[str]:
    """Page-layout requests in a shot description (borders, panels, storyboard...)."""
    import re
    lowered = str(text or "").lower()
    return [term for term in LAYOUT_TERMS if re.search(rf"\b{re.escape(term)}\b", lowered)]


def image_prompt_rules(style: str | None = None) -> str:
    """Rules for Gemini when it writes visual descriptions and shot actions."""
    style = style or visual_style()
    if style == "cinematic_photo":
        return "Describe photographic, realistic scenes. Never include text, signs or screens with legible writing."
    return ("Describe scenes for a dark hand-drawn illustration with cosmic dread but NO creatures: "
            "suggest a presence only through shadow, fog, scale and wrong geometry. Never mention or "
            "imply: " + ", ".join(DARK_ILLUSTRATION["exclusions"]) + ". Never include text, signs or "
            "screens with legible writing. Each image is ONE single full-frame picture: never request "
            "comic panels, page layouts, borders around the image, paper edges or multiple views. "
            + CROWD_RULE)


def forbidden_terms(text: str, style: str | None = None) -> list[str]:
    """Excluded subjects that slipped into a prompt (checked before generation)."""
    import re
    lowered = str(text or "").lower()
    terms = [term for term in negative_terms(style)
             if term not in {"text", "letters", "photorealism", "bright saturated colour"}]
    return [term for term in terms if re.search(rf"\b{re.escape(term.lower())}\b", lowered)]


def image_noun(style: str | None = None) -> str:
    """'illustration' or 'photograph' for the generation-request voice."""
    return "photograph" if (style or visual_style()) == "cinematic_photo" else "illustration"


def style_hash(style: str | None = None, variant: str | None = None) -> str:
    import hashlib
    style = style or visual_style()
    payload = f"{STYLE_VERSION}|{style}|{variant or style_variant() if style == 'dark_illustration' else ''}|" \
              f"{style_suffix(style, variant)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
