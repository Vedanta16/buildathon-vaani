import base64
from typing import Callable

from google import genai
from google.genai import types

from backend.config import cfg
from backend.events import TtsAudioChunk, TtsDone


class GeminiTTS:
    """
    Non-streaming TTS via Gemini generateContent with audio modalities.
    Higher TTFB than OpenAI — useful as a demo contrast point.
    Emits full audio per sentence as TtsAudioChunk + TtsDone.
    """

    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = genai.Client(api_key=cfg.gemini_api_key)

    async def synthesize(self, text: str) -> None:
        if not text.strip():
            return
        response = await self._client.aio.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="Aoede"
                        )
                    )
                ),
            ),
        )
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                    pcm = base64.b64decode(part.inline_data.data)
                    self._on_event(TtsAudioChunk(
                        pcm_bytes=pcm,
                        sample_rate=24000,
                        provider="gemini",
                        source="tts",
                    ))
        self._on_event(TtsDone(provider="gemini"))

    async def close(self) -> None:
        pass
