# backend/providers/tts/factory.py
from typing import Callable
from backend.config import cfg

TTS_PROVIDERS = frozenset({"mock", "openai", "gemini"})


def registered_tts_providers() -> frozenset[str]:
    return TTS_PROVIDERS


def create_tts(on_event: Callable, provider: str | None = None):
    p = provider or cfg.tts_provider
    if p == "mock":
        from backend.providers.tts.mock import MockTTS
        return MockTTS(on_event=on_event)
    elif p == "openai":
        from backend.providers.tts.tts_openai import OpenAITTS
        return OpenAITTS(on_event=on_event)
    elif p == "gemini":
        from backend.providers.tts.tts_gemini import GeminiTTS
        return GeminiTTS(on_event=on_event)
    else:
        raise ValueError(f"Unknown TTS provider: {p}")
