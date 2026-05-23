from __future__ import annotations

from pathlib import Path

from backend.conversation.models import RouteDecision, UserTurn
from backend.conversation.phrases import PHRASES


TEMPLATE_PATH = Path(__file__).parent / "templates" / "live_chat_system.jinja"


def _history_lines(turn: UserTurn, limit: int = 8) -> str:
    recent = turn.conversation_history[-limit:]
    return "\n".join(f"{msg.role}: {msg.content}" for msg in recent)


def _phrase_manifest(route: RouteDecision) -> str:
    phrases = [
        phrase
        for phrase in PHRASES.values()
        if route.route in phrase.routes or phrase.category in {"ack", "clarify", "closing"}
    ]
    return "\n".join(f"{phrase.id}={phrase.text}" for phrase in phrases)


def render_live_chat_prompt(
    turn: UserTurn,
    route: RouteDecision,
    *,
    base_system_prompt: str,
    include_conversation: bool = True,
) -> str:
    template = TEMPLATE_PATH.read_text()
    memory_block = ""
    if turn.runtime_flags.memory and turn.memory_context:
        memory_block = turn.memory_context.to_prompt_block()

    replacements = {
        "base_system_prompt": base_system_prompt.strip(),
        "route_lane": route.lane,
        "route_reason": route.reason,
        "phrase_manifest": _phrase_manifest(route),
        "memory_block": memory_block or "No reviewed memory is relevant for this turn.",
        "conversation_history": (
            _history_lines(turn) or "No prior turns."
            if include_conversation
            else "Provided in model contents."
        ),
        "current_user_turn": (
            turn.user_text.strip()
            if include_conversation
            else "Provided in model contents."
        ),
    }

    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
    return rendered.strip()
