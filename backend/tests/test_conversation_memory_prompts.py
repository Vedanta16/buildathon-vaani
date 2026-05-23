from backend.conversation.memory import (
    filter_memory_for_turn,
    memory_items_from_blob,
    memory_items_to_blob,
)
from backend.conversation.models import (
    ConversationMessage,
    MemoryItem,
    RuntimeFlags,
    UserTurn,
)
from backend.conversation.prompts import render_live_chat_prompt
from backend.conversation.routes import select_route


def test_memory_filter_excludes_disabled_memory():
    items = [
        MemoryItem(
            id="m1",
            category="user_preference",
            text="Prefers SMS links for renewal.",
            tags=["renewal", "sms"],
        )
    ]

    context = filter_memory_for_turn("Send my renewal link", items, enabled=False)

    assert context.included_ids == []
    assert context.excluded_ids == []
    assert context.reason == "memory_disabled"
    assert context.to_prompt_block() == ""


def test_memory_filter_includes_only_safe_relevant_reviewed_memory():
    items = [
        MemoryItem(
            id="m1",
            category="user_preference",
            text="Prefers SMS links for renewal.",
            tags=["renewal", "sms"],
        ),
        MemoryItem(
            id="m2",
            category="durable_fact",
            text="Has homeowners policy.",
            tags=["home"],
        ),
        MemoryItem(
            id="m3",
            category="private_note",
            text="Internal escalation risk.",
            sensitivity="private",
            tags=["renewal"],
        ),
        MemoryItem(
            id="m4",
            category="recent_issue",
            text="Had a billing problem.",
            confidence=0.4,
            tags=["billing"],
        ),
    ]

    context = filter_memory_for_turn("Please send my renewal link", items, enabled=True)

    assert context.included_ids == ["m1"]
    assert set(context.excluded_ids) == {"m2", "m3", "m4"}
    assert context.reason == "relevant_memory_selected"
    assert "Prefers SMS links" in context.to_prompt_block()
    assert "Internal escalation" not in context.to_prompt_block()


def test_memory_blob_roundtrip_ignores_invalid_items():
    items = memory_items_from_blob({
        "items": [
            {
                "id": "m1",
                "category": "user_preference",
                "text": "Prefers SMS links.",
                "confidence": 0.9,
                "tags": ["sms"],
            },
            {"id": "missing-category", "text": "Invalid item."},
            "not-a-memory-item",
        ]
    })

    assert len(items) == 1
    assert items[0].id == "m1"
    assert memory_items_to_blob(items)["items"][0]["text"] == "Prefers SMS links."


def test_prompt_includes_relevant_memory_only_when_enabled():
    items = [
        MemoryItem(
            id="m1",
            category="user_preference",
            text="Prefers SMS links for renewal.",
            tags=["renewal", "sms"],
        ),
        MemoryItem(
            id="m2",
            category="private_note",
            text="Do not tell the user this internal note.",
            sensitivity="private",
            tags=["renewal"],
        ),
    ]
    context = filter_memory_for_turn("Send the renewal link", items, enabled=True)
    turn = UserTurn(
        session_id="s1",
        user_id="u1",
        turn_id="t1",
        user_text="Send the renewal link",
        asr_provider="mock",
        vad_mode="local_manual",
        conversation_history=[
            ConversationMessage(role="user", content="Hi"),
            ConversationMessage(role="assistant", content="Hi, how can I help?"),
        ],
        memory_context=context,
        runtime_flags=RuntimeFlags(memory=True),
    )

    prompt = render_live_chat_prompt(
        turn,
        select_route(turn.user_text, turn.runtime_flags),
        base_system_prompt="You are concise.",
    )

    assert "Prefers SMS links for renewal." in prompt
    assert "Do not tell the user this internal note." not in prompt
    assert "user: Hi" in prompt
    assert "Current user turn:" in prompt


def test_prompt_omits_memory_when_memory_flag_is_false():
    context = filter_memory_for_turn(
        "Send the renewal link",
        [
            MemoryItem(
                id="m1",
                category="user_preference",
                text="Prefers SMS links for renewal.",
                tags=["renewal", "sms"],
            )
        ],
        enabled=True,
    )
    turn = UserTurn(
        session_id="s1",
        user_id="u1",
        turn_id="t1",
        user_text="Send the renewal link",
        asr_provider="mock",
        vad_mode="local_manual",
        memory_context=context,
        runtime_flags=RuntimeFlags(memory=False),
    )

    prompt = render_live_chat_prompt(
        turn,
        select_route(turn.user_text, turn.runtime_flags),
        base_system_prompt="You are concise.",
    )

    assert "Prefers SMS links for renewal." not in prompt
    assert "No reviewed memory is relevant for this turn." in prompt


def test_prompt_can_leave_history_in_model_contents_for_live_streaming():
    turn = UserTurn(
        session_id="s1",
        user_id="u1",
        turn_id="t1",
        user_text="Can you check my policy?",
        asr_provider="mock",
        vad_mode="local_manual",
        conversation_history=[ConversationMessage(role="user", content="Hi")],
    )

    prompt = render_live_chat_prompt(
        turn,
        select_route(turn.user_text, turn.runtime_flags),
        base_system_prompt="You are concise.",
        include_conversation=False,
    )

    assert "lane=FAST_LLM" in prompt
    assert "Provided in model contents." in prompt
    assert "user: Hi" not in prompt
    assert "Can you check my policy?" not in prompt
