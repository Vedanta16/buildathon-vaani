# backend/barge_in.py
import asyncio
from typing import Callable

class BargeInHandler:
    def __init__(
        self,
        on_cancel: Callable,   # async callable — sends playback.cancel to client WS
        vad,                   # VAD instance — sets agent_playing = False
    ):
        self._on_cancel = on_cancel
        self._vad = vad
        self._llm_task: asyncio.Task | None = None
        self._tts_task: asyncio.Task | None = None

    def register_llm_task(self, task: asyncio.Task) -> None:
        self._llm_task = task

    def register_tts_task(self, task: asyncio.Task) -> None:
        self._tts_task = task

    async def fire(self, ts_ms: int) -> None:
        """Call when VAD detects speech during agent playback."""
        self._vad.agent_playing = False
        if self._llm_task and not self._llm_task.done():
            self._llm_task.cancel()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        await self._on_cancel()
