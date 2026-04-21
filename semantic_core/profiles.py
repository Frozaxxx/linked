from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import simplemma

from models import Page

from .constants import (
    DOMAIN_GENERIC_TOKENS,
    LOCATION_TOKENS,
    MARKETPLACE_GENERIC_TOKENS,
    PRODUCT_MODEL_CUES,
    STOP_WORDS,
    TOKEN_RE,
    WEAK_STRUCTURE_TOKENS,
)


@dataclass(frozen=True)
class TokenProfile:
    topical: set[str]
    generic: set[str]
    location: set[str]
    weak: set[str]
    model_like: set[str]

    @property
    def strong(self) -> set[str]:
        return self.topical | self.model_like

    @property
    def all(self) -> set[str]:
        return self.topical | self.generic | self.location | self.weak | self.model_like


def page_token_profile(page: Page | None, url: str) -> TokenProfile:
    domain_generics = domain_tokens(url)
    profiles = [token_profile_from_text(slug_text(url), domain_generics=domain_generics, keep_numbers=True)]
    if page is not None:
        profiles.extend(
            [
                token_profile_from_text(page.title, domain_generics=domain_generics, keep_numbers=True),
                token_profile_from_text(page.h1, domain_generics=domain_generics, keep_numbers=True),
                token_profile_from_text(" ".join(page.breadcrumbs), domain_generics=domain_generics, keep_numbers=True),
            ]
        )
        if not (page.title or page.h1):
            profiles.append(token_profile_from_text(page.text, domain_generics=domain_generics, keep_numbers=False))
    return merge_profiles(profiles)


def branch_token_profile(url: str) -> TokenProfile:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    return token_profile_from_text(" ".join(parts[:-1]), domain_generics=domain_tokens(url), keep_numbers=True)


def token_profile_from_text(value: str, *, domain_generics: set[str], keep_numbers: bool) -> TokenProfile:
    normalized = unquote(value).replace("ё", "е").replace("Ё", "Е").casefold()
    raw_tokens = TOKEN_RE.findall(normalized)
    lemmas = [normalize_token(raw_token, keep_numbers=keep_numbers) for raw_token in raw_tokens]
    lemmas = [token for token in lemmas if token]
    context = set(lemmas)

    topical: set[str] = set()
    generic: set[str] = set()
    location: set[str] = set()
    weak: set[str] = set()
    model_like: set[str] = set()

    for token in lemmas:
        if token in STOP_WORDS:
            continue
        if is_context_model_token(token, context, keep_numbers=keep_numbers):
            model_like.add(token)
            continue
        if token in domain_generics or token in DOMAIN_GENERIC_TOKENS or token in MARKETPLACE_GENERIC_TOKENS:
            generic.add(token)
            continue
        if token in LOCATION_TOKENS:
            location.add(token)
            continue
        if token in WEAK_STRUCTURE_TOKENS:
            weak.add(token)
            continue
        topical.add(token)

    return TokenProfile(
        topical=topical,
        generic=generic,
        location=location,
        weak=weak,
        model_like=model_like,
    )


def merge_profiles(profiles: list[TokenProfile]) -> TokenProfile:
    topical: set[str] = set()
    generic: set[str] = set()
    location: set[str] = set()
    weak: set[str] = set()
    model_like: set[str] = set()
    for profile in profiles:
        topical.update(profile.topical)
        generic.update(profile.generic)
        location.update(profile.location)
        weak.update(profile.weak)
        model_like.update(profile.model_like)
    return TokenProfile(topical=topical, generic=generic, location=location, weak=weak, model_like=model_like)


def tokenize(value: str) -> list[str]:
    profile = token_profile_from_text(value, domain_generics=set(), keep_numbers=True)
    return sorted(profile.strong)


def normalize_token(token: str, *, keep_numbers: bool) -> str:
    token = token.casefold()
    if token.isdigit():
        if keep_numbers and 1 <= len(token) <= 4:
            return token
        return ""
    if len(token) < 2:
        return ""
    if re.search(r"[a-z]", token):
        return simplemma.lemmatize(token, lang="en").casefold()
    if re.search(r"[\u0400-\u04FF]", token):
        return simplemma.lemmatize(token, lang="ru").casefold()
    return token


def is_context_model_token(token: str, context: set[str], *, keep_numbers: bool) -> bool:
    if token in PRODUCT_MODEL_CUES:
        return True
    if re.fullmatch(r"[a-z]{1,4}\d{1,4}[a-z]{0,4}", token):
        return True
    if re.fullmatch(r"\d{2,4}(gb|гб|tb|тб)", token):
        return True
    if token.isdigit() and keep_numbers and context & PRODUCT_MODEL_CUES:
        return True
    if token in {"pro", "max", "mini", "plus", "ultra"} and context & PRODUCT_MODEL_CUES:
        return True
    return False


def slug_text(url: str) -> str:
    path = urlsplit(url).path
    return re.sub(r"[-_/.,+]+", " ", path)


def domain_tokens(url: str) -> set[str]:
    host = (urlsplit(url).hostname or "").casefold()
    tokens = set()
    for part in re.split(r"[^0-9a-zа-яё]+", host):
        if part and part not in {"www", "ru", "com", "net", "org"}:
            normalized = normalize_token(part, keep_numbers=False)
            if normalized:
                tokens.add(normalized)
    return tokens
