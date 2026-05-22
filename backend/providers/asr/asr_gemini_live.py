# backend/providers/asr/asr_gemini_live.py
import asyncio
from typing import Callable
from google import genai
from google.genai import types
from backend.config import cfg
from backend.events import AsrPartial, AsrFinal


class GeminiLiveASR:
    """
    Uses Gemini Live API in transcription-only mode.
    Sends 16kHz PCM, maps input_transcription → asr.partial / asr.final.
    Model audio output is discarded (response_modalities=[]).

    SDK notes (google-genai v1.75.0):
    - client.aio.live.connect() is an @asynccontextmanager, not a plain coroutine.
      We enter it manually via __aenter__ / __aexit__ to keep the session open.
    - send_realtime_input() accepts keyword args: audio=Blob(...), activity_end=ActivityEnd()
    - LiveServerContent.input_transcription is a Transcription object with .text and .finished
    - Transcription.finished=True signals the end of a transcription utterance (like turn_complete
      for input). We use both turn_complete and transcription.finished to emit AsrFinal.
    """

    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = genai.Client(api_key=cfg.gemini_api_key)
        self._session = None
        self._ctx_manager = None
        self._partial_buf = ""
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        config = types.LiveConnectConfig(
            response_modalities=[],  # no audio output — transcription only
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )
        # connect() is an asynccontextmanager; enter it manually to keep it alive
        self._ctx_manager = self._client.aio.live.connect(
            model="gemini-live-2.5-flash",
            config=config,
        )
        self._session = await self._ctx_manager.__aenter__()
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        try:
            async for msg in self._session.receive():
                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                # Input transcription (partial or final)
                input_trans = getattr(server_content, "input_transcription", None)
                if input_trans:
                    text = getattr(input_trans, "text", "") or ""
                    finished = getattr(input_trans, "finished", False)
                    if text:
                        self._partial_buf = text
                        if finished:
                            # Transcription marked finished → emit final
                            self._on_event(AsrFinal(
                                text=text,
                                provider="gemini_live",
                            ))
                            self._partial_buf = ""
                        else:
                            self._on_event(AsrPartial(
                                text=text,
                                stable_ms=0,
                                provider="gemini_live",
                            ))

                # Turn complete → emit final if we have buffered text
                if getattr(server_content, "turn_complete", False):
                    if self._partial_buf:
                        self._on_event(AsrFinal(
                            text=self._partial_buf,
                            provider="gemini_live",
                        ))
                        self._partial_buf = ""
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
        )

    async def flush(self) -> None:
        """Signal end of user turn to trigger final transcription."""
        if not self._session:
            return
        await self._session.send_realtime_input(
            activity_end=types.ActivityEnd()
        )

    async def close(self) -> None:
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
