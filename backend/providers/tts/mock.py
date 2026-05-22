# backend/providers/tts/mock.py
import numpy as np
from typing import Callable
from backend.events import TtsAudioChunk, TtsDone

class MockTTS:
    def __init__(self, on_event: Callable):
        self._on_event = on_event

    async def synthesize(self, text: str) -> None:
        # Emit 0.5s of silence as fake PCM (16-bit, 16kHz)
        samples = np.zeros(8000, dtype=np.int16)
        self._on_event(TtsAudioChunk(
            pcm_bytes=samples.tobytes(),
            sample_rate=16000,
            provider="mock",
            source="tts",
        ))
        self._on_event(TtsDone(provider="mock"))

    async def close(self) -> None:
        pass
