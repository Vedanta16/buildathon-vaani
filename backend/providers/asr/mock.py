from typing import Callable
from backend.events import AsrPartial, AsrFinal

class MockASR:
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._buffer = b""

    async def connect(self) -> None:
        pass

    async def activity_start(self) -> None:
        pass

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self._buffer += pcm_bytes
        if len(self._buffer) >= 512:
            self._on_event(AsrPartial(text="hello", stable_ms=200, provider="mock"))

    async def flush(self) -> None:
        self._on_event(AsrFinal(text="hello world", provider="mock"))
        self._buffer = b""

    async def close(self) -> None:
        pass
