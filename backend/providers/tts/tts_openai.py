from typing import Callable
from openai import AsyncOpenAI
from backend.config import cfg
from backend.events import TtsAudioChunk, TtsDone


class OpenAITTS:
    """
    Streaming TTS via OpenAI /v1/audio/speech.
    Emits TtsAudioChunk events as PCM chunks arrive.
    Output: 24kHz, 16-bit, mono PCM.
    """
    CHUNK_SIZE = 4096  # bytes

    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = AsyncOpenAI(api_key=cfg.openai_api_key)

    async def synthesize(self, text: str) -> None:
        if not text.strip():
            return
        async with self._client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="alloy",
            input=text,
            response_format="pcm",  # 24kHz, 16-bit, mono
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=self.CHUNK_SIZE):
                if chunk:
                    self._on_event(TtsAudioChunk(
                        pcm_bytes=chunk,
                        sample_rate=24000,
                        provider="openai",
                        source="tts",
                    ))
        self._on_event(TtsDone(provider="openai"))

    async def close(self) -> None:
        pass
