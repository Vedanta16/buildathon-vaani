# backend/phrase_cache.py
import re
import pickle
from difflib import SequenceMatcher
from pathlib import Path
from backend.config import cfg

def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

class PhraseCache:
    def __init__(self):
        self._data: dict[str, bytes] = {}

    def load(self, cache_dir: str = cfg.phrase_cache_dir) -> None:
        path = Path(cache_dir) / "phrases.pkl"
        if path.exists():
            with open(path, "rb") as f:
                self._data = pickle.load(f)

    def save(self, cache_dir: str = cfg.phrase_cache_dir) -> None:
        path = Path(cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "phrases.pkl", "wb") as f:
            pickle.dump(self._data, f)

    def add(self, phrase: str, pcm_bytes: bytes) -> None:
        self._data[normalize(phrase)] = pcm_bytes

    def lookup(self, sentence: str) -> bytes | None:
        key = normalize(sentence)

        if key in self._data:
            return self._data[key]

        if not self._data:
            return None

        best_ratio = 0.0
        best_pcm = None
        for cached_key, pcm in self._data.items():
            ratio = SequenceMatcher(None, key, cached_key).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_pcm = pcm

        if best_ratio >= cfg.phrase_cache_ratio:
            return best_pcm
        return None

phrase_cache = PhraseCache()
