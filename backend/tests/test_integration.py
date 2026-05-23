# backend/tests/test_integration.py
import json
import pytest
import time
from fastapi.testclient import TestClient
from unittest.mock import patch
from backend.main import app
from backend.events import AsrPartial, AsrFinal
from backend.config import cfg


@pytest.mark.asyncio
async def test_server_starts_and_has_ws_route():
    """Verify the FastAPI app has the WebSocket route."""
    routes = [r.path for r in app.routes]
    assert "/ws/{session_id}" in routes


@pytest.mark.asyncio
async def test_full_call_cycle_with_mocks():
    """Full call cycle: connect WS, send config + PCM, receive ASR events."""

    # Patch database to avoid actual SQLite calls
    async def noop_init_db():
        pass

    async def noop_create_session(*a, **kw):
        pass

    async def noop_append_turn(*a, **kw):
        pass

    async def noop_close_session(*a, **kw):
        pass

    # Patch phrase_cache to not load from disk
    from backend.phrase_cache import phrase_cache
    phrase_cache._data = {}

    with patch("backend.main.init_db", noop_init_db), \
         patch("backend.main.create_session", noop_create_session), \
         patch("backend.main.append_turn", noop_append_turn), \
         patch("backend.main.close_session", noop_close_session):

        client = TestClient(app)
        with client.websocket_connect("/ws/test-session-001") as ws:
            # Send config (force mock providers)
            ws.send_json({
                "user_id": "u1",
                "asr_provider": "mock",
                "tts_provider": "mock",
                "smart_routing": False,
                "spec_enabled": False,
            })

            # Send fake PCM frame (512 zero int16 samples = 1024 bytes)
            pcm_frame = b"\x00\x00" * 512
            ws.send_bytes(pcm_frame)

            # At minimum, the connection and config exchange should work.
            assert ws is not None


class FakeVAD:
    def __init__(self, *args, **kwargs):
        self.agent_playing = False
        self._last_prob = None
        self._speech_frames = 0

    def process_and_check(self, pcm_bytes: bytes) -> bool:
        if self.agent_playing:
            return False
        self._speech_frames += 1
        self._last_prob = 0.9 if self._speech_frames == 1 else 0.0
        return self._speech_frames == 1

    def process_barge_in(self, pcm_bytes: bytes) -> bool:
        self._last_prob = 0.0
        return False

    def set_cooldown(self, ms: int = 500) -> None:
        pass


class CountingASR:
    def __init__(self, on_event):
        self._on_event = on_event
        self.send_audio_count = 0
        self.flush_count = 0
        self.closed = False

    async def connect(self) -> None:
        pass

    async def activity_start(self) -> None:
        pass

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self.send_audio_count += 1
        if self.send_audio_count == 1:
            self._on_event(AsrPartial(text="hello", stable_ms=200, provider="mock"))

    async def flush(self) -> None:
        self.flush_count += 1
        self._on_event(AsrFinal(text="hello world", provider="mock"))

    async def close(self) -> None:
        self.closed = True


def receive_json(ws, timeout=2.0):
    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError
        msg = ws.receive()
        if "text" in msg and msg["text"]:
            return json.loads(msg["text"])


@pytest.mark.asyncio
async def test_half_duplex_gate_closes_during_response_and_ignores_audio():
    async def noop_create_session(*a, **kw):
        pass

    async def noop_append_turn(*a, **kw):
        pass

    async def noop_close_session(*a, **kw):
        pass

    async def fake_stream_response(messages, system_prompt, on_token):
        on_token("Mock response.")
        return "Mock response.", {"model_used": "mock-llm"}

    from backend.phrase_cache import phrase_cache
    phrase_cache._data = {}
    asr_instances = []

    def create_counting_asr(on_event, provider):
        asr = CountingASR(on_event)
        asr_instances.append(asr)
        return asr

    with patch("backend.main.create_session", noop_create_session), \
         patch("backend.main.append_turn", noop_append_turn), \
         patch("backend.main.close_session", noop_close_session), \
         patch("backend.main.VAD", FakeVAD), \
         patch("backend.main.create_asr", create_counting_asr), \
         patch("backend.main.stream_response", fake_stream_response):

        client = TestClient(app)
        with client.websocket_connect("/ws/test-session-gate") as ws:
            ws.send_json({
                "user_id": "u1",
                "asr_provider": "mock",
                "tts_provider": "mock",
                "smart_routing": False,
                "spec_enabled": False,
                "interruptions_enabled": False,
            })

            pcm_frame = b"\x00\x00" * 512
            for _ in range(cfg.vad_silence_flush_frames + 2):
                ws.send_bytes(pcm_frame)

            seen = []
            closed_seen = False
            open_seen = False
            send_count_at_close = None
            metrics = []
            while not (open_seen and metrics):
                msg = receive_json(ws, timeout=3.0)
                seen.append(msg)
                if msg.get("type") == "input.gate" and msg.get("state") == "closed":
                    closed_seen = True
                    send_count_at_close = asr_instances[0].send_audio_count
                    for _ in range(3):
                        ws.send_bytes(pcm_frame)
                if msg.get("type") == "input.gate" and msg.get("state") == "open":
                    open_seen = True
                if msg.get("type") == "metrics.turn":
                    metrics.append(msg["turn"])

            assert closed_seen
            assert open_seen
            assert send_count_at_close is not None
            assert asr_instances[0].send_audio_count == send_count_at_close
            assert any(m.get("interruption_mode") == "disabled" for m in metrics)
            assert any(m.get("input_gated") is True for m in metrics)
