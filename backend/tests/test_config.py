from backend.config import cfg

def test_config_loads():
    assert cfg.large_model == "gpt-4o"
    assert cfg.small_model == "gpt-4o-mini"
    assert "yes" in cfg.short_answer_set
    assert cfg.gemini_api_key != ""
    assert cfg.spec_commit_ratio == 0.85
