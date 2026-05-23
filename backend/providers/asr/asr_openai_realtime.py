# backend/providers/asr/asr_openai_realtime.py
import asyncio
import base64
import json
import logging
import math
from typing import Callable

import numpy as np
from scipy.signal import resample_poly
import websockets

from backend.config import cfg
from backend.events import AsrPartial, AsrFinal

logger = logging.getLogger("asr_openai_realtime")


OPENAI_REALTIME_TRANSCRIPTION_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
OPENAI_REALTIME_SAMPLE_RATE = 24000


def _resample_pcm16(pcm_bytes: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes, dtype="<i2")
    if len(samples) == 0:
        return b""
    gcd = math.gcd(source_rate, target_rate)
    up = target_rate // gcd
    down = source_rate // gcd
    resampled = resample_poly(samples.astype(np.float32), up, down)
    return np.clip(np.rint(resampled), -32768, 32767).astype("<i2").tobytes()


class OpenAIRealtimeASR:
    """
    Uses OpenAI Realtime API in transcription-only mode.
    Sends PCM audio, receives transcript.text.delta events.
    """
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._ws = None
        self._partial_buf = ""
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        transcription_config = {
            "model": cfg.openai_transcription_model,
            "language": "en",
        }
        if cfg.openai_transcription_model == "gpt-realtime-whisper":
            transcription_config["delay"] = "low"

        self._ws = await websockets.connect(
            OPENAI_REALTIME_TRANSCRIPTION_URL,
            additional_headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
            max_size=None,
        )
        await self._send({
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": OPENAI_REALTIME_SAMPLE_RATE,
                        },
                        "transcription": transcription_config,
                        "turn_detection": None,
                    },
                },
            },
        })
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _send(self, event: dict) -> None:
        if not self._ws:
            return
        await self._ws.send(json.dumps(event))

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                etype = event.get("type", "")
                if etype == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta", "")
                    self._partial_buf += delta
                    self._on_event(AsrPartial(
                        text=self._partial_buf,
                        stable_ms=0,
                        provider="openai_realtime",
                    ))
                elif etype == "conversation.item.input_audio_transcription.completed":
                    text = event.get("transcript", self._partial_buf) or self._partial_buf
                    self._on_event(AsrFinal(text=text, provider="openai_realtime"))
                    self._partial_buf = ""
                elif etype == "error":
                    error = event.get("error", {})
                    logger.error("OpenAI Realtime ASR error: %s", error)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("OpenAI Realtime receive loop error: %s", e, exc_info=True)

    async def activity_start(self) -> None:
        pass

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._ws:
            return
        pcm_24k = _resample_pcm16(pcm_bytes, cfg.sample_rate, OPENAI_REALTIME_SAMPLE_RATE)
        if not pcm_24k:
            return
        audio_b64 = base64.b64encode(pcm_24k).decode()
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": audio_b64,
        })

    async def flush(self) -> None:
        if not self._ws:
            return
        await self._send({"type": "input_audio_buffer.commit"})

    async def close(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
