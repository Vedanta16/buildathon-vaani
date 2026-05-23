from typing import Callable

from google import genai
from google.genai import types

from backend.config import cfg
from backend.events import TtsAudioChunk, TtsDone


def _parse_sample_rate(mime_type: str) -> int:
    for part in mime_type.split(";"):
        part = part.strip()
        if part.lower().startswith("rate="):
            try:
                return int(part.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
    return 24000


class GeminiTTS:
    """
    Streaming TTS via gemini-3.1-flash-tts-preview.
    Emits TtsAudioChunk events per streamed chunk, then TtsDone.
    """

    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = genai.Client(api_key=cfg.gemini_api_key)

    async def synthesize(self, text: str) -> None:
        if not text.strip():
            return

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=text)],
            )
        ]
        config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["audio"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Zephyr"
                    )
                )
            ),
        )

        async for chunk in await self._client.aio.models.generate_content_stream(
            model="gemini-3.1-flash-tts-preview",
            contents=contents,
            config=config,
        ):
            if chunk.parts is None:
                continue
            part = chunk.parts[0]
            if part.inline_data and part.inline_data.data:
                pcm = part.inline_data.data
                if isinstance(pcm, str):
                    import base64
                    pcm = base64.b64decode(pcm)
                sample_rate = _parse_sample_rate(part.inline_data.mime_type or "")
                self._on_event(TtsAudioChunk(
                    pcm_bytes=bytes(pcm),
                    sample_rate=sample_rate,
                    provider="gemini",
                    source="tts",
                ))

        self._on_event(TtsDone(provider="gemini"))

    async def close(self) -> None:
        pass
