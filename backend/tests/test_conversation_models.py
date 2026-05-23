from backend.conversation.models import (
    AssistantPlan,
    ConversationMessage,
    PlanValidation,
    RouteDecision,
    RuntimeFlags,
    SpokenSegment,
    TurnTimestamps,
    UserTurn,
)


def test_user_turn_serializes_nested_contract():
    turn = UserTurn(
        session_id="s1",
        user_id="u1",
        turn_id="t1",
        user_text="Can you check my policy?",
        asr_provider="mock",
        vad_mode="local_manual",
        timestamps=TurnTimestamps(vad_start_ms=10, asr_first_partial_ms=25, asr_final_ms=50),
        conversation_history=[ConversationMessage(role="user", content="Hi")],
        runtime_flags=RuntimeFlags(memory=True, phrase_cache=False),
    )

    data = turn.to_dict()

    assert data["session_id"] == "s1"
    assert data["timestamps"]["asr_final_ms"] == 50
    assert data["conversation_history"] == [{"role": "user", "content": "Hi"}]
    assert data["runtime_flags"]["memory"] is True
    assert data["runtime_flags"]["phrase_cache"] is False


def test_assistant_plan_serializes_spoken_segments_and_route():
    route = RouteDecision(
        lane="FAST_LLM",
        route="policy_lookup",
        reason="lookup_or_policy_terms",
        model="gpt-4o-mini",
    )
    segment = SpokenSegment(
        id="seg-1",
        type="text",
        text="Your policy is active.",
        should_display=True,
        should_speak=True,
        cache_policy="bypass",
    )
    plan = AssistantPlan(
        assistant_turn_id="a1",
        display_text="Your policy is active.",
        spoken_segments=[segment],
        route=route,
        validation=PlanValidation(
            display_matches_spoken=True,
            contains_private_memory=False,
            safe_to_speak=True,
        ),
    )

    data = plan.to_dict()

    assert data["route"]["lane"] == "FAST_LLM"
    assert data["spoken_segments"][0]["text"] == "Your policy is active."
    assert data["validation"]["safe_to_speak"] is True
    assert plan.spoken_text() == "Your policy is active."

