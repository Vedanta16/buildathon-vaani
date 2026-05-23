from __future__ import annotations

import re

from backend.config import cfg
from backend.conversation.models import RouteDecision, RuntimeFlags


WORD_RE = re.compile(r"[a-z0-9']+")

GREETING = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}
THANKS = {"thanks", "thank you", "appreciate it"}
CONFIRM = {"yes", "yeah", "yep", "ok", "okay", "sure", "correct", "exactly", "go ahead"}
NEGATE = {"no", "nope", "not really"}
REPEAT_PATTERNS = ("repeat", "say that again", "again please")
POST_CALL_PATTERNS = ("summarize this call", "call summary", "post call", "sentiment", "quality score")
COMPLEX_TERMS = {
    "compare",
    "why",
    "reason",
    "difference",
    "best",
    "should",
    "recommend",
    "pros",
    "cons",
    "coverage",
    "liability",
    "collision",
    "deductible",
}
LOOKUP_TERMS = {"check", "find", "look", "pull", "send", "policy", "renewal", "billing", "claim", "coverage"}
CLARIFY_TERMS = {"what do you mean", "i don't understand", "dont understand", "unclear", "confused"}


def normalize_text(text: str) -> str:
    normalized = " ".join(WORD_RE.findall(text.lower()))
    return normalized.strip()


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def select_route(
    user_text: str,
    runtime_flags: RuntimeFlags | None = None,
) -> RouteDecision:
    flags = runtime_flags or RuntimeFlags()
    normalized = normalize_text(user_text)

    if not normalized:
        return RouteDecision(
            lane="NO_LLM",
            route="empty",
            reason="empty_user_text",
            model=None,
            confidence=1.0,
        )

    if any(pattern in normalized for pattern in POST_CALL_PATTERNS):
        return RouteDecision(
            lane="ASYNC",
            route="post_call_analysis",
            reason="post_call_request",
            model=cfg.small_model,
            confidence=0.92,
        )

    if normalized in GREETING:
        return RouteDecision(
            lane="CACHE",
            route="greeting",
            reason="known_greeting_phrase",
            model=None,
            confidence=0.99,
            phrase_id="G1.v1",
        )

    if normalized in THANKS:
        return RouteDecision(
            lane="CACHE",
            route="closing",
            reason="known_thanks_phrase",
            model=None,
            confidence=0.95,
            phrase_id="S1.v1",
        )

    if normalized in CONFIRM or normalized in NEGATE:
        return RouteDecision(
            lane="NO_LLM",
            route="simple_confirmation",
            reason="simple_confirmation_or_negation",
            model=None,
            confidence=0.97,
            phrase_id="K1.v1",
        )

    if any(pattern in normalized for pattern in REPEAT_PATTERNS):
        return RouteDecision(
            lane="NO_LLM",
            route="repeat_last_response",
            reason="repeat_request",
            model=None,
            confidence=0.93,
        )

    if any(pattern in normalized for pattern in CLARIFY_TERMS):
        return RouteDecision(
            lane="FAST_LLM",
            route="clarify",
            reason="clarification_request",
            model=cfg.small_model,
            confidence=0.88,
            phrase_id="C1.v1",
        )

    words = set(normalized.split())
    if words & COMPLEX_TERMS and ("compare" in words or "why" in words or "should" in words):
        return RouteDecision(
            lane="SMART_LLM" if flags.smart_routing else "FAST_LLM",
            route="reasoning",
            reason="reasoning_or_comparison_terms",
            model=cfg.large_model if flags.smart_routing else cfg.small_model,
            confidence=0.86,
        )

    if words & LOOKUP_TERMS:
        return RouteDecision(
            lane="FAST_LLM",
            route="policy_lookup",
            reason="lookup_or_policy_terms",
            model=cfg.small_model,
            confidence=0.82,
            phrase_id="A1.v1",
        )

    if _word_count(normalized) <= cfg.routing_word_threshold:
        return RouteDecision(
            lane="FAST_LLM",
            route="short_answer",
            reason="short_non_deterministic_turn",
            model=cfg.small_model,
            confidence=0.72,
        )

    return RouteDecision(
        lane="SMART_LLM" if flags.smart_routing else "FAST_LLM",
        route="open_ended",
        reason="open_ended_or_long_turn",
        model=cfg.large_model if flags.smart_routing else cfg.small_model,
        confidence=0.68,
    )
