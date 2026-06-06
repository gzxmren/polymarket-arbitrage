#!/usr/bin/env python3
"""Shared utilities for Polymarket monitoring scripts"""

# Slugs to skip (short-lived/low-value markets)
_SHORT_LIVED_KEYS = ('updown-', '-5m-', '-15m-', 'temperature-in-')

# Known acronyms to uppercase
_ACRONYMS = frozenset({
    'aapl', 'btc', 'eth', 'sol', 'nba', 'nfl', 'mlb', 'nhl',
    'f1', 'ceo', 'cfo', 'ai', 'gop', 'dem', 'fed', 'gdp',
    'cpi', 'et', 'pm', 'am', 'us', 'uk', 'eu', 'un',
})

# Words that should stay lowercase (conjunctions, articles, prepositions)
_LOWERCASE_WORDS = frozenset({'or', 'and', 'a', 'an', 'the', 'in', 'on', 'at', 'to', 'for', 'of', 'by', 'with'})


def slug_to_title(slug: str) -> str:
    """Convert Polymarket slug to readable title. Returns '' for short-lived markets."""
    if any(k in slug for k in _SHORT_LIVED_KEYS):
        return ''

    title = slug.replace('-and-', ' & ').replace('-or-', ' or ').replace('-', ' ')

    words = title.split()
    result = []
    for i, w in enumerate(words):
        wl = w.lower()
        if wl in _ACRONYMS:
            result.append(w.upper())
        elif i > 0 and wl in _LOWERCASE_WORDS:
            result.append(wl)
        else:
            result.append(w.capitalize())
    title = ' '.join(result)

    # Add question mark if starts with question word
    if any(title.startswith(q) for q in ('Will', 'Is', 'Are', 'Does', 'Can', 'Has', 'Do')):
        if not title.endswith('?'):
            title += '?'

    return title
