# backend/vad.py
import numpy as np
import torch

class VAD:
    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.agent_playing: bool = False
        self._last_prob: float | None = None
        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        self._model = model
        self._model.eval()

    def process(self, pcm_bytes: bytes) -> float | None:
        if self.agent_playing:
            return None
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        # Silero needs at least 512 samples at 16kHz
        if len(samples) < 512:
            samples = np.pad(samples, (0, 512 - len(samples)))
        tensor = torch.from_numpy(samples).unsqueeze(0)
        with torch.no_grad():
            prob = self._model(tensor, self.sample_rate).item()
        self._last_prob = prob
        return prob

    def process_and_check(self, pcm_bytes: bytes) -> bool:
        prob = self.process(pcm_bytes)
        if prob is None:
            return False
        self._last_prob = prob
        return prob >= self.threshold
