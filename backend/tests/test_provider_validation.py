from backend.config import Config
from backend.providers.asr.factory import registered_asr_providers
from backend.providers.tts.factory import registered_tts_providers


def test_registered_provider_sets_include_live_and_mock_adapters():
    assert registered_asr_providers() == frozenset({"mock", "openai_realtime", "gemini_live"})
    assert registered_tts_providers() == frozenset({"mock", "openai", "gemini"})


def test_missing_provider_keys_reports_selected_provider_dependencies():
    config = Config(openai_api_key="", gemini_api_key="")

    missing = config.missing_provider_keys("openai_realtime", "gemini")

    assert "OPENAI_API_KEY required by ASR provider openai_realtime" in missing
    assert "GEMINI_API_KEY required by TTS provider gemini" in missing


def test_placeholder_keys_count_as_missing():
    config = Config(openai_api_key="YOUR_OPENAI_API_KEY", gemini_api_key="YOUR_GEMINI_API_KEY")

    assert config.missing_provider_keys("openai_realtime", "gemini_live")
