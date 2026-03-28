"""Utilities for normalizing user-provided profile asset names."""

from __future__ import annotations

import re

_SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_NON_SAFE_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_WHITESPACE_RE = re.compile(r"\s+")
_DASHES_RE = re.compile(r"-{2,}")

_CYRILLIC_TO_LATIN: dict[str, str] = {
    "\u0430": "a",  # а
    "\u0431": "b",  # б
    "\u0432": "v",  # в
    "\u0433": "g",  # г
    "\u0434": "d",  # д
    "\u0435": "e",  # е
    "\u0451": "e",  # ё
    "\u0436": "zh",  # ж
    "\u0437": "z",  # з
    "\u0438": "i",  # и
    "\u0439": "y",  # й
    "\u043a": "k",  # к
    "\u043b": "l",  # л
    "\u043c": "m",  # м
    "\u043d": "n",  # н
    "\u043e": "o",  # о
    "\u043f": "p",  # п
    "\u0440": "r",  # р
    "\u0441": "s",  # с
    "\u0442": "t",  # т
    "\u0443": "u",  # у
    "\u0444": "f",  # ф
    "\u0445": "h",  # х
    "\u0446": "ts",  # ц
    "\u0447": "ch",  # ч
    "\u0448": "sh",  # ш
    "\u0449": "sch",  # щ
    "\u044b": "y",  # ы
    "\u044d": "e",  # э
    "\u044e": "yu",  # ю
    "\u044f": "ya",  # я
    "\u044a": "",  # ъ
    "\u044c": "",  # ь
}


def normalize_runtime_name(raw_name: str, *, max_length: int = 128) -> str:
    """Normalize user-provided labels to a runtime-safe slug.

    The result is constrained to the skill/subagent naming pattern:
    `^[a-z0-9][a-z0-9-]*$`.
    """

    lowered = raw_name.strip().lower()
    if not lowered:
        raise ValueError(f"Invalid name: {raw_name}")

    transliterated_parts: list[str] = []
    for char in lowered:
        transliterated_parts.append(_CYRILLIC_TO_LATIN.get(char, char))
    candidate = "".join(transliterated_parts)

    candidate = candidate.replace("_", "-")
    candidate = _WHITESPACE_RE.sub("-", candidate)
    candidate = _NON_SAFE_CHARS_RE.sub("", candidate)
    candidate = _DASHES_RE.sub("-", candidate).strip("-")
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip("-")

    if not _SAFE_NAME_RE.match(candidate):
        raise ValueError(f"Invalid name: {raw_name}")
    return candidate
