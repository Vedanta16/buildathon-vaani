# backend/filler.py
import random
from pathlib import Path
from backend.config import cfg
from backend.events import TtsAudioChunk

def get_filler_chunk() -> TtsAudioChunk | None:
    """Return a random filler clip if available, else None."""
    filler_dir = Path(cfg.filler_dir)
    if not filler_dir.exists():
        return None
    files = list(filler_dir.glob("*.pcm"))
    if not files:
        return None
    chosen = random.choice(files)
    pcm = chosen.read_bytes()
    return TtsAudioChunk(
        pcm_bytes=pcm,
        sample_rate=cfg.sample_rate,
        provider="cache",
        source="filler",
    )
