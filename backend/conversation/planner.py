from __future__ import annotations

import uuid
from typing import Any

from backend.conversation.models import (
    AssistantPlan,
    PlanValidation,
    RouteDecision,
    SpokenSegment,
    UserTurn,
)
from backend.conversation.phrases import phrase_for_route, phrase_segment
from backend.conversation.routes import select_route


def contains_private_memory(turn: UserTurn, text: str) -> bool:
    if not turn.memory_context:
        return False
    private_texts = [
        item.text
        for item in turn.memory_context.items
        if item.sensitivity != "normal" or item.category == "private_note"
    ]
    normalized = text.lower()
    return any(private_text.lower() in normalized for private_text in private_texts)


def is_safe_to_speak_text(turn: UserTurn, text: str) -> bool:
    return bool(text.strip()) and not contains_private_memory(turn, text)


def _validation(turn: UserTurn, display_text: str, segments: list[SpokenSegment]) -> PlanValidation:
    spoken = " ".join(
        segment.spoken_text()
        for segment in segments
        if segment.should_speak and segment.spoken_text()
    ).strip()
    display_segments = " ".join(
        segment.spoken_text()
        for segment in segments
        if segment.should_display and segment.spoken_text()
    ).strip()
    display_matches_spoken = display_text.strip() == spoken or display_text.strip() == display_segments
    private_memory_found = contains_private_memory(turn, display_text + " " + spoken)
    return PlanValidation(
        display_matches_spoken=display_matches_spoken,
        contains_private_memory=private_memory_found,
        safe_to_speak=bool(spoken) and not private_memory_found,
    )


def plan_for_prefilled_route(
    turn: UserTurn,
    route: RouteDecision | None = None,
) -> AssistantPlan | None:
    route = route or select_route(turn.user_text, turn.runtime_flags)
    phrase = phrase_for_route(route)
    if phrase is None:
        return None

    segment = phrase_segment(phrase)
    display_text = phrase.text
    validation = _validation(turn, display_text, [segment])
    return AssistantPlan(
        assistant_turn_id=str(uuid.uuid4()),
        display_text=display_text,
        spoken_segments=[segment],
        route=route,
        validation=validation,
        metrics={
            "lane": route.lane,
            "route": route.route,
            "route_reason": route.reason,
            "model": route.model or "none",
            "phrase_id": phrase.id,
        },
    )


def plan_for_deterministic_route(
    turn: UserTurn,
    route: RouteDecision | None = None,
) -> AssistantPlan | None:
    route = route or select_route(turn.user_text, turn.runtime_flags)
    prefilled = plan_for_prefilled_route(turn, route)
    if prefilled:
        return prefilled

    if route.route == "repeat_last_response":
        last_assistant = next(
            (
                msg.content
                for msg in reversed(turn.conversation_history)
                if msg.role == "assistant" and msg.content.strip()
            ),
            "",
        )
        text = last_assistant or "I do not have anything to repeat yet."
        return plan_from_llm_response(
            turn,
            text,
            route=route,
            usage={"model": "none", "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0},
        )

    return None


def plan_from_llm_response(
    turn: UserTurn,
    response_text: str,
    *,
    route: RouteDecision | None = None,
    usage: dict[str, Any] | None = None,
) -> AssistantPlan:
    route = route or select_route(turn.user_text, turn.runtime_flags)
    text = response_text.strip()
    segment = SpokenSegment(
        id="seg-1",
        type="text",
        text=text,
        should_display=True,
        should_speak=True,
        cache_policy="bypass",
    )
    validation = _validation(turn, text, [segment])
    metrics = {
        "lane": route.lane,
        "route": route.route,
        "route_reason": route.reason,
        "model": (usage or {}).get("model") or route.model or "unknown",
    }
    if usage:
        metrics.update(usage)

    return AssistantPlan(
        assistant_turn_id=str(uuid.uuid4()),
        display_text=text,
        spoken_segments=[segment],
        route=route,
        validation=validation,
        metrics=metrics,
    )
