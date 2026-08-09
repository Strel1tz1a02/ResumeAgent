"""Deterministic quality scorers for conversation-memory compaction."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


def _normalized_text(value: Any) -> str:
    """Flatten JSON-like data for stable, case-insensitive phrase matching."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"[\s\W_]+", "", payload, flags=re.UNICODE).casefold()


def memory_fact_recall(
    memory: Mapping[str, Any],
    expected_fact_aliases: Sequence[Sequence[str]],
) -> float:
    """Return the fraction of expected facts represented by any accepted alias."""
    if not expected_fact_aliases:
        return 1.0
    text = _normalized_text(memory)
    hits = sum(
        1
        for aliases in expected_fact_aliases
        if any(_normalized_text(alias) in text for alias in aliases)
    )
    return hits / len(expected_fact_aliases)


def forbidden_memory_hits(
    memory: Mapping[str, Any],
    forbidden_phrases: Sequence[str],
) -> list[str]:
    """Return domain facts, stale claims, or assistant guesses leaked into memory."""
    text = _normalized_text(memory)
    return [
        phrase
        for phrase in forbidden_phrases
        if _normalized_text(phrase) in text
    ]


def token_compression_ratio(source_tokens: int, memory_tokens: int) -> float:
    """Return retained token share; lower is smaller, while 1.0 means no saving."""
    if source_tokens <= 0:
        raise ValueError("source_tokens must be positive")
    if memory_tokens < 0:
        raise ValueError("memory_tokens cannot be negative")
    return memory_tokens / source_tokens
