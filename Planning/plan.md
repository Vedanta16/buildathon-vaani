# Voice AI Agent — Hackathon Build (Memory + Cost/Latency Demo)

## Context
One-day hackathon. We are a voice-AI startup. Goal: a working WebSocket-based voice agent that demonstrates **measurable, visible wins** in cost (prompt caching, smart routing), latency (filler audio, phrase cache, speculative generation), and product polish (barge-in, post-call eval). Cross-session memory is secondary. If a win isn't quantified on the UI, it doesn't count. **Stop building when time runs out** — follow the build priority table.

## Stack
- **Backend:** Python (FastAPI + `websockets`) — single process is fine
- **ASR / TTS:** Provider-agnostic interfaces; **OpenAI Realtime** is the default for the hackathon demo. **Google Gemini Live** is the first-class alternate. Pick via env vars — no pipeline rewrite when swapping.
- **LLM:** OpenAI GPT (`gpt-4o` / `gpt-4o-mini`) — must use **prompt caching**. Always the text Chat Completions API, never bundled inside a speech-to-speech model. Smart routing selects model per turn.
- **VAD:** Silero VAD, server-side (provider-agnostic; stays in our process regardless of ASR vendor)
- **Frontend:** Next.js + React + Tailwind, plain browser WebSocket client + Web Audio API for mic capture and playback
- **Storage:** SQLite for users / sessions / turns / memory blobs. No vector DB. No agent framework.

## Speech Providers (OpenAI · Gemini)

Sarvam removed. Two providers only. The pipeline must not care which vendor produced a transcript or audio chunk. All provider-specific code lives behind thin adapters; the orchestrator (`main.py`) only speaks an internal event contract.

### Config (env-driven, no code changes to swap)
```
ASR_PROVIDER=openai_realtime | gemini_live    # default: openai_realtime
TTS_PROVIDER=openai | gemini                  # default: openai
OPENAI_API_KEY=...
GEMINI_API_KEY=...                            # Google AI Studio
```
Top-bar dropdowns mirror these for live A/B during the demo (persist choice per session).

### Internal event contract (all ASR/TTS adapters must emit/consume this)
Adapters translate vendor wire formats → these events. Everything upstream (speculative gen, metrics, UI) consumes only these:

| Event | Payload | Used by |
|-------|---------|---------|
| `asr.partial` | `{text, stable_ms, provider}` | Transcript UI, speculative LLM |
| `asr.final` | `{text, provider}` | LLM commit, turn finalization |
| `tts.audio_chunk` | `{pcm_bytes, sample_rate, provider, source}` | WebSocket → browser playback |
| `tts.done` | `{provider}` | Pipeline trace, `agent_playing` flag |
| `playback.cancel` | `{}` | Client stops TTS (barge-in), `agent_playing` flag |
| `barge_in` | `{ts_ms}` | Pipeline trace, robustness metric |
| `recording.segment` | `{speaker, pcm_bytes, ts_ms}` | Audio stitcher |

`tts.audio_chunk` carries `source: "tts" | "phrase_cache" | "filler"` so the latency pane can tag cache hits.

### ASR options

| Provider key | Vendor API | Streaming partials | Notes |
|--------------|------------|--------------------|-------|
| `openai_realtime` *(default)* | OpenAI Realtime API transcription session | Yes — `transcript.text.delta` events | Primary; reliable partials; English-first |
| `gemini_live` | Google Gemini Live API (WebSocket) | Yes — `inputTranscription.text` on server messages | Adapter discards model audio turns — ASR only. Enable `input_audio_transcription: {}`. Use our Silero VAD for end-of-utterance. Input PCM: 16-bit LE, 16 kHz |

Both emit `asr.partial` / `asr.final`. Silero VAD remains server-side for both — consistent barge-in behavior regardless of ASR provider.

**Adapter files:** `providers/asr/base.py` (protocol), `asr_openai_realtime.py`, `asr_gemini_live.py`, `factory.py`

### TTS options

| Provider key | Vendor API | Streaming | TTFB | Demo use |
|--------------|------------|-----------|------|----------|
| `openai` *(default)* | `/v1/audio/speech` | Yes — chunked PCM | Low | Primary low-latency path. `response_format: "pcm"` (24 kHz) |
| `gemini` | `generateContent` + `response_modalities: ["audio"]` | No — full audio per sentence request | Higher | Contrast demo: swap provider → TTFB visibly increases in latency pane |

Gemini TTS: adapter requests per sentence chunk, emits `tts.audio_chunk` events. Voice via `SpeechConfig` / `prebuilt_voice_config`.

**Phrase cache / filler pregen:** `scripts/pregen_phrases.py --tts-provider openai|gemini` generates cached clips once.

**Adapter files:** `providers/tts/base.py` (protocol), `tts_openai.py`, `tts_gemini.py`, `factory.py`

### LLM (fixed for demo — separate from speech providers)
OpenAI text API only, structured for prompt caching. Smart routing selects `gpt-4o` or `gpt-4o-mini` per turn. Do **not** route conversation through OpenAI Realtime speech-to-speech or Gemini Live speech-to-speech.

### Explicitly out of scope
- **`gpt-realtime-2` / OpenAI Realtime speech-to-speech** — collapses ASR + LLM + TTS into one black box
- **Gemini Live speech-to-speech** — same problem; use `gemini_live` for input transcription only
- **Batch ASR for the live call loop** — no timely partials, speculation hit rate = 0%
- **Sarvam** — removed entirely

### Provider selection guidance
| Goal | ASR | TTS | LLM |
|------|-----|-----|-----|
| Hackathon demo (default) | `openai_realtime` | `openai` | gpt-4o / gpt-4o-mini + caching |
| All-Google speech | `gemini_live` | `gemini` | gpt-4o / gpt-4o-mini + caching |
| Provider contrast demo | `openai_realtime` | `gemini` | gpt-4o / gpt-4o-mini + caching |

---

## Architecture

### 1. Audio pipeline + Echo Cancellation
Browser captures mic with **`{echoCancellation: true, noiseSuppression: true, autoGainControl: true}`** passed to `getUserMedia` — activates hardware-level AEC before any audio hits the server.

PCM frames stream over client WebSocket → server runs Silero VAD → **VAD is gated: skip frame analysis while `agent_playing` is `True`** (second echo cancellation layer). Speech frames forwarded to active ASR adapter → adapter emits `asr.partial` / `asr.final` → orchestrator forwards transcript events to client.

`agent_playing` flag lifecycle:
- Set `True`: first `tts.audio_chunk` sent to client
- Set `False`: `tts.done` received or `playback.cancel` fired

### 2. LLM + TTS in the loop
After ASR final (or stable partial with speculation), stream LLM tokens into the active TTS adapter (chunk on sentence or punctuation boundaries). Adapter emits `tts.audio_chunk` events; orchestrator streams PCM back to client. Do **not** wait for full LLM completion before starting TTS. Filler audio (§4) and phrase cache (§5) slot in ahead of live TTS when applicable.

### 3. Prompt caching
Structure every LLM request as: `[STATIC SYSTEM PROMPT][USER MEMORY BLOCK][CONVERSATION HISTORY][CURRENT TURN]`. The first two segments are stable and should hit the cache. Track and display cached vs uncached prompt token counts per call.

### 4. Filler audio (acknowledgment tokens)
While the LLM is generating (after user stops speaking, before first TTS byte), play a short pre-cached clip: "mm-hm", "let me think", "one moment". Served from disk — zero API cost, ~30 min to build.

Track **perceived latency vs actual latency** per turn:
- *Actual:* VAD end → first agent TTS byte (excluding filler)
- *Perceived:* VAD end → first audio the user hears (filler or real TTS, whichever comes first)

Show both on the latency pane.

### 5. TTS phrase cache with fuzzy matching
Pre-generate ~50 stock phrases via `scripts/pregen_phrases.py`. Stored as `{normalized_text: pcm_bytes}` dict loaded at startup. Normalization: lowercase, strip punctuation, collapse whitespace.

**Runtime matching per sentence chunk:**
1. Normalize incoming sentence
2. Exact dict lookup — O(1)
3. If miss: `difflib.SequenceMatcher` ratio against all keys — threshold **0.88**
4. Cache hit → emit `tts.audio_chunk` with `source: "phrase_cache"`, TTFB = 0ms, no TTS API call
5. Cache miss → call TTS adapter normally

**What caches:** ack phrases ("Got it, one moment"), transitions ("Let me pull that up"), sign-offs ("Is there anything else?"). Dynamic content (names, numbers, dates) intentionally falls through to TTS.

Filler audio clips are a subset of the phrase cache.

### 6. Barge-in / interruption handling
If VAD detects user speech while agent TTS is playing or LLM is in-flight:
1. Set `agent_playing = False`
2. Send `playback.cancel` to client → stop TTS immediately
3. Abort in-flight LLM stream (`asyncio.Task.cancel()`)
4. Clear TTS buffer; resume listening on user audio

VAD gate (`agent_playing` flag from §1) prevents false barge-ins from agent audio. Log **`barge_in` events** in pipeline trace.

### 7. Smart model routing
Route every LLM call through a heuristic before firing the API request. Zero added latency — pure Python logic.

**Heuristic (in order):**
1. Turn text in `SHORT_ANSWER_SET` (`{"yes", "no", "ok", "sure", "thanks", "got it", "sounds good", ...}`) → `gpt-4o-mini`
2. Word count ≤ 8 → `gpt-4o-mini`
3. Otherwise → `gpt-4o`

**Toggle:** `Smart Routing: ON/OFF`. When OFF, always use `gpt-4o`.

**Metrics per turn:** `model_used`, `routed_small: bool`, `estimated_cost_usd` → feeds cost pane row "Turns routed to small model: X / est. $Y saved."

### 8. Speculative LLM generation
*(Build after priorities 1–6 are solid.)*

**Trigger:** ASR partial unchanged for **400ms** AND ≥ 5 words → fire speculative LLM call through smart routing heuristic. Short partials go to `gpt-4o-mini` and often complete before the user finishes speaking.

**On final ASR arrival:**
```python
ratio = SequenceMatcher(None, speculative_input, final_text).ratio()
if ratio >= 0.85:
    commit_speculative_task()       # let in-flight gen continue
else:
    speculative_task.cancel()       # asyncio.Task.cancel()
    fire_llm_call(final_text)       # fresh call with final text
```

**Pipeline trace tag:** committed spec turns show `spec·mini` or `spec·4o`.

**Toggle:** `Speculative Gen: ON/OFF`. Metric: spec hit rate % per session.

### 9. Cross-session memory
*(Only if build priorities 1–7 are solid by ~4 pm.)*
- After each call ends: summarize into structured JSON — `{facts: [], preferences: [], open_items: [], last_call_summary: "..."}` — via a cheap `gpt-4o-mini` call. Persist under the user.
- On next call start: load memory → format into fixed block → inject into **cacheable** prefix of system prompt.
- Fixed key order so cached prefix stays byte-identical across calls.

**Memory confidence gating:** score relevance to current turn (keyword overlap or cheap LLM yes/no). Only inject items above threshold.

### 10. Call recording & audio stitching
During call: append timestamped PCM segments:
- `recordings/{session_id}/user.pcm`
- `recordings/{session_id}/agent.pcm`
- `recordings/{session_id}/timeline.jsonl` — `{ts_ms, speaker, event, text?}`

On End Call: stitch into `recordings/{session_id}/call.wav` (mono mix with gap padding from timestamps). Use stdlib `wave` — no ffmpeg. Store `recording_path` on the `sessions` row.

Build recording plumbing alongside metrics (priority 2).

### 11. Post-call evaluation (async, after End Call)
Single `gpt-4o-mini` call producing:
```json
{
  "summary": "...",
  "sentiment_arc": ["neutral", "curious", "satisfied"],
  "talk_ratio": {"user_pct": 38, "agent_pct": 62},
  "issues": ["..."],
  "wins": ["..."],
  "suggested_memory_updates": [...]
}
```
Does not block the demo loop. Populates Post-Call Report tab async.

---

## UI Layout

### Top bar
- User selector dropdown
- Session list for active user (past calls, clickable)
- **Start Call / End Call** button
- Collapsible system prompt editor
- Toggle switches:
  - `ASR Provider: OpenAI Realtime | Gemini Live`
  - `TTS Provider: OpenAI | Gemini`
  - `Filler Audio: ON / OFF`
  - `Phrase Cache: ON / OFF`
  - `Prompt Caching: ON / OFF`
  - `Smart Routing: ON / OFF`
  - `Speculative Gen: ON / OFF`
  - `Memory: ON / OFF`

### Left pane (~55% width) — Live Transcript
- User and agent turns, color-coded
- ASR partials shown in light gray, finalize on `asr.final`
- Timestamp on each finalized turn; memory-referenced phrases underlined
- Auto-scroll to latest

### Right pane (~45% width) — Observability, tabbed

**Tab 1 — Live Metrics:**

*Pipeline Trace (last turn):* Horizontal swimlane — `VAD → ASR partial(s) [provider] → ASR final → [filler?] → LLM start (spec·mini / spec·4o / fresh) → LLM first token → TTS start [provider | cache] → first audio → playback | [barge_in]`

*Latency (live session):* Per-turn stacked bars (ASR ms | LLM TTFT ms | TTS TTFB ms). Perceived vs actual latency side-by-side. ⚡ on phrase cache hit turns (TTFB = 0ms). Running median, spec hit rate %, barge-in count. Comparison vs previous session.

*Cost & Tokens (A/B):* Live stacked area chart — per-turn columns growing rightward, segments: LLM prompt cached / uncached / completion / TTS / ASR. This session vs previous session overlay. "Saved by caching: X tokens / $Y". "Turns routed to small model: X / est. $Z saved."

**Tab 2 — Post-Call Report** *(populates async after End Call):*
Summary, sentiment arc, talk ratio, wins, issues, `call.wav` player, memory suggestions.

---

## Session / Memory Spec
- Schema: `users → sessions → turns`; `users → memory_blob` (latest); `sessions.recording_path`, `sessions.post_call_eval_json`
- During call: append PCM + `timeline.jsonl` under `recordings/{session_id}/`
- After session end: stitch audio → trigger memory summarization + post-call eval (async, non-blocking)
- Memory blob fixed key order — cached prefix stays byte-identical across calls

---

## Build Priority — stop when time runs out

| # | Item | Gate | Est. effort |
|---|------|------|-------------|
| **1** | End-to-end audio loop (mic → ASR → LLM → TTS) with echo cancellation | **Non-negotiable** | ~½ day |
| **2** | Metrics pane with **real telemetry** (pipeline trace, cost, latency) + call recording plumbing | **Non-negotiable — this is the demo** | ~2–3 h |
| **3** | Prompt caching + toggle | Cheap, big visible win | ~1 h |
| **4** | Filler audio + TTS phrase cache with fuzzy matching | Cheap, makes everything feel better | ~1 h |
| **5** | Barge-in handling | Small effort, huge demo robustness | ~1 h |
| **6** | Smart model routing + toggle | Cheap once LLM plumbing exists; cost story | ~1 h |
| **7** | Speculative generation + toggle | Headline latency feature; composes with routing | ~2–3 h |
| **8** | Cross-session memory + toggle | **Only if 1–7 solid by ~4 pm** | ~2 h |
| **9** | Post-call eval + stitched recording playback in UI | Async; high story value, zero live risk | ~1–2 h |
| — | Prosody-flavored memory | Stretch — derive from post-call eval JSON | ~1 h |

**Rule:** never start a lower-priority item if the one above it isn't demo-ready. Smart routing (#6) ships before speculative gen (#7) — routing is what makes speculation composable and demo-safe.

---

## Measurement / Demo Requirements (most important)
Every win must be visible as a number on the right pane:
- **Cost win:** flip Prompt Caching OFF, run scripted exchange, end call. Flip ON, re-run. Show tokens and $ saved.
- **Smart routing win:** "Turns routed to small model: X / est. $Y saved" in cost pane. Toggle OFF → all turns go to gpt-4o, cost rises.
- **Perceived latency win:** perceived vs actual latency bars — filler makes agent feel faster.
- **Phrase cache win:** greeting turn shows TTS TTFB = 0 ms.
- **Speculative + routing win:** short turn ("yeah keep it") → spec·mini committed → sub-300ms perceived latency.
- **Robustness win:** interrupt agent mid-sentence → barge-in fires cleanly (no false trigger from agent audio), pipeline trace shows cancel.
- **Provider contrast:** swap TTS to Gemini → TTFB visibly increases in latency chart → swap back.
- **Memory win** *(if built):* call 1 mention something. Call 2 — agent references it. Memory tab highlights entry.

---

## Deliverables

1. **Build plan** — follow Build Priority table above. Cut ruthlessly.

2. **Repo skeleton:**
   - `backend/`:
     - `main.py` — FastAPI + client WebSocket orchestrator; `agent_playing` flag; VAD gate
     - `config.py` — `ASR_PROVIDER`, `TTS_PROVIDER`, `OPENAI_API_KEY`, `GEMINI_API_KEY`
     - `providers/asr/` — `base.py`, `asr_openai_realtime.py`, `asr_gemini_live.py`, `factory.py`
     - `providers/tts/` — `base.py`, `tts_openai.py`, `tts_gemini.py`, `factory.py`
     - `vad.py` — Silero VAD; accepts `enabled: bool` gate param
     - `llm_openai.py` — text LLM with prompt caching + smart model routing
     - `filler.py` — play acknowledgment clip while LLM pending
     - `phrase_cache.py` — `lookup_phrase()`, `normalize()`, startup loader
     - `barge_in.py` — cancel LLM + TTS; set `agent_playing = False`
     - `speculation.py` — 400ms debounce, speculative task, similarity commit/discard
     - `recording.py` — PCM append + stitch to WAV on End Call
     - `post_call_eval.py` — async job: transcript + metrics → eval JSON
     - `memory.py`, `metrics.py`, `db.py`
     - `scripts/pregen_phrases.py` — one-time TTS pre-generation for phrase cache
   - `recordings/` — gitignored session audio
   - `frontend/`: Next.js app with `Transcript`, `PipelineTrace`, `CostPanel`, `LatencyPanel`, `PostCallPanel`, `Toggles`, `SystemPromptEditor`, `RecordingPlayer`
   - `README.md` — run instructions, env vars, provider swap guide

3. **Frontend layout** — split-pane with mock data first; lock layout before backend integration. *(Prototype already built — see `frontend/Sample Buildathon/`.)*

4. **Demo script** — 3-minute judge flow: scripted utterances, interrupt once (barge-in), toggle flips, which metric to point at after each beat.

---

## Constraints
- One day, two-to-three people building. Cut ruthlessly.
- No vector DBs. No LangChain / LlamaIndex / agent frameworks. Plain functions, plain SQL.
- The metrics pane is mandatory. Skip a feature before you skip its measurement.
- **Provider compatibility is non-negotiable:** orchestrator code must never import OpenAI or Google SDKs directly — only `factory.create_asr()` / `factory.create_tts()`. Adding a vendor = new adapter file + factory branch, zero pipeline changes.
- Do not use end-to-end speech-to-speech models (OpenAI `gpt-realtime-2`, Gemini Live with model audio driving replies) — incompatible with the modular demo architecture.
- Echo cancellation is non-negotiable: browser AEC + server VAD gate must both be active before any demo.
