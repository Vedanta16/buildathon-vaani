import pytest

from backend.config import cfg
from backend.conversation.models import RuntimeFlags
from backend.conversation.routes import select_route


@pytest.mark.parametrize(
    ("text", "lane", "route", "reason"),
    [
        ("Hi", "CACHE", "greeting", "known_greeting_phrase"),
        ("hello", "CACHE", "greeting", "known_greeting_phrase"),
        ("thanks", "CACHE", "closing", "known_thanks_phrase"),
        ("yes", "NO_LLM", "simple_confirmation", "simple_confirmation_or_negation"),
        ("nope", "NO_LLM", "simple_confirmation", "simple_confirmation_or_negation"),
        ("can you repeat that", "NO_LLM", "repeat_last_response", "repeat_request"),
        ("what do you mean", "FAST_LLM", "clarify", "clarification_request"),
        ("I don't understand", "FAST_LLM", "clarify", "clarification_request"),
        ("Can you check my policy?", "FAST_LLM", "policy_lookup", "lookup_or_policy_terms"),
        ("Please send the renewal link again", "FAST_LLM", "policy_lookup", "lookup_or_policy_terms"),
        ("Find my billing status", "FAST_LLM", "policy_lookup", "lookup_or_policy_terms"),
        ("Compare liability and collision coverage", "SMART_LLM", "reasoning", "reasoning_or_comparison_terms"),
        ("Why is my deductible higher?", "SMART_LLM", "reasoning", "reasoning_or_comparison_terms"),
        ("Summarize this call", "ASYNC", "post_call_analysis", "post_call_request"),
        ("", "NO_LLM", "empty", "empty_user_text"),
        ("Tell me more", "FAST_LLM", "short_answer", "short_non_deterministic_turn"),
    ],
)
def test_select_route_representative_utterances(text, lane, route, reason):
    decision = select_route(text)

    assert decision.lane == lane
    assert decision.route == route
    assert decision.reason == reason
    assert decision.confidence > 0


def test_smart_routing_disabled_uses_fast_model_for_reasoning():
    decision = select_route(
        "Why should I keep collision coverage?",
        RuntimeFlags(smart_routing=False),
    )

    assert decision.lane == "FAST_LLM"
    assert decision.model == cfg.small_model


def test_smart_routing_enabled_uses_large_model_for_reasoning():
    decision = select_route(
        "Why should I keep collision coverage?",
        RuntimeFlags(smart_routing=True),
    )

    assert decision.lane == "SMART_LLM"
    assert decision.model == cfg.large_model

