# backend/providers/asr/asr_gemini_live.py
import asyncio
import logging
from typing import Callable
from google import genai
from google.genai import types
from backend.config import cfg
from backend.events import AsrPartial, AsrFinal

logger = logging.getLogger("asr_gemini_live")


class GeminiLiveASR:
    """
    Uses Gemini Live API in transcription-only mode.
    Sends 16kHz PCM, maps input_transcription → asr.partial / asr.final.
    Model audio output is discarded (response_modalities=["AUDIO"] required by native model).

    Key insight from testing: session.receive() is an async iterator that ends after
    turn_complete, but the underlying WebSocket stays alive. We wrap the inner loop in
    `while not self._closed` to re-enter session.receive() for each subsequent turn —
    no reconnect needed.
    """

    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = genai.Client(api_key=cfg.gemini_api_key)
        self._session = None
        self._ctx_manager = None
        self._partial_buf = ""
        self._receive_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
        )
        self._ctx_manager = self._client.aio.live.connect(
            model=cfg.gemini_live_model,
            config=config,
        )
        self._session = await self._ctx_manager.__aenter__()
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        """
        Outer while loop keeps the session alive across turns.
        session.receive() ends after each turn_complete (StopAsyncIteration),
        but the WebSocket remains open — re-entering the iterator starts the next turn.
        """
        msg_count = 0
        try:
            while not self._closed:
                async for msg in self._session.receive():
                    msg_count += 1
                    server_content = getattr(msg, "server_content", None)
                    if server_content is None:
                        continue

                    model_turn = getattr(server_content, "model_turn", None)
                    input_trans = getattr(server_content, "input_transcription", None)
                    turn_complete = getattr(server_content, "turn_complete", False)

                    # Discard model audio — we only want input transcription
                    if model_turn:
                        continue

                    if input_trans:
                        text = getattr(input_trans, "text", "") or ""
                        finished = getattr(input_trans, "finished", False)
                        if text:
                            self._partial_buf = text
                            if finished:
                                logger.info("ASR final (transcription.finished): %r", text)
                                self._on_event(AsrFinal(text=text, provider="gemini_live"))
                                self._partial_buf = ""
                            else:
                                self._on_event(AsrPartial(text=text, stable_ms=0, provider="gemini_live"))

                    if turn_complete:
                        logger.info("ASR turn_complete, partial_buf=%r", self._partial_buf)
                        if self._partial_buf:
                            self._on_event(AsrFinal(text=self._partial_buf, provider="gemini_live"))
                            self._partial_buf = ""
                        # Inner async-for ends here; outer while re-enters for next turn

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Gemini Live receive loop error: %s", e, exc_info=True)
        finally:
            logger.info("Gemini Live receive loop exited after %d msgs", msg_count)

    async def activity_start(self) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(activity_start=types.ActivityStart())

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
        )

    async def flush(self) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(activity_end=types.ActivityEnd())

    async def close(self) -> None:
        self._closed = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ctx_manager and self._session:
            try:
                await self._ctx_manager.__aexit__(None, None, None)
            except Exception:
                pass
        self._session = None
        self._ctx_manager = None
