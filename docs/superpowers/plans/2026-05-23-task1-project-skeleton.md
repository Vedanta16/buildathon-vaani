# Task 1: Project Skeleton + Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the backend Python project skeleton with config, package structure, and all dependencies installed, verified by a passing test.

**Architecture:** A FastAPI-based voice AI backend. Config is a single dataclass in `config.py` at the backend root. Packages are laid out as `backend/`, `backend/providers/asr/`, `backend/providers/tts/`, `backend/tests/`, and `backend/scripts/` — each with an `__init__.py`. Tests run via pytest from the project root using `python -m pytest backend/tests/`.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, websockets, torch, torchaudio, silero-vad, openai SDK, google-generativeai, aiosqlite, numpy, scipy, pytest, pytest-asyncio, httpx, httpx-ws

---

### Task 1: Package structure and __init__.py files

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/providers/__init__.py`
- Create: `backend/providers/asr/__init__.py`
- Create: `backend/providers/tts/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/scripts/__init__.py`

- [ ] **Step 1: Create all __init__.py files**

Run from project root:
```bash
mkdir -p "backend/providers/asr" "backend/providers/tts" "backend/tests" "backend/scripts"
touch backend/__init__.py backend/providers/__init__.py backend/providers/asr/__init__.py backend/providers/tts/__init__.py backend/tests/__init__.py backend/scripts/__init__.py
```

Expected: No output, all directories and files created.

- [ ] **Step 2: Verify structure**

Run:
```bash
find backend -name "__init__.py"
```
Expected output (order may vary):
```
backend/__init__.py
backend/providers/__init__.py
backend/providers/asr/__init__.py
backend/providers/tts/__init__.py
backend/tests/__init__.py
backend/scripts/__init__.py
```

---

### Task 2: Create requirements.txt

**Files:**
- Create: `backend/requirements.txt`

- [ ] **Step 1: Write requirements.txt**

Create `backend/requirements.txt` with contents:
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
websockets>=12.0
torch>=2.0.0
torchaudio>=2.0.0
silero-vad>=4.0.0
openai>=1.40.0
google-generativeai>=0.7.0
aiosqlite>=0.20.0
numpy>=1.24.0
scipy>=1.11.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
httpx-ws>=0.6.0
```

Note: Using relaxed version pins (`>=`) to avoid pip resolution conflicts with torch/silero-vad.

---

### Task 3: Create config.py

**Files:**
- Create: `backend/config.py`

- [ ] **Step 1: Write config.py**

Create `backend/config.py`:
```python
import os
from dataclasses import dataclass, field

@dataclass
class Config:
    asr_provider: str = os.getenv("ASR_PROVIDER", "openai_realtime")
    tts_provider: str = os.getenv("TTS_PROVIDER", "openai")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "AIzaSyCJ0bUaqkF6pGJOpEyXqgY8MHgLLDQ_ndA")

    large_model: str = "gpt-4o"
    small_model: str = "gpt-4o-mini"
    routing_word_threshold: int = 8

    short_answer_set: frozenset = field(default_factory=lambda: frozenset({
        "yes", "no", "ok", "okay", "sure", "thanks", "thank you",
        "got it", "sounds good", "perfect", "great", "fine",
        "yep", "nope", "correct", "exactly", "absolutely",
        "go ahead", "send it", "do it", "keep it",
    }))

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

---

### Task 4: Create pytest.ini

**Files:**
- Create: `backend/pytest.ini`

- [ ] **Step 1: Write pytest.ini**

Create `backend/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

---

### Task 5: Install dependencies

- [ ] **Step 1: Install from requirements.txt**

Run:
```bash
cd "/Users/vedanta/Self/Personal Projects/Buildathon/backend" && pip install -r requirements.txt
```
Expected: All packages install successfully. May take several minutes due to torch download size.

If silero-vad has conflicts, run:
```bash
pip install silero-vad
```
without a version pin.

---

### Task 6: Write and run config test

**Files:**
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Before config.py exists (or before packages are installed), this would fail. Create `backend/tests/test_config.py`:
```python
from backend.config import cfg

def test_config_loads():
    assert cfg.large_model == "gpt-4o"
    assert cfg.small_model == "gpt-4o-mini"
    assert "yes" in cfg.short_answer_set
    assert cfg.gemini_api_key != ""
    assert cfg.spec_commit_ratio == 0.85
```

- [ ] **Step 2: Run test**

Run from project root:
```bash
cd "/Users/vedanta/Self/Personal Projects/Buildathon" && python -m pytest backend/tests/test_config.py -v
```
Expected:
```
PASSED backend/tests/test_config.py::test_config_loads
1 passed in ...
```

---

### Task 7: Commit

- [ ] **Step 1: Stage and commit all backend files**

Run from project root:
```bash
cd "/Users/vedanta/Self/Personal Projects/Buildathon" && git add backend/ && git commit -m "feat: project skeleton, config, and dependencies"
```
Expected: Commit created successfully.
