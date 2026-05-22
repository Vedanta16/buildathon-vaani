# backend/recording.py
import os
import wave
from pathlib import Path
from backend.config import cfg

class Recorder:
    def __init__(self, session_id: str, base_dir: str = cfg.recordings_dir, sample_rate: int = 16000):
        self._session_id = session_id
        self._sample_rate = sample_rate
        self._dir = Path(base_dir) / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._user_frames: list[tuple[int, bytes]] = []
        self._agent_frames: list[tuple[int, bytes]] = []

    def append_user(self, pcm_bytes: bytes, ts_ms: int) -> None:
        self._user_frames.append((ts_ms, pcm_bytes))

    def append_agent(self, pcm_bytes: bytes, ts_ms: int) -> None:
        self._agent_frames.append((ts_ms, pcm_bytes))

    def stitch(self) -> str:
        """Stitch all frames into a mono WAV with gap padding. Returns path."""
        out_path = str(self._dir / "call.wav")
        all_frames = sorted(
            [(ts, pcm) for ts, pcm in self._user_frames] +
            [(ts, pcm) for ts, pcm in self._agent_frames],
            key=lambda x: x[0],
        )
        if not all_frames:
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self._sample_rate)
                wf.writeframes(b"")
            return out_path

        samples_per_ms = self._sample_rate // 1000
        output_samples: list[bytes] = []
        cursor_ms = 0

        for ts_ms, pcm in all_frames:
            gap_ms = max(0, ts_ms - cursor_ms)
            if gap_ms > 0:
                silence = b"\x00\x00" * (gap_ms * samples_per_ms)
                output_samples.append(silence)
            output_samples.append(pcm)
            duration_ms = len(pcm) // (2 * samples_per_ms) if samples_per_ms > 0 else 0
            cursor_ms = ts_ms + duration_ms

        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(b"".join(output_samples))

        return out_path
