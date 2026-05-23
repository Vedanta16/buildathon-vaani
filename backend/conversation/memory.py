from __future__ import annotations

import re
from typing import Any

from backend.conversation.models import MemoryContext, MemoryItem


TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def filter_memory_for_turn(
    user_text: str,
    items: list[MemoryItem],
    *,
    enabled: bool,
) -> MemoryContext:
    if not enabled:
        return MemoryContext(items=items, reason="memory_disabled")

    user_tokens = _tokens(user_text)
    included: list[str] = []
    excluded: list[str] = []
    for item in items:
        if not item.safe_for_prompt():
            excluded.append(item.id)
            continue
        haystack = _tokens(" ".join([item.text, *item.tags]))
        if user_tokens & haystack:
            included.append(item.id)
        else:
            excluded.append(item.id)

    reason = "relevant_memory_selected" if included else "no_relevant_memory"
    return MemoryContext(
        items=items,
        included_ids=included,
        excluded_ids=excluded,
        reason=reason,
    )


def memory_items_from_blob(blob: dict[str, Any] | None) -> list[MemoryItem]:
    if not blob:
        return []
    raw_items = blob.get("items", blob if isinstance(blob, list) else [])
    items: list[MemoryItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            items.append(
                MemoryItem(
                    id=str(raw["id"]),
                    category=raw["category"],
                    text=str(raw["text"]),
                    confidence=float(raw.get("confidence", 1.0)),
                    sensitivity=raw.get("sensitivity", "normal"),
                    stale=bool(raw.get("stale", False)),
                    source=raw.get("source", "reviewed"),
                    tags=[str(tag) for tag in raw.get("tags", [])],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return items


def memory_items_to_blob(items: list[MemoryItem]) -> dict[str, Any]:
    return {"items": [item.to_dict() for item in items]}
