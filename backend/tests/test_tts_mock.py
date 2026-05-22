# backend/tests/test_tts_mock.py
import asyncio
import pytest
from backend.providers.tts.mock import MockTTS
from backend.events import TtsAudioChunk, TtsDone

@pytest.mark.asyncio
async def test_mock_tts_emits_chunk_and_done():
    events = []
    tts = MockTTS(on_event=lambda e: events.append(e))
    await tts.synthesize("Hello, how are you?")
    assert any(isinstance(e, TtsAudioChunk) for e in events)
    assert any(isinstance(e, TtsDone) for e in events)
    chunk = next(e for e in events if isinstance(e, TtsAudioChunk))
    assert chunk.provider == "mock"
    assert chunk.source == "tts"
    assert len(chunk.pcm_bytes) > 0

@pytest.mark.asyncio
async def test_mock_tts_close():
    tts = MockTTS(on_event=lambda e: None)
    await tts.close()  # should not raise
