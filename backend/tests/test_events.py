# backend/tests/test_events.py
from backend.events import AsrPartial, AsrFinal, TtsAudioChunk, TtsDone, PlaybackCancel, BargeIn, RecordingSegment

def test_asr_partial_fields():
    e = AsrPartial(text="hello", stable_ms=200, provider="openai_realtime")
    assert e.text == "hello"
    assert e.stable_ms == 200
    assert e.provider == "openai_realtime"

def test_tts_audio_chunk_source():
    e = TtsAudioChunk(pcm_bytes=b"\x00\x01", sample_rate=16000, provider="openai", source="phrase_cache")
    assert e.source == "phrase_cache"

def test_tts_audio_chunk_default_source():
    e = TtsAudioChunk(pcm_bytes=b"\x00\x01", sample_rate=16000, provider="openai")
    assert e.source == "tts"

def test_recording_segment_fields():
    e = RecordingSegment(speaker="user", pcm_bytes=b"\x00", ts_ms=100)
    assert e.speaker == "user"
