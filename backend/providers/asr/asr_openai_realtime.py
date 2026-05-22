# backend/providers/asr/asr_openai_realtime.py
import asyncio
import base64
import json
from typing import Callable
from openai import AsyncOpenAI
from backend.config import cfg
from backend.events import AsrPartial, AsrFinal

class OpenAIRealtimeASR:
    """
    Uses OpenAI Realtime API in transcription-only mode.
    Sends PCM audio, receives transcript.text.delta events.
    """
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = AsyncOpenAI(api_key=cfg.openai_api_key)
        self._ws = None
        self._partial_buf = ""
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        # .enter() is the non-context-manager way to open the connection
        self._ws = await self._client.beta.realtime.connect(
            model="gpt-4o-realtime-preview"
        ).enter()
        await self._ws.session.update(session={
            "input_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": None,
        })
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        try:
            async for event in self._ws:
                # SDK events are objects with a .type attribute
                etype = getattr(event, "type", None) or (event.get("type", "") if isinstance(event, dict) else "")
                if etype == "conversation.item.input_audio_transcription.delta":
                    delta = getattr(event, "delta", "") or (event.get("delta", "") if isinstance(event, dict) else "")
                    self._partial_buf += delta
                    self._on_event(AsrPartial(
                        text=self._partial_buf,
                        stable_ms=0,
                        provider="openai_realtime",
                    ))
                elif etype == "conversation.item.input_audio_transcription.completed":
                    text = getattr(event, "transcript", self._partial_buf) or self._partial_buf
                    self._on_event(AsrFinal(text=text, provider="openai_realtime"))
                    self._partial_buf = ""
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._ws:
            return
        audio_b64 = base64.b64encode(pcm_bytes).decode()
        await self._ws.input_audio_buffer.append(audio=audio_b64)

    async def flush(self) -> None:
        if not self._ws:
            return
        await self._ws.input_audio_buffer.commit()
        await self._ws.response.create()

    async def close(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
