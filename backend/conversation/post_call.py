from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.conversation.models import MemoryUpdate


Sentiment = Literal["positive", "neutral", "negative", "mixed"]
Outcome = Literal["resolved", "unresolved", "handoff", "abandoned"]

NEGATIVE_TERMS = {
    "angry",
    "annoyed",
    "broken",
    "can't",
    "cannot",
    "confused",
    "frustrated",
    "issue",
    "not working",
    "problem",
    "still not",
    "wrong",
}
POSITIVE_TERMS = {
    "appreciate",
    "fixed",
    "great",
    "perfect",
    "resolved",
    "thanks",
    "thank you",
    "works",
}
HANDOFF_TERMS = {"agent", "human", "representative", "supervisor", "handoff", "transfer"}


@dataclass(slots=True)
class PostCallAnalysis:
    summary: str
    user_sentiment: Sentiment
    sentiment_evidence: list[str]
    outcome: Outcome
    action_items: list[str] = field(default_factory=list)
    memory_candidates: list[MemoryUpdate] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["memory_candidates"] = [
            candidate.to_dict() for candidate in self.memory_candidates
        ]
        return data


def analyze_post_call(turns: list[dict[str, Any]]) -> PostCallAnalysis:
    user_texts = [
        str(turn.get("text", "")).strip()
        for turn in turns
        if turn.get("role") == "user" and str(turn.get("text", "")).strip()
    ]
    assistant_texts = [
        str(turn.get("text", "")).strip()
        for turn in turns
        if turn.get("role") == "assistant" and str(turn.get("text", "")).strip()
    ]
    transcript = " ".join(user_texts + assistant_texts).lower()

    sentiment, evidence = _sentiment(user_texts)
    outcome = _outcome(user_texts, assistant_texts, transcript)
    action_items = _action_items(user_texts, outcome)
    memory_candidates = _memory_candidates(user_texts)
    quality_flags = _quality_flags(turns)
    summary = _summary(user_texts, assistant_texts, outcome)

    return PostCallAnalysis(
        summary=summary,
        user_sentiment=sentiment,
        sentiment_evidence=evidence,
        outcome=outcome,
        action_items=action_items,
        memory_candidates=memory_candidates,
        quality_flags=quality_flags,
    )


def _sentiment(user_texts: list[str]) -> tuple[Sentiment, list[str]]:
    negative_evidence = _evidence(user_texts, NEGATIVE_TERMS)
    positive_evidence = _evidence(user_texts, POSITIVE_TERMS)
    if negative_evidence and positive_evidence:
        return "mixed", [negative_evidence[0], positive_evidence[0]]
    if negative_evidence:
        return "negative", negative_evidence[:2]
    if positive_evidence:
        return "positive", positive_evidence[:2]
    return "neutral", []


def _evidence(texts: list[str], terms: set[str]) -> list[str]:
    matches: list[str] = []
    for text in texts:
        lowered = text.lower()
        if any(term in lowered for term in terms):
            matches.append(text)
    return matches


def _outcome(user_texts: list[str], assistant_texts: list[str], transcript: str) -> Outcome:
    if not assistant_texts and user_texts:
        return "abandoned"
    if any(term in transcript for term in HANDOFF_TERMS):
        return "handoff"
    last_user = user_texts[-1].lower() if user_texts else ""
    if any(term in last_user for term in ("still", "not working", "can't", "cannot", "unresolved")):
        return "unresolved"
    if any(term in last_user for term in ("fixed", "great", "perfect", "resolved", "thanks", "thank you", "works")):
        return "resolved"
    if any(term in transcript for term in ("resolved", "fixed", "works", "thank you", "thanks")):
        return "resolved"
    if any(term in transcript for term in ("issue", "problem", "broken", "wrong")):
        return "unresolved"
    return "resolved" if assistant_texts else "abandoned"


def _action_items(user_texts: list[str], outcome: Outcome) -> list[str]:
    joined = " ".join(user_texts).lower()
    items: list[str] = []
    if "link" in joined or "sms" in joined or "phone" in joined:
        items.append("Confirm requested link delivery channel.")
    if "billing" in joined or "invoice" in joined or "charge" in joined:
        items.append("Review billing details before the next follow-up.")
    if outcome == "unresolved":
        items.append("Follow up on the unresolved user issue.")
    if outcome == "handoff":
        items.append("Confirm handoff owner and next-step SLA.")
    return items


def _memory_candidates(user_texts: list[str]) -> list[MemoryUpdate]:
    joined = " ".join(user_texts).lower()
    candidates: list[MemoryUpdate] = []
    if "sms" in joined and ("prefer" in joined or "send" in joined or "link" in joined):
        candidates.append(
            MemoryUpdate(
                category="user_preference",
                text="Prefers SMS for link delivery.",
                confidence=0.86,
                requires_review=True,
            )
        )
    if "email" in joined and ("prefer" in joined or "send" in joined or "link" in joined):
        candidates.append(
            MemoryUpdate(
                category="user_preference",
                text="Prefers email for link delivery.",
                confidence=0.82,
                requires_review=True,
            )
        )
    return candidates


def _quality_flags(turns: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for turn in turns:
        metrics = turn.get("metrics") or turn.get("metrics_json") or {}
        if isinstance(metrics, str):
            continue
        if metrics.get("playback_cancelled") or metrics.get("barge_in_ms"):
            flags.append("barge_in_during_agent_playback")
        if metrics.get("display_matches_spoken") is False:
            flags.append("display_spoken_mismatch")
        if metrics.get("safe_to_speak") is False:
            flags.append("unsafe_spoken_output_detected")
    return sorted(set(flags))


def _summary(user_texts: list[str], assistant_texts: list[str], outcome: Outcome) -> str:
    if not user_texts:
        return "No user turns were captured."
    first_issue = user_texts[0].rstrip(".")
    if assistant_texts:
        return f"User asked: {first_issue}. Final outcome: {outcome}."
    return f"User asked: {first_issue}. The call ended before an assistant response."
