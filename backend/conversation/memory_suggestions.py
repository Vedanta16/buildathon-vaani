from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from backend.conversation.models import MemoryItem


Decision = Literal["accepted", "rejected"]
TOKEN_RE = re.compile(r"[a-z0-9]+")


def suggestion_id(session_id: str, suggestion: dict[str, Any]) -> str:
    category = str(suggestion.get("category", ""))
    text = str(suggestion.get("text", ""))
    digest = hashlib.sha1(f"{session_id}|{category}|{text}".encode()).hexdigest()[:12]
    return f"mem-{digest}"


def suggestions_from_report(
    session_id: str,
    user_id: str,
    report: dict[str, Any],
    decisions: dict[str, dict],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for raw in report.get("memory_candidates", []):
        if not isinstance(raw, dict):
            continue
        sid = suggestion_id(session_id, raw)
        decision = decisions.get(sid, {}).get("decision", "pending")
        suggestions.append({
            "id": sid,
            "session_id": session_id,
            "user_id": user_id,
            "status": decision,
            **raw,
        })
    return suggestions


def memory_item_from_suggestion(suggestion: dict[str, Any]) -> MemoryItem:
    text = str(suggestion["text"])
    tags = list(dict.fromkeys(TOKEN_RE.findall(text.lower())))[:8]
    return MemoryItem(
        id=str(suggestion["id"]),
        category=suggestion["category"],
        text=text,
        confidence=float(suggestion.get("confidence", 1.0)),
        sensitivity="normal",
        stale=False,
        source="reviewed",
        tags=tags,
    )


def upsert_reviewed_memory(existing: list[MemoryItem], item: MemoryItem) -> list[MemoryItem]:
    normalized = (item.category, item.text.strip().lower())
    for current in existing:
        if (current.category, current.text.strip().lower()) == normalized:
            return existing
    return [*existing, item]
