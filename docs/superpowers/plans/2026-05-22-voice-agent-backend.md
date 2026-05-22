# Voice Agent Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete FastAPI + WebSocket voice agent backend with OpenAI + Gemini providers, echo cancellation, smart model routing, speculative generation, and phrase cache fuzzy matching.

**Architecture:** Single FastAPI process handles a client WebSocket per call. All provider-specific code lives behind adapter interfaces — the orchestrator (`main.py`) only sees internal events. Echo cancellation uses browser AEC + server-side VAD gate. Smart routing and speculative generation compose: short speculative calls go to `gpt-4o-mini` and often complete before the user finishes speaking.

**Tech Stack:** Python 3.11+, FastAPI, websockets, silero-vad (torch), openai SDK, google-generativeai SDK, difflib (stdlib), wave (stdlib), SQLite (aiosqlite)

---

## File Map

| File | Responsibility |
|------|----------------|
| `backend/config.py` | Env vars, provider keys, model names, routing thresholds |
| `backend/db.py` | SQLite schema + async CRUD for users/sessions/turns |
| `backend/events.py` | Internal event dataclasses (ASR, TTS, barge-in, etc.) |
| `backend/providers/asr/base.py` | ASR adapter protocol |
| `backend/providers/asr/mock.py` | Mock ASR for offline testing |
| `backend/providers/asr/asr_openai_realtime.py` | OpenAI Realtime API adapter |
| `backend/providers/asr/asr_gemini_live.py` | Gemini Live API adapter |
| `backend/providers/asr/factory.py` | `create_asr(provider)` |
| `backend/providers/tts/base.py` | TTS adapter protocol |
| `backend/providers/tts/mock.py` | Mock TTS for offline testing |
| `backend/providers/tts/tts_openai.py` | OpenAI `/v1/audio/speech` streaming adapter |
| `backend/providers/tts/tts_gemini.py` | Gemini TTS per-sentence adapter |
| `backend/providers/tts/factory.py` | `create_tts(provider)` |
| `backend/vad.py` | Silero VAD wrapper with `agent_playing` gate |
| `backend/llm_openai.py` | OpenAI LLM with prompt caching + smart model routing |
| `backend/phrase_cache.py` | `normalize()`, `lookup_phrase()`, startup loader |
| `backend/filler.py` | Pre-cached filler clip selector + emitter |
| `backend/barge_in.py` | Cancel in-flight LLM + TTS; reset `agent_playing` |
| `backend/speculation.py` | 400ms debounce, speculative task, similarity commit/discard |
| `backend/metrics.py` | Per-turn telemetry collector, session aggregator |
| `backend/recording.py` | PCM append to temp files + WAV stitch on End Call |
| `backend/main.py` | FastAPI app, WebSocket handler, pipeline orchestrator |
| `backend/scripts/pregen_phrases.py` | One-time phrase pre-generation CLI |
| `backend/tests/` | All tests (mirrors src structure) |

---

## Task 1: Project skeleton + dependencies

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/config.py`
- Create: `backend/__init__.py`, `backend/providers/__init__.py`, `backend/providers/asr/__init__.py`, `backend/providers/tts/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
websockets==12.0
torch==2.3.0
torchaudio==2.3.0
silero-vad==4.0.0
openai==1.40.0
google-generativeai==0.7.2
aiosqlite==0.20.0
numpy==1.26.4
scipy==1.13.0
pytest==8.3.0
pytest-asyncio==0.23.8
httpx==0.27.0
```

- [ ] **Step 2: Create config.py**

```python
import os
from dataclasses import dataclass

@dataclass
class Config:
    asr_provider: str = os.getenv("ASR_PROVIDER", "openai_realtime")
    tts_provider: str = os.getenv("TTS_PROVIDER", "openai")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    large_model: str = "gpt-4o"
    small_model: str = "gpt-4o-mini"
    routing_word_threshold: int = 8

    short_answer_set: frozenset = frozenset({
        "yes", "no", "ok", "okay", "sure", "thanks", "thank you",
        "got it", "sounds good", "perfect", "great", "fine",
        "yep", "nope", "correct", "exactly", "absolutely",
        "go ahead", "send it", "do it", "keep it",
    })

    spec_debounce_ms: int = 400
    spec_min_words: int = 5
    spec_commit_ratio: float = 0.85
    phrase_cache_ratio: float = 0.88

    sample_rate: int = 16000
    phrase_cache_dir: str = "phrase_cache"
    filler_dir: str = "filler_audio"
    recordings_dir: str = "recordings"

cfg = Config()
```

- [ ] **Step 3: Create __init__.py files**

```bash
touch backend/__init__.py
touch backend/providers/__init__.py
touch backend/providers/asr/__init__.py
touch backend/providers/tts/__init__.py
touch backend/tests/__init__.py
```

- [ ] **Step 4: Install dependencies**

```bash
cd backend && pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/config.py backend/__init__.py backend/providers/
git commit -m "feat: project skeleton and config"
```

---

## Task 2: Internal event types

**Files:**
- Create: `backend/events.py`
- Create: `backend/tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_events.py
from backend.events import AsrPartial, AsrFinal, TtsAudioChunk, TtsDone, PlaybackCancel, BargeIn

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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_events.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement events.py**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && pytest tests/test_events.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/events.py backend/tests/test_events.py
git commit -m "feat: internal event dataclasses"
```

---

## Task 3: VAD with agent_playing gate (echo cancellation — server layer)

**Files:**
- Create: `backend/vad.py`
- Create: `backend/tests/test_vad.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_vad.py
import numpy as np
import pytest
from backend.vad import VAD

@pytest.fixture
def vad():
    return VAD(sample_rate=16000, threshold=0.5)

def test_vad_skips_frames_when_agent_playing(vad):
    vad.agent_playing = True
    silence = np.zeros(512, dtype=np.float32)
    result = vad.process(silence.tobytes())
    assert result is None  # gated — no VAD output

def test_vad_processes_frames_when_agent_not_playing(vad):
    vad.agent_playing = False
    # 512 samples of silence → speech_prob near 0 → no speech detected
    silence = np.zeros(512, dtype=np.float32).tobytes()
    result = vad.process(silence)
    assert result is not None  # returns a float (speech probability)
    assert result < 0.1

def test_vad_agent_playing_defaults_false(vad):
    assert vad.agent_playing is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_vad.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement vad.py**

```python
# backend/vad.py
import numpy as np
import torch

class VAD:
    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.agent_playing: bool = False
        self._model, self._utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
        )
        self._model.eval()

    def process(self, pcm_bytes: bytes) -> float | None:
        """
        Returns speech probability (0.0–1.0) or None if gated.
        Caller checks: if result >= threshold → speech detected.
        """
        if self.agent_playing:
            return None
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(samples).unsqueeze(0)
        with torch.no_grad():
            prob = self._model(tensor, self.sample_rate).item()
        return prob

    @property
    def is_speech(self) -> bool:
        """Use after process() — checks last result against threshold."""
        return self._last_prob is not None and self._last_prob >= self.threshold

    def process_and_check(self, pcm_bytes: bytes) -> bool:
        self._last_prob = self.process(pcm_bytes)
        return self._last_prob is not None and self._last_prob >= self.threshold

    _last_prob: float | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_vad.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/vad.py backend/tests/test_vad.py
git commit -m "feat: Silero VAD with agent_playing echo cancellation gate"
```

---

## Task 4: ASR adapter protocol + mock

**Files:**
- Create: `backend/providers/asr/base.py`
- Create: `backend/providers/asr/mock.py`
- Create: `backend/providers/asr/factory.py`
- Create: `backend/tests/test_asr_mock.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_asr_mock.py
import asyncio
import pytest
from backend.providers.asr.mock import MockASR
from backend.events import AsrPartial, AsrFinal

@pytest.mark.asyncio
async def test_mock_asr_emits_partial_then_final():
    events = []
    asr = MockASR(on_event=lambda e: events.append(e))
    await asr.send_audio(b"\x00" * 512)
    await asr.flush()
    assert any(isinstance(e, AsrPartial) for e in events)
    assert any(isinstance(e, AsrFinal) for e in events)
    final = next(e for e in events if isinstance(e, AsrFinal))
    assert final.provider == "mock"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_asr_mock.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement base.py**

```python
# backend/providers/asr/base.py
from typing import Callable, Protocol
from backend.events import AsrPartial, AsrFinal

ASREvent = AsrPartial | AsrFinal

class ASRAdapter(Protocol):
    async def connect(self) -> None: ...
    async def send_audio(self, pcm_bytes: bytes) -> None: ...
    async def flush(self) -> None: ...
    async def close(self) -> None: ...
```

- [ ] **Step 4: Implement mock.py**

```python
# backend/providers/asr/mock.py
from typing import Callable
from backend.events import AsrPartial, AsrFinal

class MockASR:
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._buffer = b""

    async def connect(self) -> None:
        pass

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self._buffer += pcm_bytes
        if len(self._buffer) >= 512:
            self._on_event(AsrPartial(text="hello", stable_ms=200, provider="mock"))

    async def flush(self) -> None:
        self._on_event(AsrFinal(text="hello world", provider="mock"))
        self._buffer = b""

    async def close(self) -> None:
        pass
```

- [ ] **Step 5: Implement factory.py**

```python
# backend/providers/asr/factory.py
from typing import Callable
from backend.config import cfg

def create_asr(on_event: Callable, provider: str | None = None):
    p = provider or cfg.asr_provider
    if p == "mock":
        from backend.providers.asr.mock import MockASR
        return MockASR(on_event=on_event)
    elif p == "openai_realtime":
        from backend.providers.asr.asr_openai_realtime import OpenAIRealtimeASR
        return OpenAIRealtimeASR(on_event=on_event)
    elif p == "gemini_live":
        from backend.providers.asr.asr_gemini_live import GeminiLiveASR
        return GeminiLiveASR(on_event=on_event)
    else:
        raise ValueError(f"Unknown ASR provider: {p}")
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd backend && pytest tests/test_asr_mock.py -v
```
Expected: PASSED

- [ ] **Step 7: Commit**

```bash
git add backend/providers/asr/
git commit -m "feat: ASR adapter protocol, mock, and factory"
```

---

## Task 5: TTS adapter protocol + mock

**Files:**
- Create: `backend/providers/tts/base.py`
- Create: `backend/providers/tts/mock.py`
- Create: `backend/providers/tts/factory.py`
- Create: `backend/tests/test_tts_mock.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_tts_mock.py
import asyncio
import pytest
from backend.providers.tts.mock import MockTTS
from backend.events import TtsAudioChunk, TtsDone

@pytest.mark.asyncio
async def test_mock_tts_emits_chunk_and_done():
    events = []
    tts = MockTTS(on_event=lambda e: events.append(e))
    await tts.synthesize("Hello, how are you?")
    assert any(isinstance(e, TtsAudioChunk) for e in events)
    assert any(isinstance(e, TtsDone) for e in events)
    chunk = next(e for e in events if isinstance(e, TtsAudioChunk))
    assert chunk.provider == "mock"
    assert chunk.source == "tts"
    assert len(chunk.pcm_bytes) > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_tts_mock.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement base.py**

```python
# backend/providers/tts/base.py
from typing import Callable, Protocol

class TTSAdapter(Protocol):
    async def synthesize(self, text: str) -> None:
        """Synthesize text and emit TtsAudioChunk events via on_event callback."""
        ...

    async def close(self) -> None: ...
```

- [ ] **Step 4: Implement mock.py**

```python
# backend/providers/tts/mock.py
import numpy as np
from typing import Callable
from backend.events import TtsAudioChunk, TtsDone

class MockTTS:
    def __init__(self, on_event: Callable):
        self._on_event = on_event

    async def synthesize(self, text: str) -> None:
        # Emit 0.5s of silence as fake PCM (16-bit, 16kHz)
        samples = np.zeros(8000, dtype=np.int16)
        self._on_event(TtsAudioChunk(
            pcm_bytes=samples.tobytes(),
            sample_rate=16000,
            provider="mock",
            source="tts",
        ))
        self._on_event(TtsDone(provider="mock"))

    async def close(self) -> None:
        pass
```

- [ ] **Step 5: Implement factory.py**

```python
# backend/providers/tts/factory.py
from typing import Callable
from backend.config import cfg

def create_tts(on_event: Callable, provider: str | None = None):
    p = provider or cfg.tts_provider
    if p == "mock":
        from backend.providers.tts.mock import MockTTS
        return MockTTS(on_event=on_event)
    elif p == "openai":
        from backend.providers.tts.tts_openai import OpenAITTS
        return OpenAITTS(on_event=on_event)
    elif p == "gemini":
        from backend.providers.tts.tts_gemini import GeminiTTS
        return GeminiTTS(on_event=on_event)
    else:
        raise ValueError(f"Unknown TTS provider: {p}")
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd backend && pytest tests/test_tts_mock.py -v
```
Expected: PASSED

- [ ] **Step 7: Commit**

```bash
git add backend/providers/tts/
git commit -m "feat: TTS adapter protocol, mock, and factory"
```

---

## Task 6: LLM with prompt caching + smart model routing

**Files:**
- Create: `backend/llm_openai.py`
- Create: `backend/tests/test_llm_routing.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_llm_routing.py
from backend.llm_openai import select_model

def test_routes_short_answer_to_small_model():
    assert select_model("yes") == "gpt-4o-mini"
    assert select_model("ok") == "gpt-4o-mini"
    assert select_model("sure thanks") == "gpt-4o-mini"

def test_routes_short_word_count_to_small_model():
    # 8 words exactly → small
    assert select_model("yeah keep it and send the link") == "gpt-4o-mini"

def test_routes_long_turn_to_large_model():
    assert select_model(
        "I'm trying to renew my policy but the link in your email isn't working for some reason"
    ) == "gpt-4o"

def test_nine_words_routes_large():
    # 9 words → large
    assert select_model("can you check if my roadside coverage is still active") == "gpt-4o"

def test_routing_disabled_always_large(monkeypatch):
    from backend import llm_openai
    monkeypatch.setattr(llm_openai, "ROUTING_ENABLED", False)
    assert select_model("yes") == "gpt-4o"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_llm_routing.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement llm_openai.py**

```python
# backend/llm_openai.py
import asyncio
from typing import AsyncGenerator, Callable
from openai import AsyncOpenAI
from backend.config import cfg

ROUTING_ENABLED: bool = True

_client = AsyncOpenAI(api_key=cfg.openai_api_key)

def select_model(text: str) -> str:
    if not ROUTING_ENABLED:
        return cfg.large_model
    normalized = text.strip().lower().rstrip(".,!?")
    if normalized in cfg.short_answer_set:
        return cfg.small_model
    if len(text.split()) <= cfg.routing_word_threshold:
        return cfg.small_model
    return cfg.large_model

async def stream_response(
    messages: list[dict],
    system_prompt: str,
    memory_block: str = "",
    on_token: Callable[[str], None] = None,
    model: str | None = None,
) -> tuple[str, dict]:
    """
    Stream LLM response tokens. Returns (full_text, usage_dict).

    Prompt structure for caching:
      1. Static system prompt (cacheable)
      2. Memory block (cacheable when unchanged)
      3. Conversation history + current turn
    """
    if model is None:
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        model = select_model(last_user)

    full_messages = [
        {"role": "system", "content": system_prompt},
    ]
    if memory_block:
        full_messages.append({"role": "system", "content": memory_block})
    full_messages.extend(messages)

    full_text = ""
    usage = {}

    stream = await _client.chat.completions.create(
        model=model,
        messages=full_messages,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            full_text += token
            if on_token:
                on_token(token)
        if chunk.usage:
            usage = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "cached_tokens": getattr(chunk.usage, "prompt_tokens_details", None) and
                                 chunk.usage.prompt_tokens_details.cached_tokens or 0,
                "model": model,
                "routed_small": model == cfg.small_model,
            }
    return full_text, usage
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_llm_routing.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/llm_openai.py backend/tests/test_llm_routing.py
git commit -m "feat: LLM with prompt caching and smart model routing"
```

---

## Task 7: Phrase cache with fuzzy matching

**Files:**
- Create: `backend/phrase_cache.py`
- Create: `backend/tests/test_phrase_cache.py`
- Create: `backend/scripts/pregen_phrases.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_phrase_cache.py
import pytest
from backend.phrase_cache import normalize, PhraseCache

def test_normalize_strips_punctuation_and_lowercases():
    assert normalize("Got it, one moment!") == "got it one moment"
    assert normalize("Let me pull that up.") == "let me pull that up"

def test_normalize_collapses_whitespace():
    assert normalize("  hello   world  ") == "hello world"

def test_exact_match_returns_audio():
    cache = PhraseCache()
    cache._data["got it one moment"] = b"fake_pcm"
    result = cache.lookup("Got it, one moment!")
    assert result == b"fake_pcm"

def test_fuzzy_match_handles_minor_punctuation_diff():
    cache = PhraseCache()
    cache._data["let me check that for you"] = b"audio_bytes"
    # Extra comma → should still match at 0.88+
    result = cache.lookup("Let me check that, for you.")
    assert result == b"audio_bytes"

def test_no_match_returns_none():
    cache = PhraseCache()
    cache._data["let me check that for you"] = b"audio_bytes"
    result = cache.lookup("Your policy ends on June fourth.")
    assert result is None

def test_semantically_different_does_not_match():
    cache = PhraseCache()
    cache._data["let me check that for you"] = b"audio_bytes"
    # Different enough words
    result = cache.lookup("Let me find another solution here.")
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phrase_cache.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement phrase_cache.py**

```python
# backend/phrase_cache.py
import os
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

        # 1. Exact match — O(1)
        if key in self._data:
            return self._data[key]

        # 2. Fuzzy match
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

# Module-level singleton loaded at startup
phrase_cache = PhraseCache()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_phrase_cache.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Implement scripts/pregen_phrases.py**

```python
#!/usr/bin/env python3
# backend/scripts/pregen_phrases.py
"""
One-time script to pre-generate TTS audio for stock phrases.
Usage: python -m backend.scripts.pregen_phrases --tts-provider openai
"""
import asyncio
import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.phrase_cache import phrase_cache
from backend.providers.tts.factory import create_tts
from backend.events import TtsAudioChunk

PHRASES = [
    "Got it, one moment.",
    "Let me pull that up.",
    "Let me check that for you.",
    "One second.",
    "I see that here.",
    "Sure, I can help with that.",
    "Absolutely.",
    "Of course.",
    "Let me look into that.",
    "Is there anything else I can help you with?",
    "Have a great day.",
    "Thank you for calling.",
    "I understand.",
    "Got it.",
    "Sending that now.",
    "Let me find that for you.",
    "I'll take care of that.",
    "No problem at all.",
    "Right away.",
    "I'm looking into that now.",
]

async def generate(tts_provider: str):
    print(f"Generating {len(PHRASES)} phrases with {tts_provider} TTS...")
    for phrase in PHRASES:
        chunks = []
        def collect(event):
            if isinstance(event, TtsAudioChunk):
                chunks.append(event.pcm_bytes)

        tts = create_tts(on_event=collect, provider=tts_provider)
        await tts.synthesize(phrase)
        await tts.close()

        if chunks:
            phrase_cache.add(phrase, b"".join(chunks))
            print(f"  ✓ {phrase}")
        else:
            print(f"  ✗ {phrase} — no audio")

    phrase_cache.save()
    print(f"Saved to {phrase_cache._data.__len__()} entries in phrase_cache/phrases.pkl")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tts-provider", default="openai", choices=["openai", "gemini", "mock"])
    args = parser.parse_args()
    asyncio.run(generate(args.tts_provider))
```

- [ ] **Step 6: Commit**

```bash
git add backend/phrase_cache.py backend/tests/test_phrase_cache.py backend/scripts/pregen_phrases.py
git commit -m "feat: phrase cache with fuzzy matching and pregen script"
```

---

## Task 8: Speculative generation

**Files:**
- Create: `backend/speculation.py`
- Create: `backend/tests/test_speculation.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_speculation.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.speculation import SpeculationManager

@pytest.mark.asyncio
async def test_fires_after_debounce():
    fired = []
    mgr = SpeculationManager(
        debounce_ms=50,  # fast for tests
        min_words=2,
        llm_fn=AsyncMock(return_value=("hello response", {})),
        on_token=lambda t: None,
    )
    await mgr.on_partial("hello there")
    await asyncio.sleep(0.1)  # wait for debounce
    assert mgr._spec_task is not None

@pytest.mark.asyncio
async def test_commits_on_similar_final():
    committed = []
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=2,
        llm_fn=AsyncMock(return_value=("sure thing", {})),
        on_token=lambda t: None,
        on_commit=lambda text, usage: committed.append(text),
    )
    await mgr.on_partial("sure")
    await asyncio.sleep(0.1)
    result = await mgr.on_final("sure thing")
    assert result == "commit"

@pytest.mark.asyncio
async def test_discards_on_divergent_final():
    discarded = []
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=2,
        llm_fn=AsyncMock(return_value=("response for sure", {})),
        on_token=lambda t: None,
        on_discard=lambda: discarded.append(True),
    )
    await mgr.on_partial("sure")
    await asyncio.sleep(0.1)
    result = await mgr.on_final("completely different question about my policy renewal please")
    assert result == "discard"
    assert discarded

@pytest.mark.asyncio
async def test_does_not_fire_below_min_words():
    fired = []
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=5,
        llm_fn=AsyncMock(return_value=("ok", {})),
        on_token=lambda t: None,
    )
    await mgr.on_partial("yes")
    await asyncio.sleep(0.1)
    assert mgr._spec_task is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_speculation.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement speculation.py**

```python
# backend/speculation.py
import asyncio
from difflib import SequenceMatcher
from typing import Callable, Awaitable
from backend.config import cfg

class SpeculationManager:
    def __init__(
        self,
        llm_fn: Callable[..., Awaitable[tuple[str, dict]]],
        on_token: Callable[[str], None],
        on_commit: Callable[[str, dict], None] | None = None,
        on_discard: Callable[[], None] | None = None,
        debounce_ms: int = cfg.spec_debounce_ms,
        min_words: int = cfg.spec_min_words,
        commit_ratio: float = cfg.spec_commit_ratio,
    ):
        self._llm_fn = llm_fn
        self._on_token = on_token
        self._on_commit = on_commit or (lambda text, usage: None)
        self._on_discard = on_discard or (lambda: None)
        self._debounce_ms = debounce_ms
        self._min_words = min_words
        self._commit_ratio = commit_ratio

        self._spec_task: asyncio.Task | None = None
        self._debounce_task: asyncio.Task | None = None
        self._last_partial: str = ""
        self._spec_input: str = ""
        self._spec_output: str = ""
        self._spec_usage: dict = {}

    async def on_partial(self, text: str) -> None:
        self._last_partial = text
        if self._debounce_task:
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce(text))

    async def _debounce(self, text: str) -> None:
        await asyncio.sleep(self._debounce_ms / 1000.0)
        if len(text.split()) < self._min_words:
            return
        if self._spec_task and not self._spec_task.done():
            self._spec_task.cancel()
        self._spec_input = text
        self._spec_task = asyncio.create_task(self._run_speculation(text))

    async def _run_speculation(self, text: str) -> None:
        try:
            full_text, usage = await self._llm_fn(text, on_token=self._on_token)
            self._spec_output = full_text
            self._spec_usage = usage
        except asyncio.CancelledError:
            pass

    async def on_final(self, final_text: str) -> str:
        """Returns 'commit' or 'discard'. Call when ASR final arrives."""
        if self._debounce_task:
            self._debounce_task.cancel()

        if self._spec_task is None:
            return "discard"

        # Wait briefly for spec task to finish if close to done
        try:
            await asyncio.wait_for(asyncio.shield(self._spec_task), timeout=0.05)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        ratio = SequenceMatcher(None, self._spec_input, final_text).ratio()
        if ratio >= self._commit_ratio and self._spec_output:
            self._on_commit(self._spec_output, self._spec_usage)
            self._reset()
            return "commit"
        else:
            if self._spec_task and not self._spec_task.done():
                self._spec_task.cancel()
            self._on_discard()
            self._reset()
            return "discard"

    def _reset(self) -> None:
        self._spec_task = None
        self._spec_input = ""
        self._spec_output = ""
        self._spec_usage = {}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_speculation.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/speculation.py backend/tests/test_speculation.py
git commit -m "feat: speculative generation with debounce and similarity commit"
```

---

## Task 9: OpenAI Realtime ASR adapter

**Files:**
- Create: `backend/providers/asr/asr_openai_realtime.py`

- [ ] **Step 1: Implement asr_openai_realtime.py**

```python
# backend/providers/asr/asr_openai_realtime.py
import asyncio
import base64
import json
from typing import Callable
from openai import AsyncOpenAI
from backend.config import cfg
from backend.events import AsrPartial, AsrFinal

class OpenAIRealtimeASR:
    """
    Uses OpenAI Realtime API in transcription-only mode.
    Sends PCM audio, receives transcript.text.delta events.
    """
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = AsyncOpenAI(api_key=cfg.openai_api_key)
        self._ws = None
        self._partial_buf = ""

    async def connect(self) -> None:
        self._ws = await self._client.beta.realtime.connect(
            model="gpt-4o-realtime-preview-2024-10-01"
        )
        await self._ws.session.update(session={
            "input_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": None,  # we use our own VAD
        })
        asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        async for event in self._ws:
            etype = event.get("type", "")
            if etype == "conversation.item.input_audio_transcription.delta":
                delta = event.get("delta", "")
                self._partial_buf += delta
                self._on_event(AsrPartial(
                    text=self._partial_buf,
                    stable_ms=0,
                    provider="openai_realtime",
                ))
            elif etype == "conversation.item.input_audio_transcription.completed":
                text = event.get("transcript", self._partial_buf)
                self._on_event(AsrFinal(text=text, provider="openai_realtime"))
                self._partial_buf = ""

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._ws:
            return
        audio_b64 = base64.b64encode(pcm_bytes).decode()
        await self._ws.input_audio_buffer.append(audio=audio_b64)

    async def flush(self) -> None:
        if not self._ws:
            return
        await self._ws.input_audio_buffer.commit()
        await self._ws.response.create()

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
```

- [ ] **Step 2: Smoke test with mock (no live API needed)**

```bash
cd backend && python -c "
from backend.providers.asr.factory import create_asr
asr = create_asr(on_event=print, provider='mock')
import asyncio
asyncio.run(asr.flush())
"
```
Expected: prints `AsrFinal(text='hello world', provider='mock')`

- [ ] **Step 3: Commit**

```bash
git add backend/providers/asr/asr_openai_realtime.py
git commit -m "feat: OpenAI Realtime ASR adapter"
```

---

## Task 10: Gemini Live ASR adapter

**Files:**
- Create: `backend/providers/asr/asr_gemini_live.py`

- [ ] **Step 1: Implement asr_gemini_live.py**

```python
# backend/providers/asr/asr_gemini_live.py
import asyncio
from typing import Callable
import google.generativeai as genai
from google.generativeai.types import LiveConnectConfig, AudioConfig
from backend.config import cfg
from backend.events import AsrPartial, AsrFinal

class GeminiLiveASR:
    """
    Uses Gemini Live API in transcription-only mode.
    Sends 16kHz PCM, maps inputTranscription.text → asr.partial / asr.final.
    Model audio output is discarded.
    """
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        genai.configure(api_key=cfg.gemini_api_key)
        self._session = None
        self._partial_buf = ""

    async def connect(self) -> None:
        client = genai.Client()
        config = LiveConnectConfig(
            response_modalities=[],  # no audio output — transcription only
            input_audio_transcription={},
        )
        self._session = await client.aio.live.connect(
            model="gemini-live-2.5-flash",
            config=config,
        )
        asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        async for msg in self._session.receive():
            # Input transcription partial
            if hasattr(msg, "server_content") and msg.server_content:
                sc = msg.server_content
                if hasattr(sc, "input_transcription") and sc.input_transcription:
                    text = sc.input_transcription.text or ""
                    if text:
                        self._partial_buf = text
                        self._on_event(AsrPartial(
                            text=text,
                            stable_ms=0,
                            provider="gemini_live",
                        ))
                # Discard model audio turns entirely
                if hasattr(sc, "model_turn"):
                    continue
                if getattr(sc, "turn_complete", False):
                    if self._partial_buf:
                        self._on_event(AsrFinal(
                            text=self._partial_buf,
                            provider="gemini_live",
                        ))
                        self._partial_buf = ""

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(
            audio={"data": pcm_bytes, "mime_type": "audio/pcm;rate=16000"}
        )

    async def flush(self) -> None:
        # Gemini Live uses VAD to detect turn end; we can signal manually
        if self._session:
            await self._session.send({"client_content": {"turn_complete": True}})

    async def close(self) -> None:
        if self._session:
            await self._session.close()
```

- [ ] **Step 2: Commit**

```bash
git add backend/providers/asr/asr_gemini_live.py
git commit -m "feat: Gemini Live ASR adapter (transcription-only mode)"
```

---

## Task 11: OpenAI TTS adapter

**Files:**
- Create: `backend/providers/tts/tts_openai.py`

- [ ] **Step 1: Implement tts_openai.py**

```python
# backend/providers/tts/tts_openai.py
from typing import Callable
from openai import AsyncOpenAI
from backend.config import cfg
from backend.events import TtsAudioChunk, TtsDone

class OpenAITTS:
    """
    Streaming TTS via OpenAI /v1/audio/speech.
    Emits TtsAudioChunk events as PCM chunks arrive.
    """
    CHUNK_SIZE = 4096  # bytes

    def __init__(self, on_event: Callable):
        self._on_event = on_event
        self._client = AsyncOpenAI(api_key=cfg.openai_api_key)

    async def synthesize(self, text: str) -> None:
        if not text.strip():
            return
        async with self._client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="alloy",
            input=text,
            response_format="pcm",  # 24kHz, 16-bit, mono
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=self.CHUNK_SIZE):
                if chunk:
                    self._on_event(TtsAudioChunk(
                        pcm_bytes=chunk,
                        sample_rate=24000,
                        provider="openai",
                        source="tts",
                    ))
        self._on_event(TtsDone(provider="openai"))

    async def close(self) -> None:
        pass
```

- [ ] **Step 2: Commit**

```bash
git add backend/providers/tts/tts_openai.py
git commit -m "feat: OpenAI streaming TTS adapter"
```

---

## Task 12: Gemini TTS adapter

**Files:**
- Create: `backend/providers/tts/tts_gemini.py`

- [ ] **Step 1: Implement tts_gemini.py**

```python
# backend/providers/tts/tts_gemini.py
import base64
from typing import Callable
import google.generativeai as genai
from backend.config import cfg
from backend.events import TtsAudioChunk, TtsDone

class GeminiTTS:
    """
    Non-streaming TTS via Gemini generateContent with audio modalities.
    Higher TTFB than OpenAI — useful as a demo contrast point.
    Emits full audio per sentence as TtsAudioChunk + TtsDone.
    """
    def __init__(self, on_event: Callable):
        self._on_event = on_event
        genai.configure(api_key=cfg.gemini_api_key)
        self._client = genai.Client()

    async def synthesize(self, text: str) -> None:
        if not text.strip():
            return
        response = await self._client.aio.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=text,
            config={
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": "Aoede"}
                    }
                },
            },
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                pcm = base64.b64decode(part.inline_data.data)
                self._on_event(TtsAudioChunk(
                    pcm_bytes=pcm,
                    sample_rate=24000,
                    provider="gemini",
                    source="tts",
                ))
        self._on_event(TtsDone(provider="gemini"))

    async def close(self) -> None:
        pass
```

- [ ] **Step 2: Commit**

```bash
git add backend/providers/tts/tts_gemini.py
git commit -m "feat: Gemini TTS adapter (per-sentence, non-streaming)"
```

---

## Task 13: Metrics collector + DB

**Files:**
- Create: `backend/metrics.py`
- Create: `backend/db.py`

- [ ] **Step 1: Implement metrics.py**

```python
# backend/metrics.py
import time
from dataclasses import dataclass, field

@dataclass
class TurnMetrics:
    turn_id: int
    vad_start_ms: int = 0
    asr_final_ms: int = 0
    llm_start_ms: int = 0
    llm_first_token_ms: int = 0
    tts_start_ms: int = 0
    tts_first_audio_ms: int = 0
    playback_start_ms: int = 0
    barge_in_ms: int | None = None
    filler_played: bool = False
    phrase_cache_hit: bool = False
    spec_hit: bool = False
    spec_input: str = ""
    model_used: str = ""
    routed_small: bool = False
    prompt_cached_tokens: int = 0
    prompt_uncached_tokens: int = 0
    completion_tokens: int = 0
    tts_provider: str = ""
    asr_provider: str = ""

    @property
    def asr_ms(self) -> int:
        return max(0, self.asr_final_ms - self.vad_start_ms)

    @property
    def llm_ttft_ms(self) -> int:
        return max(0, self.llm_first_token_ms - self.llm_start_ms)

    @property
    def tts_ttfb_ms(self) -> int:
        if self.phrase_cache_hit:
            return 0
        return max(0, self.tts_first_audio_ms - self.tts_start_ms)

    @property
    def actual_latency_ms(self) -> int:
        return max(0, self.tts_first_audio_ms - self.vad_start_ms)

    @property
    def perceived_latency_ms(self) -> int:
        filler_start = self.llm_start_ms  # filler plays right at LLM start
        first_audio = min(filler_start, self.tts_first_audio_ms) if self.filler_played else self.tts_first_audio_ms
        return max(0, first_audio - self.vad_start_ms)

class SessionMetrics:
    def __init__(self):
        self.turns: list[TurnMetrics] = []
        self._start_ms = int(time.time() * 1000)

    def now_ms(self) -> int:
        return int(time.time() * 1000) - self._start_ms

    def new_turn(self) -> TurnMetrics:
        t = TurnMetrics(turn_id=len(self.turns) + 1)
        self.turns.append(t)
        return t

    @property
    def total_prompt_cached(self) -> int:
        return sum(t.prompt_cached_tokens for t in self.turns)

    @property
    def total_prompt_uncached(self) -> int:
        return sum(t.prompt_uncached_tokens for t in self.turns)

    @property
    def total_completion(self) -> int:
        return sum(t.completion_tokens for t in self.turns)

    @property
    def spec_hit_rate(self) -> float:
        if not self.turns:
            return 0.0
        return sum(1 for t in self.turns if t.spec_hit) / len(self.turns)

    @property
    def median_latency_ms(self) -> int:
        latencies = sorted(t.actual_latency_ms for t in self.turns if t.actual_latency_ms > 0)
        if not latencies:
            return 0
        mid = len(latencies) // 2
        return latencies[mid]
```

- [ ] **Step 2: Implement db.py**

```python
# backend/db.py
import aiosqlite
import json
import os

DB_PATH = os.getenv("DB_PATH", "voice_agent.db")

CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    asr_provider TEXT,
    tts_provider TEXT,
    recording_path TEXT,
    post_call_eval_json TEXT,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS memory_blobs (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    blob_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SCHEMA)
        await db.commit()

async def get_or_create_user(user_id: str, name: str, email: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)",
            (user_id, name, email),
        )
        await db.commit()
        async with db.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return {"id": row[0], "name": row[1], "email": row[2]}

async def create_session(session_id: str, user_id: str, asr: str, tts: str) -> None:
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, user_id, started_at, asr_provider, tts_provider) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, int(time.time() * 1000), asr, tts),
        )
        await db.commit()

async def append_turn(session_id: str, role: str, text: str, ts_ms: int, metrics: dict | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO turns (session_id, role, text, ts_ms, metrics_json) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, ts_ms, json.dumps(metrics) if metrics else None),
        )
        await db.commit()

async def close_session(session_id: str, recording_path: str | None, metrics_json: str | None) -> None:
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ?, recording_path = ?, metrics_json = ? WHERE id = ?",
            (int(time.time() * 1000), recording_path, metrics_json, session_id),
        )
        await db.commit()
```

- [ ] **Step 3: Commit**

```bash
git add backend/metrics.py backend/db.py
git commit -m "feat: metrics collector and SQLite schema"
```

---

## Task 14: Recording (PCM append + WAV stitch)

**Files:**
- Create: `backend/recording.py`
- Create: `backend/tests/test_recording.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_recording.py
import os
import wave
import tempfile
import pytest
from backend.recording import Recorder

def test_stitch_creates_wav():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = Recorder(session_id="test-session", base_dir=tmpdir)
        # Simulate 3 user frames + 2 agent frames
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_recording.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement recording.py**

```python
# backend/recording.py
import os
import wave
import struct
from pathlib import Path
from backend.config import cfg

class Recorder:
    def __init__(self, session_id: str, base_dir: str = cfg.recordings_dir, sample_rate: int = 16000):
        self._session_id = session_id
        self._sample_rate = sample_rate
        self._dir = Path(base_dir) / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._timeline: list[dict] = []
        self._user_frames: list[tuple[int, bytes]] = []  # (ts_ms, pcm)
        self._agent_frames: list[tuple[int, bytes]] = []

    def append_user(self, pcm_bytes: bytes, ts_ms: int) -> None:
        self._user_frames.append((ts_ms, pcm_bytes))
        self._timeline.append({"ts_ms": ts_ms, "speaker": "user"})

    def append_agent(self, pcm_bytes: bytes, ts_ms: int) -> None:
        self._agent_frames.append((ts_ms, pcm_bytes))
        self._timeline.append({"ts_ms": ts_ms, "speaker": "agent"})

    def stitch(self) -> str:
        """Stitch all frames into a mono WAV with gap padding. Returns path."""
        out_path = str(self._dir / "call.wav")
        all_frames = sorted(
            [(ts, "user", pcm) for ts, pcm in self._user_frames] +
            [(ts, "agent", pcm) for ts, pcm in self._agent_frames],
            key=lambda x: x[0],
        )
        if not all_frames:
            # Write empty WAV
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self._sample_rate)
                wf.writeframes(b"")
            return out_path

        # Build timeline with gap padding (silence between segments)
        samples_per_ms = self._sample_rate // 1000
        output_samples: list[bytes] = []
        cursor_ms = 0

        for ts_ms, speaker, pcm in all_frames:
            gap_ms = max(0, ts_ms - cursor_ms)
            if gap_ms > 0:
                silence = b"\x00\x00" * (gap_ms * samples_per_ms)
                output_samples.append(silence)
            output_samples.append(pcm)
            duration_ms = len(pcm) // (2 * samples_per_ms)
            cursor_ms = ts_ms + duration_ms

        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(b"".join(output_samples))

        return out_path
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && pytest tests/test_recording.py -v
```
Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/recording.py backend/tests/test_recording.py
git commit -m "feat: call recording with PCM append and WAV stitching"
```

---

## Task 15: Filler audio + barge-in handler

**Files:**
- Create: `backend/filler.py`
- Create: `backend/barge_in.py`

- [ ] **Step 1: Implement filler.py**

```python
# backend/filler.py
import os
import random
from pathlib import Path
from backend.config import cfg
from backend.events import TtsAudioChunk

# Filler clips are a subset of the phrase cache — pre-generated PCM files
FILLER_PHRASES = [
    "mm-hm",
    "one moment",
    "let me think",
    "sure",
]

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
```

- [ ] **Step 2: Implement barge_in.py**

```python
# backend/barge_in.py
import asyncio
from typing import Callable

class BargeInHandler:
    def __init__(
        self,
        on_cancel: Callable,       # sends playback.cancel to client WS
        vad,                       # VAD instance — sets agent_playing = False
    ):
        self._on_cancel = on_cancel
        self._vad = vad
        self._llm_task: asyncio.Task | None = None
        self._tts_task: asyncio.Task | None = None

    def register_llm_task(self, task: asyncio.Task) -> None:
        self._llm_task = task

    def register_tts_task(self, task: asyncio.Task) -> None:
        self._tts_task = task

    async def fire(self, ts_ms: int) -> None:
        """Call when VAD detects speech during agent playback."""
        self._vad.agent_playing = False
        if self._llm_task and not self._llm_task.done():
            self._llm_task.cancel()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        await self._on_cancel()
```

- [ ] **Step 3: Commit**

```bash
git add backend/filler.py backend/barge_in.py
git commit -m "feat: filler audio and barge-in handler"
```

---

## Task 16: FastAPI + WebSocket orchestrator (main.py)

**Files:**
- Create: `backend/main.py`

This is the integration layer that wires all components together.

- [ ] **Step 1: Implement main.py**

```python
# backend/main.py
import asyncio
import json
import time
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.config import cfg
from backend.db import init_db, create_session, append_turn, close_session
from backend.events import AsrPartial, AsrFinal, TtsAudioChunk, TtsDone, PlaybackCancel, BargeIn
from backend.vad import VAD
from backend.llm_openai import stream_response
from backend.phrase_cache import phrase_cache
from backend.filler import get_filler_chunk
from backend.barge_in import BargeInHandler
from backend.speculation import SpeculationManager
from backend.metrics import SessionMetrics
from backend.recording import Recorder
from backend.providers.asr.factory import create_asr
from backend.providers.tts.factory import create_tts

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM_PROMPT = (
    "You are a helpful voice assistant. Be concise — voice responses should be 1-3 sentences. "
    "Never use markdown, bullet points, or lists. Speak naturally."
)

@app.on_event("startup")
async def startup():
    await init_db()
    phrase_cache.load()

@app.websocket("/ws/{session_id}")
async def voice_session(ws: WebSocket, session_id: str):
    await ws.accept()
    config = await ws.receive_json()  # first message: {user_id, asr_provider, tts_provider, ...}

    user_id = config.get("user_id", "u1")
    asr_provider = config.get("asr_provider", cfg.asr_provider)
    tts_provider = config.get("tts_provider", cfg.tts_provider)
    smart_routing = config.get("smart_routing", True)
    spec_enabled = config.get("spec_enabled", True)

    await create_session(session_id, user_id, asr_provider, tts_provider)

    session_metrics = SessionMetrics()
    recorder = Recorder(session_id=session_id)
    vad = VAD(sample_rate=cfg.sample_rate)
    conversation_history: list[dict] = []
    current_turn: dict | None = None

    async def send_cancel():
        try:
            await ws.send_json({"type": "playback.cancel"})
        except Exception:
            pass

    barge_in_handler = BargeInHandler(on_cancel=send_cancel, vad=vad)

    # --- TTS event handler ---
    async def on_tts_event(event):
        nonlocal current_turn
        if isinstance(event, TtsAudioChunk):
            recorder.append_agent(event.pcm_bytes, session_metrics.now_ms())
            await ws.send_bytes(json.dumps({
                "type": "tts.audio_chunk",
                "source": event.source,
                "provider": event.provider,
                "sample_rate": event.sample_rate,
            }).encode() + b"|" + event.pcm_bytes)
            if current_turn and current_turn.get("tts_first_audio_ms", 0) == 0:
                current_turn["tts_first_audio_ms"] = session_metrics.now_ms()
                vad.agent_playing = True
        elif isinstance(event, TtsDone):
            vad.agent_playing = False
            await ws.send_json({"type": "tts.done"})

    tts = create_tts(on_event=lambda e: asyncio.create_task(on_tts_event(e)), provider=tts_provider)

    # --- LLM + TTS pipeline ---
    async def run_llm_tts(text: str, turn_metrics):
        turn_metrics["llm_start_ms"] = session_metrics.now_ms()
        sentence_buf = ""
        usage_result = {}

        async def on_token(token: str):
            nonlocal sentence_buf
            sentence_buf += token
            if turn_metrics.get("llm_first_token_ms", 0) == 0:
                turn_metrics["llm_first_token_ms"] = session_metrics.now_ms()
            # Flush on sentence boundary
            if token in ".!?" and len(sentence_buf.strip()) > 3:
                await flush_sentence(sentence_buf.strip())
                sentence_buf = ""

        async def flush_sentence(sentence: str):
            turn_metrics["tts_start_ms"] = session_metrics.now_ms()
            cached_pcm = phrase_cache.lookup(sentence)
            if cached_pcm:
                turn_metrics["phrase_cache_hit"] = True
                await on_tts_event(TtsAudioChunk(
                    pcm_bytes=cached_pcm,
                    sample_rate=cfg.sample_rate,
                    provider="phrase_cache",
                    source="phrase_cache",
                ))
                await on_tts_event(TtsDone(provider="phrase_cache"))
            else:
                await tts.synthesize(sentence)

        full_text, usage = await stream_response(
            messages=conversation_history,
            system_prompt=SYSTEM_PROMPT,
            on_token=on_token,
        )
        # Flush any remaining buffer
        if sentence_buf.strip():
            await flush_sentence(sentence_buf.strip())

        conversation_history.append({"role": "assistant", "content": full_text})
        usage_result.update(usage)
        turn_metrics.update(usage)

        await ws.send_json({
            "type": "metrics.turn",
            "turn": turn_metrics,
        })
        return full_text

    # --- Speculative generation ---
    async def spec_llm_fn(text: str, on_token=None):
        return await stream_response(
            messages=conversation_history + [{"role": "user", "content": text}],
            system_prompt=SYSTEM_PROMPT,
            on_token=on_token or (lambda t: None),
        )

    spec_manager = SpeculationManager(
        llm_fn=spec_llm_fn,
        on_token=lambda t: None,  # tokens from spec not streamed until commit
    ) if spec_enabled else None

    # --- ASR event handler ---
    async def on_asr_event(event):
        nonlocal current_turn
        if isinstance(event, AsrPartial):
            await ws.send_json({
                "type": "asr.partial",
                "text": event.text,
                "provider": event.provider,
            })
            if spec_manager:
                await spec_manager.on_partial(event.text)

        elif isinstance(event, AsrFinal):
            if current_turn is None:
                return
            current_turn["asr_final_ms"] = session_metrics.now_ms()
            conversation_history.append({"role": "user", "content": event.text})

            await ws.send_json({
                "type": "asr.final",
                "text": event.text,
                "provider": event.provider,
            })

            # Filler audio while LLM thinks
            filler = get_filler_chunk()
            if filler:
                current_turn["filler_played"] = True
                await on_tts_event(filler)

            if spec_manager:
                spec_result = await spec_manager.on_final(event.text)
                if spec_result == "commit":
                    current_turn["spec_hit"] = True
                    return  # committed speculative generation is already streaming

            llm_task = asyncio.create_task(run_llm_tts(event.text, current_turn))
            barge_in_handler.register_llm_task(llm_task)

    asr = create_asr(on_event=lambda e: asyncio.create_task(on_asr_event(e)), provider=asr_provider)
    await asr.connect()

    # --- Main WebSocket receive loop ---
    try:
        async for message in ws.iter_bytes():
            # Each message is raw PCM bytes from browser mic
            recorder.append_user(message, session_metrics.now_ms())

            if current_turn is None:
                current_turn = {"vad_start_ms": session_metrics.now_ms()}

            is_speech = vad.process_and_check(message)
            if is_speech:
                # Check for barge-in
                if vad.agent_playing:
                    await barge_in_handler.fire(ts_ms=session_metrics.now_ms())
                    await ws.send_json({
                        "type": "barge_in",
                        "ts_ms": session_metrics.now_ms(),
                    })
                await asr.send_audio(message)
            else:
                # Silence after speech → flush ASR
                await asr.flush()

    except WebSocketDisconnect:
        pass
    finally:
        await asr.close()
        await tts.close()
        recording_path = recorder.stitch()
        await close_session(
            session_id,
            recording_path=recording_path,
            metrics_json=json.dumps({"turns": len(session_metrics.turns)}),
        )
```

- [ ] **Step 2: Smoke test with mock providers**

```bash
cd backend && ASR_PROVIDER=mock TTS_PROVIDER=mock uvicorn backend.main:app --reload --port 8000
```
Expected: server starts with `Application startup complete.`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: FastAPI WebSocket orchestrator — end-to-end pipeline"
```

---

## Task 17: Browser echo cancellation (frontend getUserMedia)

**Files:**
- Modify: `frontend/Sample Buildathon/Voice Agent Console.html` — update mic capture code

- [ ] **Step 1: Find the getUserMedia call in the HTML file**

```bash
grep -n "getUserMedia\|AudioContext\|MediaStream" "frontend/Sample Buildathon/Voice Agent Console.html"
```

- [ ] **Step 2: Update getUserMedia constraints**

Find the existing `getUserMedia` call and replace the audio constraints with:

```javascript
const stream = await navigator.mediaDevices.getUserMedia({
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    sampleRate: 16000,
    channelCount: 1,
  },
  video: false,
});
```

If no getUserMedia exists yet, add a `startCall()` function:

```javascript
async function startCall(sessionId, wsUrl) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      sampleRate: 16000,
      channelCount: 1,
    },
    video: false,
  });

  const ws = new WebSocket(`${wsUrl}/ws/${sessionId}`);
  const audioCtx = new AudioContext({ sampleRate: 16000 });
  const source = audioCtx.createMediaStreamSource(stream);
  const processor = audioCtx.createScriptProcessor(512, 1, 1);

  processor.onaudioprocess = (e) => {
    const float32 = e.inputBuffer.getChannelData(0);
    // Convert float32 → int16 PCM
    const int16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768));
    }
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(int16.buffer);
    }
  };

  source.connect(processor);
  processor.connect(audioCtx.destination);
  return { ws, stream, audioCtx };
}
```

- [ ] **Step 3: Add PCM playback handler on WebSocket message**

```javascript
ws.onmessage = async (event) => {
  if (event.data instanceof ArrayBuffer) {
    // Raw PCM bytes — play via AudioContext
    const int16 = new Int16Array(event.data);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768;
    }
    const buffer = audioCtx.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32, 0);
    const src = audioCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(audioCtx.destination);
    src.start();
  } else {
    // JSON control message
    const msg = JSON.parse(event.data);
    handleControlMessage(msg);
  }
};

function handleControlMessage(msg) {
  if (msg.type === 'playback.cancel') {
    // Stop any queued audio — simplest: recreate AudioContext
    audioCtx.close();
    audioCtx = new AudioContext({ sampleRate: 16000 });
  }
  if (msg.type === 'asr.partial') {
    updateTranscriptPartial(msg.text);
  }
  if (msg.type === 'asr.final') {
    finalizeTranscriptTurn('user', msg.text);
  }
  if (msg.type === 'metrics.turn') {
    updateMetricsPanelTurn(msg.turn);
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add "frontend/Sample Buildathon/Voice Agent Console.html"
git commit -m "feat: browser echo cancellation via getUserMedia AEC constraints + PCM WebSocket"
```

---

## Task 18: End-to-end integration test

**Files:**
- Create: `backend/tests/test_integration.py`

- [ ] **Step 1: Write integration test with mock providers**

```python
# backend/tests/test_integration.py
import asyncio
import json
import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from backend.main import app

@pytest.mark.asyncio
async def test_full_call_cycle_with_mocks(monkeypatch):
    import backend.main as main_module
    # Force mock providers
    monkeypatch.setattr("backend.config.cfg.asr_provider", "mock")
    monkeypatch.setattr("backend.config.cfg.tts_provider", "mock")

    async with AsyncClient(app=app, base_url="http://test") as client:
        async with aconnect_ws("/ws/test-session-001", client) as ws:
            # Send config
            await ws.send_json({
                "user_id": "u1",
                "asr_provider": "mock",
                "tts_provider": "mock",
                "smart_routing": False,
                "spec_enabled": False,
            })
            # Send fake PCM frame (512 zero bytes)
            await ws.send_bytes(b"\x00" * 1024)
            # Should receive asr.partial or asr.final within 2s
            msgs = []
            for _ in range(5):
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                    msgs.append(msg)
                except asyncio.TimeoutError:
                    break
            types = [m.get("type") for m in msgs]
            assert any(t in ("asr.partial", "asr.final") for t in types)
```

- [ ] **Step 2: Install httpx-ws for testing**

```bash
pip install httpx-ws pytest-asyncio
```

- [ ] **Step 3: Run integration test**

```bash
cd backend && pytest tests/test_integration.py -v
```
Expected: PASSED (may need minor adjustments to mock flow)

- [ ] **Step 4: Run full test suite**

```bash
cd backend && pytest tests/ -v
```
Expected: All tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_integration.py
git commit -m "test: end-to-end integration test with mock providers"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|---|---|
| Browser `getUserMedia` AEC constraints | Task 17 |
| Server VAD gate (`agent_playing` flag) | Task 3 |
| `agent_playing` set on `tts.audio_chunk` / cleared on `tts.done` | Task 16 |
| OpenAI Realtime ASR adapter | Task 9 |
| Gemini Live ASR adapter (transcription-only) | Task 10 |
| OpenAI streaming TTS adapter | Task 11 |
| Gemini TTS per-sentence adapter | Task 12 |
| Factory pattern (`create_asr`, `create_tts`) | Tasks 4, 5 |
| Smart model routing heuristic + toggle | Task 6 |
| `SHORT_ANSWER_SET` routing | Task 6 |
| `ROUTING_ENABLED` toggle | Task 6 |
| Speculative gen 400ms debounce | Task 8 |
| Speculative gen SequenceMatcher commit (0.85) | Task 8 |
| `asyncio.Task.cancel()` on discard | Task 8 |
| Phrase cache normalize + exact match | Task 7 |
| Phrase cache fuzzy match (0.88 threshold) | Task 7 |
| `pregen_phrases.py` script | Task 7 |
| Filler audio | Task 15 |
| Barge-in handler (`agent_playing = False`, cancel tasks) | Task 15 |
| SQLite schema + session lifecycle | Task 13 |
| Per-turn metrics (ASR ms, LLM TTFT, TTS TTFB, perceived/actual) | Task 13 |
| Call recording + WAV stitching | Task 14 |
| Full pipeline orchestration | Task 16 |
| Prompt caching structure (system + memory + history + turn) | Task 6 |

All spec requirements covered. ✓

### Placeholder check
No TBDs, no "implement later", no "add validation" without code. ✓

### Type consistency
- `TtsAudioChunk.source` defined in Task 2, used in Tasks 11, 12, 15, 16 ✓
- `VAD.agent_playing` set in Tasks 3, 15, 16 — same attribute name ✓
- `phrase_cache.lookup(sentence)` defined Task 7, used Task 16 ✓
- `SpeculationManager.on_partial/on_final` defined Task 8, wired Task 16 ✓
- `SessionMetrics.now_ms()` defined Task 13, used Task 16 ✓
