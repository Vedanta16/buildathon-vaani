# backend/tests/test_vad.py
import numpy as np
import pytest
import torch
from backend.vad import VAD


class FakeSileroModel:
    def eval(self):
        return self

    def reset_states(self):
        pass

    def __call__(self, tensor, sample_rate):
        return torch.tensor(0.0)


@pytest.fixture
def vad(monkeypatch):
    monkeypatch.setattr("backend.vad.torch.hub.load", lambda *a, **kw: (FakeSileroModel(), None))
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

def test_vad_barge_in_path_processes_while_agent_playing(vad):
    vad.agent_playing = True
    silence = np.zeros(512, dtype=np.int16).tobytes()
    result = vad.process_barge_in(silence)
    assert result is False
    assert vad._last_prob is not None
