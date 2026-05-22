import asyncio
import pytest
from backend.providers.asr.mock import MockASR
from backend.events import AsrPartial, AsrFinal

@pytest.mark.asyncio
async def test_mock_asr_emits_partial_then_final():
    events = []
    asr = MockASR(on_event=lambda e: events.append(e))
    await asr.send_audio(b"\x00" * 512)
    await asr.flush()
    assert any(isinstance(e, AsrPartial) for e in events)
    assert any(isinstance(e, AsrFinal) for e in events)
    final = next(e for e in events if isinstance(e, AsrFinal))
    assert final.provider == "mock"

@pytest.mark.asyncio
async def test_mock_asr_connect_and_close():
    asr = MockASR(on_event=lambda e: None)
    await asr.connect()  # should not raise
    await asr.close()    # should not raise
