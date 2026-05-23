import os
from dataclasses import dataclass, field
from pathlib import Path


def _strip_inline_comment(value: str) -> str:
    """Strip unquoted .env comments while preserving # inside quoted values."""
    in_single = False
    in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return value[:i].rstrip()
    return value.strip()


def load_env_file(path: Path | None = None) -> None:
    """Load repo-root .env into process env without overriding exported values."""
    env_path = path or Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _strip_inline_comment(value).strip('"').strip("'"))


load_env_file()

@dataclass
class Config:
    asr_provider: str = os.getenv("ASR_PROVIDER", "gemini_live")
    tts_provider: str = os.getenv("TTS_PROVIDER", "gemini")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")

    openai_realtime_model: str = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
    openai_transcription_model: str = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-realtime-whisper")
    gemini_live_model: str = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
    gemini_tts_model: str = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")

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
    barge_in_threshold: float = 0.65
    vad_silence_flush_frames: int = 10
    phrase_cache_dir: str = "phrase_cache"
    filler_dir: str = "filler_audio"
    recordings_dir: str = "recordings"

    def api_key_configured(self, value: str) -> bool:
        normalized = (value or "").strip()
        if not normalized:
            return False
        placeholders = {
            "YOUR_OPENAI_API_KEY",
            "YOUR_GEMINI_API_KEY",
            "your-openai-key",
            "your-gemini-key",
            "...",
        }
        if normalized in placeholders:
            return False
        return not normalized.startswith("#")

    def missing_provider_keys(self, asr_provider: str, tts_provider: str) -> list[str]:
        required: dict[str, set[str]] = {
            "OPENAI_API_KEY": set(),
            "GEMINI_API_KEY": set(),
        }
        if asr_provider == "openai_realtime":
            required["OPENAI_API_KEY"].add("ASR provider openai_realtime")
        elif asr_provider == "gemini_live":
            required["GEMINI_API_KEY"].add("ASR provider gemini_live")

        if tts_provider == "openai":
            required["OPENAI_API_KEY"].add("TTS provider openai")
        elif tts_provider == "gemini":
            required["GEMINI_API_KEY"].add("TTS provider gemini")

        missing: list[str] = []
        if required["OPENAI_API_KEY"] and not self.api_key_configured(self.openai_api_key):
            missing.append(f"OPENAI_API_KEY required by {', '.join(sorted(required['OPENAI_API_KEY']))}")
        if required["GEMINI_API_KEY"] and not self.api_key_configured(self.gemini_api_key):
            missing.append(f"GEMINI_API_KEY required by {', '.join(sorted(required['GEMINI_API_KEY']))}")
        return missing

    def missing_live_llm_keys(self) -> list[str]:
        if self.api_key_configured(self.gemini_api_key):
            return []
        return ["GEMINI_API_KEY required by live LLM provider gemini"]

cfg = Config()
