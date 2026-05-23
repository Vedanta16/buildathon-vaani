from backend.conversation.memory import filter_memory_for_turn
from backend.conversation.models import MemoryItem, RuntimeFlags, UserTurn
from backend.conversation.models import ConversationMessage
from backend.conversation.planner import (
    is_safe_to_speak_text,
    plan_for_deterministic_route,
    plan_for_prefilled_route,
    plan_from_llm_response,
)
from backend.conversation.routes import select_route


def make_turn(text: str, *, memory=False, memory_context=None) -> UserTurn:
    return UserTurn(
        session_id="s1",
        user_id="u1",
        turn_id="t1",
        user_text=text,
        asr_provider="mock",
        vad_mode="local_manual",
        memory_context=memory_context,
        runtime_flags=RuntimeFlags(memory=memory),
    )


def test_prefilled_greeting_plan_uses_stable_phrase_id():
    turn = make_turn("Hi")
    plan = plan_for_prefilled_route(turn)

    assert plan is not None
    assert plan.route.lane == "CACHE"
    assert plan.display_text == "Hi, how can I help?"
    assert plan.spoken_segments[0].type == "prefilled_phrase"
    assert plan.spoken_segments[0].phrase_id == "G1.v1"
    assert plan.validation.display_matches_spoken is True
    assert plan.validation.safe_to_speak is True
    assert plan.metrics["phrase_id"] == "G1.v1"


def test_prefilled_plan_returns_none_when_route_has_no_phrase():
    turn = make_turn("Tell me more")
    plan = plan_for_prefilled_route(turn, select_route(turn.user_text))

    assert plan is None


def test_deterministic_confirmation_plan_bypasses_llm_with_phrase():
    turn = make_turn("yes")
    plan = plan_for_deterministic_route(turn)

    assert plan is not None
    assert plan.route.lane == "NO_LLM"
    assert plan.route.model is None
    assert plan.display_text == "Got it."
    assert plan.spoken_segments[0].phrase_id == "K1.v1"
    assert plan.metrics["model"] == "none"


def test_deterministic_repeat_plan_uses_last_assistant_message():
    turn = make_turn("Can you repeat that?")
    turn.conversation_history.extend([
        ConversationMessage(role="user", content="What is my coverage?"),
        ConversationMessage(role="assistant", content="Your roadside coverage is active."),
    ])

    plan = plan_for_deterministic_route(turn)

    assert plan is not None
    assert plan.route.route == "repeat_last_response"
    assert plan.display_text == "Your roadside coverage is active."
    assert plan.metrics["prompt_tokens"] == 0


def test_async_post_call_route_gets_deterministic_ack_plan():
    turn = make_turn("Summarize this call")
    plan = plan_for_deterministic_route(turn)

    assert plan is not None
    assert plan.route.lane == "ASYNC"
    assert plan.route.route == "post_call_analysis"
    assert plan.display_text == "I will prepare that summary after the call."
    assert plan.spoken_segments[0].phrase_id == "P1.v1"


def test_llm_response_plan_wraps_text_as_display_and_spoken_segment():
    turn = make_turn("Can you check my policy?")
    route = select_route(turn.user_text)
    plan = plan_from_llm_response(
        turn,
        "Your policy is active through June fourth.",
        route=route,
        usage={"prompt_tokens": 10, "completion_tokens": 8, "cached_tokens": 0},
    )

    assert plan.display_text == "Your policy is active through June fourth."
    assert plan.spoken_segments[0].type == "text"
    assert plan.spoken_segments[0].should_display is True
    assert plan.spoken_segments[0].should_speak is True
    assert plan.validation.display_matches_spoken is True
    assert plan.metrics["lane"] == "FAST_LLM"
    assert plan.metrics["route_reason"] == "lookup_or_policy_terms"
    assert plan.metrics["prompt_tokens"] == 10


def test_llm_response_plan_prefers_actual_usage_model_over_route_model():
    turn = make_turn("Can you check my policy?")
    route = select_route(turn.user_text)
    plan = plan_from_llm_response(
        turn,
        "Your policy is active.",
        route=route,
        usage={"model": "gemini-2.5-flash"},
    )

    assert route.model == "gpt-4o-mini"
    assert plan.metrics["model"] == "gemini-2.5-flash"


def test_planner_flags_private_memory_leak_in_output():
    memory_items = [
        MemoryItem(
            id="private",
            category="private_note",
            text="Internal escalation risk.",
            sensitivity="private",
            tags=["renewal"],
        )
    ]
    memory_context = filter_memory_for_turn(
        "What is happening with renewal?",
        memory_items,
        enabled=True,
    )
    turn = make_turn("What is happening with renewal?", memory=True, memory_context=memory_context)

    plan = plan_from_llm_response(turn, "Internal escalation risk.")

    assert plan.validation.contains_private_memory is True
    assert plan.validation.safe_to_speak is False


def test_speech_safety_blocks_private_memory_text_before_tts():
    memory_context = filter_memory_for_turn(
        "What is happening with renewal?",
        [
            MemoryItem(
                id="private",
                category="private_note",
                text="Internal escalation risk.",
                sensitivity="private",
                tags=["renewal"],
            )
        ],
        enabled=True,
    )
    turn = make_turn("What is happening with renewal?", memory=True, memory_context=memory_context)

    assert is_safe_to_speak_text(turn, "Your renewal is still being checked.") is True
    assert is_safe_to_speak_text(turn, "Internal escalation risk.") is False
