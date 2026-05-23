from typing import Callable
from backend.config import cfg

ASR_PROVIDERS = frozenset({"mock", "openai_realtime", "gemini_live"})


def registered_asr_providers() -> frozenset[str]:
    return ASR_PROVIDERS


def create_asr(on_event: Callable, provider: str | None = None):
    p = provider or cfg.asr_provider
    if p == "mock":
        from backend.providers.asr.mock import MockASR
        return MockASR(on_event=on_event)
    elif p == "openai_realtime":
        from backend.providers.asr.asr_openai_realtime import OpenAIRealtimeASR
        return OpenAIRealtimeASR(on_event=on_event)
    elif p == "gemini_live":
        from backend.providers.asr.asr_gemini_live import GeminiLiveASR
        return GeminiLiveASR(on_event=on_event)
    else:
        raise ValueError(f"Unknown ASR provider: {p}")
