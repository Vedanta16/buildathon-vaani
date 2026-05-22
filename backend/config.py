import os
from dataclasses import dataclass, field

@dataclass
class Config:
    asr_provider: str = os.getenv("ASR_PROVIDER", "openai_realtime")
    tts_provider: str = os.getenv("TTS_PROVIDER", "openai")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "AIzaSyCJ0bUaqkF6pGJOpEyXqgY8MHgLLDQ_ndA")

    large_model: str = "gpt-4o"
    small_model: str = "gpt-4o-mini"
    routing_word_threshold: int = 8

    short_answer_set: frozenset = field(default_factory=lambda: frozenset({
        "yes", "no", "ok", "okay", "sure", "thanks", "thank you",
        "got it", "sounds good", "perfect", "great", "fine",
        "yep", "nope", "correct", "exactly", "absolutely",
        "go ahead", "send it", "do it", "keep it",
    }))

    spec_debounce_ms: int = 400
    spec_min_words: int = 5
    spec_commit_ratio: float = 0.85
    phrase_cache_ratio: float = 0.88

    sample_rate: int = 16000
    phrase_cache_dir: str = "phrase_cache"
    filler_dir: str = "filler_audio"
    recordings_dir: str = "recordings"

cfg = Config()
