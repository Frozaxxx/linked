from __future__ import annotations

import re

try:
    from simplemma import lemmatize
except ImportError:  # pragma: no cover - dependency should be installed in runtime
    lemmatize = None


CYRILLIC_RE = re.compile(r"[а-яё]")
LATIN_RE = re.compile(r"[a-z]")
SUPPORTED_LANGUAGES = ("en", "ru")

TECHNICAL_TOKENS = {
    "http",
    "https",
    "www",
    "html",
    "htm",
    "php",
    "asp",
    "aspx",
    "xml",
}


def stem_token(token: str) -> str:
    normalized = token.casefold().replace("ё", "е").strip("-_")
    if len(normalized) < 3 or normalized in TECHNICAL_TOKENS or normalized.isdigit():
        return normalized

    if lemmatize is None:
        return normalized

    language: str | tuple[str, ...]
    if CYRILLIC_RE.search(normalized):
        language = "ru"
    elif LATIN_RE.search(normalized):
        language = "en"
    else:
        language = SUPPORTED_LANGUAGES

    lemma = lemmatize(normalized, lang=language)
    if not lemma or len(lemma) < 3:
        return normalized
    return lemma.casefold().replace("ё", "е")
