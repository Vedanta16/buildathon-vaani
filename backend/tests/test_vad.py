# backend/tests/test_vad.py
import numpy as np
import pytest
from backend.vad import VAD

@pytest.fixture
def vad():
    return VAD(sample_rate=16000, threshold=0.5)

def test_vad_skips_frames_when_agent_playing(vad):
    vad.agent_playing = True
    silence = np.zeros(512, dtype=np.int16)
    result = vad.process(silence.tobytes())
    assert result is None  # gated — no VAD output

def test_vad_processes_frames_when_agent_not_playing(vad):
    vad.agent_playing = False
    silence = np.zeros(512, dtype=np.int16).tobytes()
    result = vad.process(silence)
    assert result is not None  # returns a float (speech probability)
    assert result < 0.1

def test_vad_agent_playing_defaults_false(vad):
    assert vad.agent_playing is False
