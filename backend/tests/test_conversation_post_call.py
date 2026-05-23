from backend.conversation.post_call import analyze_post_call


def test_post_call_positive_resolved_transcript():
    report = analyze_post_call([
        {"role": "user", "text": "Thanks, that works perfectly.", "ts_ms": 1},
        {"role": "assistant", "text": "Happy to help.", "ts_ms": 2},
    ])

    assert report.user_sentiment == "positive"
    assert report.outcome == "resolved"
    assert report.action_items == []
    assert "Final outcome: resolved" in report.summary


def test_post_call_negative_unresolved_transcript():
    report = analyze_post_call([
        {"role": "user", "text": "The renewal link is still not working and I am frustrated.", "ts_ms": 1},
        {"role": "assistant", "text": "I can try again.", "ts_ms": 2},
    ])

    assert report.user_sentiment == "negative"
    assert report.outcome == "unresolved"
    assert "Follow up on the unresolved user issue." in report.action_items
    assert report.sentiment_evidence


def test_post_call_mixed_sentiment_and_memory_candidate():
    report = analyze_post_call([
        {"role": "user", "text": "The link was broken earlier.", "ts_ms": 1},
        {"role": "assistant", "text": "I sent a new one.", "ts_ms": 2},
        {"role": "user", "text": "Great, please send links by SMS next time.", "ts_ms": 3},
    ])

    assert report.user_sentiment == "mixed"
    assert report.outcome == "resolved"
    assert len(report.memory_candidates) == 1
    assert report.memory_candidates[0].requires_review is True
    assert report.memory_candidates[0].category == "user_preference"


def test_post_call_abandoned_when_no_assistant_response():
    report = analyze_post_call([
        {"role": "user", "text": "Can someone help with billing?", "ts_ms": 1},
    ])

    assert report.outcome == "abandoned"
    assert "ended before an assistant response" in report.summary


def test_post_call_quality_flags_from_turn_metrics():
    report = analyze_post_call([
        {
            "role": "assistant",
            "text": "Internal escalation risk.",
            "ts_ms": 1,
            "metrics": {
                "barge_in_ms": 120,
                "display_matches_spoken": False,
                "safe_to_speak": False,
            },
        },
    ])

    assert report.quality_flags == [
        "barge_in_during_agent_playback",
        "display_spoken_mismatch",
        "unsafe_spoken_output_detected",
    ]

