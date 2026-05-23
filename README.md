# Voice Agent Console

A real-time voice-to-voice (V2V) agent built for the OpenAI × Gemini Buildathon. Talk to an AI agent through your browser mic — speech is transcribed, routed through an LLM, and spoken back with low latency.

## Architecture

```
Browser mic (16kHz PCM)
    │ WebSocket binary frames
    ▼
FastAPI /ws/{session_id}
    │
    ├─ Silero VAD ──► speech detection / barge-in
    │
    ├─ ASR provider ──► asr.partial / asr.final events
    │     ├─ gemini_live   (Gemini 2.5 Flash Native Audio)
    │     └─ openai_realtime (GPT-4o Realtime)
    │
    ├─ LLM (OpenAI) ──► streaming tokens
    │     ├─ Smart routing: short turns → gpt-4o-mini
    │     ├─ Speculative generation (400ms debounce)
    │     └─ Prompt caching (static system prompt prefix)
    │
    └─ TTS provider ──► PCM audio chunks
          ├─ gemini   (Gemini 2.5 Flash TTS — Aoede voice)
          └─ openai   (TTS-1 — streaming)

Browser playback (24kHz PCM, AudioContext queue)
```

**WebSocket protocol:**
- Upstream: raw 16kHz PCM Int16 binary frames
- Downstream text frames: JSON control messages (`asr.partial`, `asr.final`, `llm.response`, `metrics.turn`, `barge_in`, …)
- Downstream binary frames: `JSON_HEADER|PCM_BYTES` (pipe-delimited)

## Prerequisites

- Python 3.11+
- Node.js (optional — only used if you prefer `npx serve` over `python3 -m http.server`)
- A modern browser (Chrome recommended; must be served via HTTP, not `file://`)

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r backend/requirements.txt

# 3. Set API keys (required)
export GEMINI_API_KEY="your-gemini-key"
export OPENAI_API_KEY="your-openai-key"   # needed for LLM; TTS optional
```

Or create a `.env` file in the project root:
```
GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
```

## Running

```bash
# Start everything (backend on :8000, frontend on :8080)
./start.sh
```

Then open: **http://localhost:8080/Voice%20Agent%20Console.html**

To stop: `Ctrl+C` (kills both processes).

### Manual start (if preferred)

```bash
# Terminal 1 — backend
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000

# Terminal 2 — frontend
cd "frontend/Sample Buildathon"
python3 -m http.server 8080
```

## Provider Options

Selectable in the top bar during a call:

| Control | Options | Notes |
|---------|---------|-------|
| ASR | `gemini_live` (default), `openai_realtime` | Gemini key is baked in; OpenAI needs `OPENAI_API_KEY` |
| TTS | `gemini` (default), `openai` | Gemini: full audio per sentence; OpenAI: streaming |

> **Default is Gemini for both** since the key is pre-configured. Switch to OpenAI providers once you have a key.

## Feature Toggles

| Toggle | What it does |
|--------|-------------|
| Filler | Plays a short audio filler ("hmm", "let me check") while LLM thinks |
| PhraseCache | Fuzzy-match common agent phrases → skip TTS synthesis (instant playback) |
| PromptCache | OpenAI prompt prefix caching — reduces cost on repeated system prompts |
| SpecGen | Speculative generation: starts LLM on ASR partial, commits if ≥85% match |
| Memory | User memory recall (UI only — not yet connected to backend) |

## System Prompt

Click **System Prompt** in the top bar to edit the agent's personality. Changes take effect on the next call start. The prompt is sent to the backend with the WebSocket config message.

## What's Live vs Mock

| Panel | Status |
|-------|--------|
| Transcript (user + agent) | **Live** — real ASR + LLM responses |
| Pipeline trace | **Live** — real per-turn timestamps once a turn completes |
| Latency bars | **Live** — per-turn ASR / LLM TTFT / TTS TTFB |
| Token cost | **Live** — real OpenAI token counts; mock if no OpenAI key |
| Call timer | **Live** |
| Post-call report | **Mock** — no backend LLM summarization yet |
| Recording playback | **Mock** — backend records WAV but no HTTP serve endpoint yet |
| User / session history | **Mock** — no user management backend |

## Project Structure

```
Buildathon/
├── start.sh                        ← startup script (this file)
├── README.md
├── backend/
│   ├── main.py                     ← FastAPI WebSocket orchestrator
│   ├── config.py                   ← Config dataclass + env vars
│   ├── events.py                   ← Internal event types
│   ├── vad.py                      ← Silero VAD wrapper
│   ├── llm_openai.py               ← OpenAI streaming + model routing
│   ├── phrase_cache.py             ← Fuzzy TTS phrase cache
│   ├── filler.py                   ← Filler audio loader
│   ├── barge_in.py                 ← Barge-in handler (cancels LLM task)
│   ├── speculation.py              ← Speculative generation manager
│   ├── metrics.py                  ← Per-turn + session metrics
│   ├── recording.py                ← WAV recorder (user + agent channels)
│   ├── db.py                       ← SQLite session store (aiosqlite)
│   ├── requirements.txt
│   └── providers/
│       ├── asr/
│       │   ├── factory.py
│       │   ├── asr_gemini_live.py
│       │   └── asr_openai_realtime.py
│       └── tts/
│           ├── factory.py
│           ├── tts_gemini.py
│           └── tts_openai.py
└── frontend/
    └── Sample Buildathon/
        ├── Voice Agent Console.html  ← open this in Chrome (via HTTP server)
        ├── app.jsx                   ← main React app, WebSocket + audio
        ├── components.jsx            ← shared UI primitives
        ├── top-bar.jsx               ← provider toggles, call button, system prompt
        ├── transcript.jsx            ← live transcript panel
        ├── right-pane.jsx            ← live metrics + post-call report
        ├── data.js                   ← mock data (fallback when no live session)
        └── styles.css
```

## Pending / Known Gaps

- **Post-call report**: needs a `/report` endpoint that calls LLM on full transcript after session ends
- **Recording playback**: backend writes `recordings/{session_id}.wav` but no HTTP endpoint serves it
- **Filler audio**: `filler_audio/` directory is empty — drop `.wav` files there to enable
- **Phrase cache**: pre-populate with `python -m backend.scripts.pregen_phrases` (needs script update)
- **OpenAI LLM key**: LLM always uses OpenAI — set `OPENAI_API_KEY` or the agent won't respond
- **Memory toggle**: UI wired but backend memory recall not implemented
- **Speculative generation**: `spec_manager.on_final` result not currently emitted to frontend as a distinct event

## Environment Variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `GEMINI_API_KEY` | For Gemini ASR/TTS | baked in config.py | Replace with your own |
| `OPENAI_API_KEY` | For LLM (always) + OpenAI ASR/TTS | placeholder | **Must set for agent to respond** |
| `ASR_PROVIDER` | No | `gemini_live` | Override default provider |
| `TTS_PROVIDER` | No | `gemini` | Override default provider |
