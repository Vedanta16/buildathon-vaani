# backend/tests/test_recording.py
import os
import wave
import tempfile
import pytest
from backend.recording import Recorder

def test_stitch_creates_wav():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = Recorder(session_id="test-session", base_dir=tmpdir)
        rec.append_user(b"\x00\x01" * 512, ts_ms=0)
        rec.append_agent(b"\x00\x02" * 512, ts_ms=300)
        rec.append_user(b"\x00\x03" * 512, ts_ms=600)
        rec.append_agent(b"\x00\x04" * 512, ts_ms=900)
        out_path = rec.stitch()
        assert os.path.exists(out_path)
        with wave.open(out_path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getnframes() > 0

def test_stitch_empty_creates_valid_wav():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = Recorder(session_id="empty-session", base_dir=tmpdir)
        out_path = rec.stitch()
        assert os.path.exists(out_path)
        with wave.open(out_path, "rb") as wf:
            assert wf.getnchannels() == 1
