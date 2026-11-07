"""Normalizing track titles and artist names so the same song matches across services."""

import re
import unicodedata

# Words that mark a version suffix rather than part of the song's name, e.g.
# "Pop Song 89 - Remastered 2013" or "Mrs. Robinson (2022 Remastered Edition)".
_VERSION_WORDS = (
    r"remaster(?:ed)?|live|version|edit|mix|mono|stereo|demo|single|radio|bonus|deluxe|"
    r"anniversary|acoustic|instrumental|session|take|recorded|from|feat\.?|featuring|\d{4}"
)
_DASH_SUFFIX = re.compile(rf"\s+-\s+(?=[^-]*\b(?:{_VERSION_WORDS})\b).*$", re.IGNORECASE)
_BRACKETED = re.compile(rf"\s*[\(\[][^\)\]]*\b(?:{_VERSION_WORDS})\b[^\)\]]*[\)\]]", re.IGNORECASE)


def clean_title(title):
    """Strip version suffixes: "Alex Chilton - 2008 Remaster" -> "Alex Chilton"."""
    title = title or ""
    cleaned = _BRACKETED.sub("", _DASH_SUFFIX.sub("", title)).strip()
    return cleaned or title.strip()


def fold(text, drop_article=True):
    """Lowercase, strip accents and punctuation, collapse whitespace: for comparisons only.
    A leading "the" is dropped too, which suits artist names ("The Replacements") but not
    titles ("The Wall" is not "Wall"); pass drop_article=False for those."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    if drop_article:
        text = re.sub(r"^the\s+", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def track_key(artist, title):
    """Identity of a song independent of service or remaster: "replacements|alex chilton"."""
    return f"{fold(artist)}|{fold(clean_title(title), drop_article=False)}"


def same_song(artist_a, title_a, artist_b, title_b):
    return track_key(artist_a, title_a) == track_key(artist_b, title_b)
