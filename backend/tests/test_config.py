from backend.config import cfg

def test_config_loads():
    assert cfg.large_model == "gpt-4o"
    assert cfg.small_model == "gpt-4o-mini"
    assert cfg.gemini_chat_fast_model == "gemini-2.5-flash"
    assert "yes" in cfg.short_answer_set
    assert cfg.gemini_api_key != ""
    assert cfg.spec_commit_ratio == 0.85
    assert cfg.vad_silence_flush_frames == 25
