# backend/metrics.py
import time
from dataclasses import dataclass, field

@dataclass
class TurnMetrics:
    turn_id: int
    vad_start_ms: int = 0
    asr_final_ms: int = 0
    llm_start_ms: int = 0
    llm_first_token_ms: int = 0
    tts_start_ms: int = 0
    tts_first_audio_ms: int = 0
    playback_start_ms: int = 0
    barge_in_ms: int | None = None
    filler_played: bool = False
    phrase_cache_hit: bool = False
    spec_hit: bool = False
    spec_input: str = ""
    model_used: str = ""
    routed_small: bool = False
    prompt_cached_tokens: int = 0
    prompt_uncached_tokens: int = 0
    completion_tokens: int = 0
    tts_provider: str = ""
    asr_provider: str = ""

    @property
    def asr_ms(self) -> int:
        return max(0, self.asr_final_ms - self.vad_start_ms)

    @property
    def llm_ttft_ms(self) -> int:
        return max(0, self.llm_first_token_ms - self.llm_start_ms)

    @property
    def tts_ttfb_ms(self) -> int:
        if self.phrase_cache_hit:
            return 0
        return max(0, self.tts_first_audio_ms - self.tts_start_ms)

    @property
    def actual_latency_ms(self) -> int:
        return max(0, self.tts_first_audio_ms - self.vad_start_ms)

    @property
    def perceived_latency_ms(self) -> int:
        filler_start = self.llm_start_ms
        first_audio = min(filler_start, self.tts_first_audio_ms) if self.filler_played else self.tts_first_audio_ms
        return max(0, first_audio - self.vad_start_ms)


class SessionMetrics:
    def __init__(self):
        self.turns: list[TurnMetrics] = []
        self._start_ms = int(time.time() * 1000)

    def now_ms(self) -> int:
        return int(time.time() * 1000) - self._start_ms

    def new_turn(self) -> TurnMetrics:
        t = TurnMetrics(turn_id=len(self.turns) + 1)
        self.turns.append(t)
        return t

    @property
    def total_prompt_cached(self) -> int:
        return sum(t.prompt_cached_tokens for t in self.turns)

    @property
    def total_prompt_uncached(self) -> int:
        return sum(t.prompt_uncached_tokens for t in self.turns)

    @property
    def total_completion(self) -> int:
        return sum(t.completion_tokens for t in self.turns)

    @property
    def spec_hit_rate(self) -> float:
        if not self.turns:
            return 0.0
        return sum(1 for t in self.turns if t.spec_hit) / len(self.turns)

    @property
    def median_latency_ms(self) -> int:
        latencies = sorted(t.actual_latency_ms for t in self.turns if t.actual_latency_ms > 0)
        if not latencies:
            return 0
        mid = len(latencies) // 2
        return latencies[mid]
