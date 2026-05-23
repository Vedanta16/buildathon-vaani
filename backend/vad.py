# backend/vad.py
import time
import numpy as np
import torch


class VAD:
    # Silero requires exactly 512 samples at 16kHz, 256 at 8kHz
    _CHUNK_SAMPLES = {16000: 512, 8000: 256}

    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.agent_playing: bool = False
        self._last_prob: float | None = None
        self._cooldown_until: float = 0.0
        self._was_paused: bool = False

        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        self._model = model
        self._model.eval()
        self._chunk = self._CHUNK_SAMPLES.get(sample_rate, 512)

    def set_cooldown(self, ms: int = 500) -> None:
        """Suppress VAD for `ms` ms after TTS ends to let speaker echo clear."""
        self._cooldown_until = time.monotonic() + ms / 1000.0

    def process(self, pcm_bytes: bytes, *, ignore_agent_playing: bool = False, ignore_cooldown: bool = False) -> float | None:
        paused = (
            (self.agent_playing and not ignore_agent_playing)
            or (time.monotonic() < self._cooldown_until and not ignore_cooldown)
        )

        if paused:
            self._was_paused = True
            return None

        # Reset RNN state after any pause so stale hidden state doesn't corrupt scores
        if self._was_paused:
            self._model.reset_states()
            self._was_paused = False

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Silero requires exactly chunk samples — trim or pad
        if len(samples) < self._chunk:
            samples = np.pad(samples, (0, self._chunk - len(samples)))
        elif len(samples) > self._chunk:
            samples = samples[: self._chunk]

        tensor = torch.from_numpy(samples).unsqueeze(0)
        with torch.no_grad():
            prob = self._model(tensor, self.sample_rate).item()
        self._last_prob = prob
        return prob

    def process_and_check(self, pcm_bytes: bytes, *, ignore_agent_playing: bool = False, ignore_cooldown: bool = False) -> bool:
        prob = self.process(
            pcm_bytes,
            ignore_agent_playing=ignore_agent_playing,
            ignore_cooldown=ignore_cooldown,
        )
        if prob is None:
            return False
        self._last_prob = prob
        return prob >= self.threshold

    def process_barge_in(self, pcm_bytes: bytes) -> bool:
        """Detect user speech during playback without the normal playback/cooldown gate."""
        return self.process_and_check(
            pcm_bytes,
            ignore_agent_playing=True,
            ignore_cooldown=True,
        )
