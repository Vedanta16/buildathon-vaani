from __future__ import annotations

from dataclasses import dataclass

from backend.conversation.models import RouteDecision, SpokenSegment


@dataclass(frozen=True, slots=True)
class Phrase:
    id: str
    category: str
    text: str
    version: int
    routes: tuple[str, ...]


PHRASES: dict[str, Phrase] = {
    "G1.v1": Phrase("G1.v1", "greeting", "Hi, how can I help?", 1, ("greeting",)),
    "K1.v1": Phrase("K1.v1", "ack", "Got it.", 1, ("simple_confirmation",)),
    "A1.v1": Phrase("A1.v1", "ack", "Got it, let me check that.", 1, ("policy_lookup", "kb_lookup")),
    "A2.v1": Phrase("A2.v1", "progress", "I am pulling that up now.", 1, ("tool_lookup", "policy_lookup")),
    "C1.v1": Phrase("C1.v1", "clarify", "Can you clarify what you mean?", 1, ("clarify",)),
    "S1.v1": Phrase("S1.v1", "closing", "Anything else I can help with?", 1, ("closing",)),
    "P1.v1": Phrase("P1.v1", "progress", "I will prepare that summary after the call.", 1, ("post_call_analysis",)),
    "E1.v1": Phrase("E1.v1", "error", "I had trouble loading that. Let me try again.", 1, ("error",)),
    "B1.v1": Phrase("B1.v1", "barge_in", "Go ahead.", 1, ("barge_in",)),
}


def phrase_for_route(route: RouteDecision) -> Phrase | None:
    if route.phrase_id:
        return PHRASES.get(route.phrase_id)
    for phrase in PHRASES.values():
        if route.route in phrase.routes:
            return phrase
    return None


def phrase_segment(
    phrase: Phrase,
    *,
    segment_id: str = "seg-1",
    should_display: bool = True,
    should_speak: bool = True,
) -> SpokenSegment:
    return SpokenSegment(
        id=segment_id,
        type="prefilled_phrase",
        text=phrase.text,
        phrase_id=phrase.id,
        should_display=should_display,
        should_speak=should_speak,
        cache_policy="prefer",
    )
