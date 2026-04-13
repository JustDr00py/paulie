"""
filters.py — Post-transcription text filters.

Currently provides filler-word removal.  All filters are pure string
transformations — no models, no I/O, instant execution.
"""

from __future__ import annotations

import re

# ── Filler-word filter ─────────────────────────────────────────────────────────

# Matches common spoken fillers at word boundaries, optionally followed by a
# comma (e.g. "Um, I think…" → "I think…").
_FILLERS = re.compile(
    r"\b(?:"
    r"um+|uh+|umm+|uhh+|hmm+|hm+|ah+|err?+"
    r"|you know"
    r"|i mean"
    r"|kind of"
    r"|sort of"
    r"),?",
    flags=re.IGNORECASE,
)

_MULTI_SPACE    = re.compile(r"  +")
_LEADING_PUNCT  = re.compile(r"^[,;]\s*")
# Parakeet occasionally omits the space after sentence-ending punctuation,
# e.g. "Hello world.How are you" — insert one when a letter follows directly.
_MISSING_SPACE  = re.compile(r"([.!?])([A-Za-z])")


def fix_spacing(text: str) -> str:
    """
    Ensure there is a space after sentence-ending punctuation.

    Applied unconditionally after every transcription regardless of whether
    the filler filter is enabled.
    """
    return _MISSING_SPACE.sub(r"\1 \2", text)


def apply_filler_filter(text: str) -> str:
    """
    Remove spoken filler words/phrases from *text* and normalise whitespace.

    Examples
    --------
    >>> apply_filler_filter("Um, I think so.")
    'I think so.'
    >>> apply_filler_filter("I, uh, don't know you know.")
    "I, don't know."
    """
    text = _FILLERS.sub("", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _LEADING_PUNCT.sub("", text)
    return text.strip()
