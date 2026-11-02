"""Relevance, tone and text checks for stock candidates (stock is the fallback provider).

Long narrative visual descriptions make providers return popular, loosely
matching images. These helpers turn a description into short concrete
queries, score each candidate's tags against them, reject bright or
saturated images that clash with the channel's dark palette, reject likely
text/signage, and keep identifiable people out of shots that need a specific
character (stock cannot preserve identity).
"""
from __future__ import annotations

import re

import config

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "into", "onto", "from",
    "with", "without", "by", "for", "as", "is", "are", "was", "were", "be", "being", "been",
    "his", "her", "their", "its", "he", "she", "they", "it", "him", "them", "this", "that",
    "these", "those", "while", "who", "whose", "which", "where", "when", "over", "under",
    "through", "across", "toward", "towards", "near", "beside", "behind", "after", "before",
    "very", "slowly", "quietly", "softly", "only", "just", "still", "then", "there", "here",
    "one", "another", "each", "every", "some", "any", "all", "own", "looking", "showing",
    "shot", "scene", "frame", "image", "view", "cinematic", "moody", "camera",
    "close", "off", "away", "down", "up", "out",
}
# Words that describe a moment rather than something a photo can show.
_ABSTRACT = {
    "sense", "feeling", "moment", "mood", "tension", "atmosphere", "emotion", "silence",
    "anxiety", "envy", "regret", "realization", "realisation", "meaning", "truth",
}
PERSON_TERMS = {
    "man", "men", "woman", "women", "boy", "girl", "person", "people", "face", "portrait",
    "couple", "family", "crowd", "human", "husband", "wife", "father", "mother", "son",
    "daughter", "friend", "guest", "guests", "businessman", "businesswoman", "groom", "bride",
    "smiling", "smile", "model", "lady", "guy",
}
# People are acceptable when only these parts or forms are shown.
IDENTITY_SAFE_TERMS = {"hand", "hands", "silhouette", "shadow", "back", "fingers", "feet",
                       "legs", "anonymous", "faceless", "blurred"}
TEXT_TERMS = {"sign", "signage", "text", "quote", "quotes", "typography", "wallpaper", "letter",
              "letters", "word", "words", "font", "logo", "banner", "poster", "label",
              "lettering", "message", "inscription", "caption"}


TONE_CLASH_TERMS = {"fun", "enjoyment", "cheers", "celebration", "celebrate", "party", "happy",
                    "happiness", "joy", "joyful", "beach", "summer", "holiday", "vacation",
                    "laughing", "laugh", "valentines", "wedding", "birthday", "festive"}


def stem(word: str) -> str:
    """Tiny English stemmer: plurals first (glasses/glass, tables/table), then -ing/-ed/-ly."""
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "ches", "shes", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    for suffix in ("ing", "ed", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def terms(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z]+", str(text or "").lower())]


def content_terms(description: str, names=()) -> list[str]:
    """Concrete nouns/adjectives in order, without stopwords, names or abstractions."""
    blocked = {name.lower() for full in names for name in str(full).split()}
    seen, result = set(), []
    for word in terms(description):
        if (len(word) < 3 or word in _STOPWORDS or word in _ABSTRACT or word in blocked
                or word.endswith("ly")):
            continue
        if word not in seen:
            seen.add(word)
            result.append(word)
    # Leading gerunds are the action ("adjusting", "sitting"), not something to search for.
    while len(result) > 1 and result[0].endswith("ing"):
        result.pop(0)
    return result


def is_character_shot(description: str, names=()) -> bool:
    words = set(terms(description))
    first_names = {str(name).split()[0].lower() for name in names if str(name).strip()}
    return bool(words & PERSON_TERMS or words & first_names)


def concise_queries(description: str, names=(), *, max_terms: int = 5, variants: int = 3) -> list[str]:
    """2-3 short search queries (3-5 concrete words) from a long visual description."""
    words = content_terms(description, names)
    if is_character_shot(description, names):
        # Stock cannot show *this* character: search for objects, hands and places.
        words = [word for word in words if word not in PERSON_TERMS] or ["silhouette"]
    candidates = [
        " ".join(words[:max_terms]),
        " ".join(words[-4:]) if len(words) > 4 else "",
        " ".join(words[:3]),
    ]
    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique and len(candidate.split()) <= max_terms:
            unique.append(candidate)
    return unique[:variants] or [" ".join(terms(description)[:max_terms])]


def relevance(query: str, tags: str, title: str = "") -> float:
    """Share of query terms present (stemmed) in the candidate's tags and title."""
    wanted = {stem(word) for word in terms(query)}
    if not wanted:
        return 0.0
    have = {stem(word) for word in terms(f"{tags} {title}")}
    return len(wanted & have) / len(wanted)


def tag_rejection(tags: str, title: str = "", *, character_shot: bool = False) -> str | None:
    """Reason to skip a candidate before downloading it, or None."""
    words = set(terms(f"{tags} {title}"))
    if words & TEXT_TERMS:
        return "text/signage tags: " + ", ".join(sorted(words & TEXT_TERMS))
    clash = words & set(getattr(config, "STOCK_TONE_CLASH_TAGS", TONE_CLASH_TERMS))
    if clash and getattr(config, "STOCK_TONE_FILTER", True):
        return "cheerful tags clash with the channel tone: " + ", ".join(sorted(clash))
    if character_shot and words & PERSON_TERMS and not words & IDENTITY_SAFE_TERMS:
        return "identifiable person in a character shot: " + ", ".join(sorted(words & PERSON_TERMS))
    return None


def tone(image) -> dict:
    """Mean luminance and saturation (0-255) of a PIL image."""
    from PIL import ImageStat

    small = image.convert("RGB").resize((96, 54))
    luminance = ImageStat.Stat(small.convert("L")).mean[0]
    saturation = ImageStat.Stat(small.convert("HSV")).mean[1]
    return {"luminance": round(luminance, 1), "saturation": round(saturation, 1)}


def tone_rejection(measured: dict) -> str | None:
    if not getattr(config, "STOCK_TONE_FILTER", True):
        return None
    max_luma = float(getattr(config, "STOCK_MAX_LUMINANCE", 150))
    max_sat = float(getattr(config, "STOCK_MAX_SATURATION", 140))
    if measured["luminance"] > max_luma:
        return f"too bright for the channel palette (luminance {measured['luminance']} > {max_luma:g})"
    # Saturated colour is fine in low light (a candle flame); bright *and*
    # saturated reads as cheerful.
    if measured["saturation"] > max_sat and measured["luminance"] > 90:
        return (f"bright and saturated for the channel palette (saturation "
                f"{measured['saturation']} > {max_sat:g}, luminance {measured['luminance']})")
    return None


def text_likelihood(image) -> float:
    """Cheap horizontal-stroke heuristic: text forms dense edge bands separated by empty rows.

    Returns a 0-1 score; photographs (smooth or uniformly textured) score low.
    """
    gray = image.convert("L").resize((256, 144))
    width, height = gray.size
    data = gray.tobytes()
    densities = []
    for row in range(height):
        values = data[row * width:(row + 1) * width]
        edges = sum(1 for a, b in zip(values, values[1:]) if abs(a - b) > 60)
        densities.append(edges / (width - 1))
    dense = [value > 0.10 for value in densities]
    empty = sum(1 for value in densities if value < 0.01) / height
    bands, previous = 0, False
    for flag in dense:
        if flag and not previous:
            bands += 1
        previous = flag
    dense_share = sum(dense) / height
    if bands < 2 or empty < 0.25 or not 0.04 <= dense_share <= 0.7:
        return 0.0
    return round(min(1.0, bands / 6) * min(1.0, empty / 0.5), 2)


def text_rejection(image) -> str | None:
    score = text_likelihood(image)
    if score >= float(getattr(config, "STOCK_TEXT_THRESHOLD", 0.4)):
        return f"probable text/signage in the image (score {score})"
    return None
