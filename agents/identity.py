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
