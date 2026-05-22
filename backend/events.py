# backend/events.py
from dataclasses import dataclass, field

@dataclass
class AsrPartial:
    text: str
    stable_ms: int
    provider: str

@dataclass
class AsrFinal:
    text: str
    provider: str

@dataclass
class TtsAudioChunk:
    pcm_bytes: bytes
    sample_rate: int
    provider: str
    source: str = "tts"  # "tts" | "phrase_cache" | "filler"

@dataclass
class TtsDone:
    provider: str

@dataclass
class PlaybackCancel:
    pass

@dataclass
class BargeIn:
    ts_ms: int

@dataclass
class RecordingSegment:
    speaker: str  # "user" | "agent"
    pcm_bytes: bytes
    ts_ms: int
