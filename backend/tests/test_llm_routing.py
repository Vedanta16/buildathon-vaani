from backend.llm_openai import select_model

def test_routes_short_answer_to_small_model():
    assert select_model("yes") == "gpt-4o-mini"
    assert select_model("ok") == "gpt-4o-mini"
    assert select_model("sure") == "gpt-4o-mini"

def test_routes_short_word_count_to_small_model():
    # 8 words exactly → small
    assert select_model("yeah keep it and send the link") == "gpt-4o-mini"

def test_routes_long_turn_to_large_model():
    assert select_model(
        "I'm trying to renew my policy but the link in your email isn't working for some reason"
    ) == "gpt-4o"

def test_nine_words_routes_large():
    # 9 words → large
    assert select_model("can you check if my roadside coverage is still active") == "gpt-4o"

def test_routing_disabled_always_large(monkeypatch):
    from backend import llm_openai
    monkeypatch.setattr(llm_openai, "ROUTING_ENABLED", False)
    assert select_model("yes") == "gpt-4o"
