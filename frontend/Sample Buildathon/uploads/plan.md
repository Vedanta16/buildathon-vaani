# Voice AI Agent — Hackathon Build (Memory + Cost/Latency Demo)

## Context
One-day hackathon. We are a voice-AI startup. Goal: a working WebSocket-based voice agent that demonstrates **measurable, visible wins** in cost (prompt caching, smart routing), latency (filler audio, phrase cache, speculative generation), and product polish (barge-in, post-call eval). Cross-session memory is secondary. If a win isn't quantified on the UI, it doesn't count. **Stop building when time runs out** — follow the build priority table.

## Stack
- **Backend:** Python (FastAPI + `websockets`) — single process is fine
- **ASR / TTS:** Provider-agnostic interfaces; **Sarvam streaming** is the default for the hackathon demo. **OpenAI** and **Google Gemini** are first-class alternates (see Speech Providers below). Pick via env vars — no pipeline rewrite when swapping.
- **LLM:** OpenAI GPT (we have $300 in credits) — must use **prompt caching**. Always the text Chat Completions / Responses API, never bundled inside a speech-to-speech model.
- **VAD:** Silero VAD, server-side (provider-agnostic; stays in our process regardless of ASR vendor)
- **Frontend:** Next.js + React + Tailwind, plain browser WebSocket client + Web Audio API for mic capture and playback
- **Storage:** SQLite for users / sessions / turns / memory blobs. No vector DB. No agent framework.

## Speech Providers (multi-vendor: Sarvam · OpenAI · Gemini)

The pipeline must not care which vendor produced a transcript or audio chunk. All provider-specific code lives behind thin adapters; the orchestrator (`main.py`) only speaks an internal event contract.

### Config (env-driven, no code changes to swap)
```
ASR_PROVIDER=sarvam | openai_realtime | openai_batch | gemini_live | gemini_batch   # default: sarvam
TTS_PROVIDER=sarvam | openai | gemini                                              # default: sarvam
OPENAI_API_KEY=...
SARVAM_API_KEY=...
GEMINI_API_KEY=...                    # Google AI Studio (generativelanguage.googleapis.com)
# Optional Vertex AI path (enterprise): GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
```
Optional top-bar dropdowns mirror these for live A/B during the demo (persist choice per session).

### Internal event contract (all ASR/TTS adapters must emit/consume this)
Adapters translate vendor wire formats → these events. Everything upstream (speculative gen, metrics, UI) consumes only these:

| Event | Payload | Used by |
|-------|---------|---------|
| `asr.partial` | `{text, stable_ms, provider}` | Transcript UI, speculative LLM |
| `asr.final` | `{text, provider}` | LLM commit, turn finalization |
| `tts.audio_chunk` | `{pcm_bytes, sample_rate, provider}` | WebSocket → browser playback |
| `tts.done` | `{provider}` | Pipeline trace |
| `playback.cancel` | `{}` | Client stops TTS (barge-in) |
| `barge_in` | `{ts_ms}` | Pipeline trace, robustness metric |
| `recording.segment` | `{speaker, pcm_bytes, ts_ms}` | Audio stitcher |

Provider name is included on every event so the observability pane can show which vendor handled each turn.

### ASR options

| Provider key | Vendor API | Models | Streaming partials? | Use when |
|--------------|------------|--------|---------------------|----------|
| `sarvam` *(default)* | Sarvam streaming ASR | Sarvam ASR models | Yes — native streaming over WebSocket/HTTP stream | Hackathon demo; Indian languages; matches our custom WS pipeline |
| `openai_realtime` | OpenAI **Realtime API** transcription session | `gpt-realtime-whisper` | Yes — `transcript.text.delta` events | Fallback if Sarvam flaky; English-first; uses OpenAI credits |
| `openai_batch` | OpenAI `/v1/audio/transcriptions` | `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` | Partial via `stream=true` on gpt-4o-* only; **no live mic partials** | Last-resort / offline replay only — **breaks speculative generation** because partials arrive too late |
| `gemini_live` | Google **Gemini Live API** (WebSocket) | `gemini-live-2.5-flash`, `gemini-live-2.5-flash-native-audio` | Yes — `inputTranscription.text` on server messages (enable `input_audio_transcription: {}`) | Live partials; multilingual; 16 kHz PCM in matches our pipeline. Adapter **discards model audio turns** — ASR only, LLM stays on OpenAI |
| `gemini_batch` | Gemini `generateContent` + audio file / inline PCM | `gemini-2.5-flash`, `gemini-3.5-flash`, etc. | No — full utterance in, transcript out | Post-call eval ground-truth, recording replay — **not for live loop** |

**Important:** OpenAI batch Whisper and Gemini batch `generateContent` are **not** drop-in replacements for Sarvam streaming ASR. For live partials, use `sarvam`, `openai_realtime`, or `gemini_live`.

**Gemini Live ASR adapter notes:** Live API is designed for speech-to-speech; our adapter uses it in **transcription-only mode**: send user PCM via `send_realtime_input`, map `server_content.input_transcription.text` → `asr.partial` / `asr.final`, ignore `model_turn` audio. Use our Silero VAD for end-of-utterance, not Gemini's built-in VAD (we need consistent barge-in behavior). Input PCM: 16-bit LE, 16 kHz.

**Adapter files:** `providers/asr/base.py` (protocol), `asr_sarvam.py`, `asr_openai_realtime.py`, `asr_openai_batch.py`, `asr_gemini_live.py`, `asr_gemini_batch.py` (stubs OK on day one).

### TTS options

| Provider key | Vendor API | Models | Streaming? | Output for our pipeline |
|--------------|------------|--------|--------------|-------------------------|
| `sarvam` *(default)* | Sarvam streaming TTS | Sarvam TTS models | Yes | PCM chunks → client |
| `openai` | OpenAI `/v1/audio/speech` | `tts-1` (low latency), `tts-1-hd`, `gpt-4o-mini-tts` | Yes — chunked transfer / `stream_format: "audio"` | Request `response_format: "pcm"` (24 kHz); resample to 16 kHz if needed for client |
| `gemini` | Gemini `generateContent` with `response_modalities: ["audio"]` | `gemini-2.5-flash-preview-tts`, `gemini-2.5-pro-preview-tts`, `gemini-3.1-flash-tts-preview` | **No native streaming** — full audio returned per request | Adapter requests per sentence chunk, receives PCM/wav inline, emits as one or more `tts.audio_chunk` events. Slightly higher TTFB vs Sarvam/OpenAI stream; fine for demo. Voice via `SpeechConfig` / `prebuilt_voice_config` (30 voices). Optional style prompts and audio tags on 3.1 Flash TTS |

TTS swap is straightforward: same input (text sentence chunks), same output (`tts.audio_chunk` events). OpenAI max 4096 chars/request; Gemini context limits per model docs — chunk on sentence boundaries (already planned).

**Phrase cache / filler pregen:** `scripts/pregen_phrases.py` accepts `--tts-provider` so cached clips can be generated from Sarvam, OpenAI, or Gemini once.

**Adapter files:** `providers/tts/base.py` (protocol), `tts_sarvam.py`, `tts_openai.py`, `tts_gemini.py`.

### LLM (fixed for demo — separate from speech providers)
OpenAI text API only, structured for prompt caching. Do **not** route conversation through OpenAI Realtime speech-to-speech or Gemini Live speech-to-speech. (Gemini text LLM could be added later as a separate `llm_gemini.py` adapter — out of scope for day one.)

### Explicitly out of scope (do not use as the main pipeline)
- **`gpt-realtime-2` / OpenAI Realtime speech-to-speech** — collapses ASR + LLM + TTS into one black box. Bypasses speculative generation, per-stage latency metrics, prompt caching toggles, and memory injection as separate measurable stages.
- **Gemini Live speech-to-speech** (`gemini-live-*` with model audio output driving the agent) — same problem. Use `gemini_live` adapter for **input transcription only**, or `gemini` TTS adapter for **output audio only**.
- **Batch ASR for the live call loop** (`openai_batch`, `gemini_batch`) — no timely partials → speculation hit rate stays 0%.

### Provider selection guidance
| Goal | ASR | TTS | LLM |
|------|-----|-----|-----|
| Hackathon demo (default) | `sarvam` | `sarvam` | OpenAI + caching |
| All-OpenAI, one API key | `openai_realtime` | `openai` | OpenAI + caching |
| All-Google speech, OpenAI brain | `gemini_live` | `gemini` | OpenAI + caching |
| Mixed (e.g. Sarvam ASR + Gemini TTS) | `sarvam` | `gemini` | OpenAI + caching |
| Degraded fallback (no partials) | `openai_batch` or `gemini_batch` | any | OpenAI + caching |
| Post-call ground-truth transcript | `gemini_batch` or `openai_batch` on stitched `call.wav` | — | OpenAI (eval) |

De-risk in first hour: confirm Sarvam streaming partial latency. If blocked, switch `ASR_PROVIDER=openai_realtime` or `gemini_live` without touching orchestrator code.

## Architecture

### 1. Audio pipeline
Browser captures mic (16 kHz PCM) → streams frames over client WebSocket → server runs Silero VAD → speech frames forwarded to **active ASR adapter** → adapter emits `asr.partial` / `asr.final` → orchestrator forwards transcript events to client. ASR adapter may hold its own outbound connection (e.g. Sarvam stream, OpenAI Realtime WS) — that detail is hidden behind the adapter interface.

### 2. LLM + TTS in the loop
After ASR final (or stable partial with speculation), stream LLM tokens into the **active TTS adapter** (chunk on sentence or punctuation boundaries). Adapter emits `tts.audio_chunk` events; orchestrator streams PCM back to client. Do **not** wait for full LLM completion before starting TTS. Filler audio (§4) and phrase cache (§5) slot in ahead of live TTS when applicable.

### 3. Prompt caching
Structure every LLM request as: `[STATIC SYSTEM PROMPT][USER MEMORY BLOCK][CONVERSATION HISTORY][CURRENT TURN]`. The first two segments are stable and should hit the cache. Track and display cached vs uncached prompt token counts per call.

### 4. Filler audio (acknowledgment tokens)
While the LLM is generating (after user stops speaking, before first TTS byte), play a short pre-cached clip: "mm-hm", "let me think", "one moment". Served from disk — zero API cost, ~30 min to build. Makes the agent feel ~300 ms faster than measured pipeline latency.

Track **perceived latency vs actual latency** per turn:
- *Actual:* VAD end → first agent TTS byte (excluding filler)
- *Perceived:* VAD end → first audio the user hears (filler or real TTS, whichever comes first)

Show both on the latency pane. This signals voice-product thinking, not just pipeline engineering.

### 5. TTS phrase cache
Pre-generate audio (once, at startup or via a `scripts/pregen_phrases.py` script) for 20–30 stock phrases: greetings, sign-offs, "got it", "one moment", "let me check", etc. Store as `{phrase_hash}.pcm` on disk. At runtime, if agent output matches a cached phrase (exact or prefix match), serve from disk instead of hitting TTS API.

Metrics: **cache hits** count, **TTS TTFB = 0 ms** on those turns — visible in latency pane as bars that finish before LLM completes.

Integrates with filler audio (filler clips are a subset of the phrase cache).

### 6. Barge-in / interruption handling
If VAD detects user speech while agent TTS is playing or LLM is in-flight:
1. Send `playback.cancel` to client → stop TTS immediately
2. Abort in-flight LLM stream (cancel token / task cancellation)
3. Clear TTS buffer; resume listening on user audio

Run VAD on incoming mic frames even during agent playback (echo cancellation not required for hackathon — judge uses headphones). Log **`barge_in` events** in pipeline trace. Judges will interrupt — demos that don't handle this feel broken.

### 7. Speculative LLM generation
*(Headline technical feature — build only after build priorities 1–5 are solid.)*
As soon as a *stable* ASR partial arrives (e.g. ~300 ms of speech with no change for one tick), kick off an LLM streaming call **while the user is still speaking**. On each subsequent stable partial:
- If new partial is a clean extension of the previous → let the in-flight gen continue
- If new partial materially diverges → abort and restart with the new text
- When VAD detects end-of-utterance and final ASR lands → if an in-flight gen matches, commit it; otherwise discard and fire a fresh call with the final text

Track **speculation hit rate**: % of turns where the speculative call was committed instead of discarded. Toggle: `Speculative Generation: ON / OFF`.

### 8. Cross-session memory
*(Only if build priorities 1–6 are solid by ~4 pm.)*
- After each call ends: summarize into structured JSON — `{facts: [], preferences: [], open_items: [], last_call_summary: "..."}` — via a cheap LLM call. Persist under the user.
- On next call start: load memory → format into fixed block → inject into **cacheable** prefix of system prompt.
- Fixed key order so cached prefix stays byte-identical across calls.

**Memory confidence gating** *(pair with memory if built):* before injecting or surfacing a memory item in speech, score relevance to current turn (cheap LLM yes/no or keyword overlap). Only use items above threshold. Avoids creepy-recall failures; gives a "responsible personalization" story for judges.

### 9. Call recording & audio stitching
Record the full call as a single playable file for replay, post-call analysis, and demo evidence.

**During call:** append timestamped PCM segments to session-scoped temp files:
- `recordings/{session_id}/user.pcm` — mic frames while VAD active
- `recordings/{session_id}/agent.pcm` — TTS output chunks (including filler / phrase-cache hits)
- `recordings/{session_id}/timeline.jsonl` — `{ts_ms, speaker, event, text?}` for alignment

**On End Call:** stitch into:
- `recordings/{session_id}/call.wav` — interleaved or sequential mono mix (user + agent with gap padding from timestamps)
- Optional: `call_dual.wav` (stereo: L=user, R=agent) for post-call prosody work

Use stdlib / `wave` + simple merge (no ffmpeg dependency required). Store `recording_path` on the `sessions` row in SQLite.

Build recording plumbing alongside metrics (priority 2) — you're already timestamping every event; writing PCM in parallel is cheap.

### 10. Post-call evaluation (async, after End Call)
Run **after** the live call ends — does not block the demo loop. Flag results in UI once ready (Tab 5 or banner on session list).

**Inputs:** full transcript (turns + timestamps), stitched `call.wav`, session metrics summary, toggles used.

**Single cheap LLM call** (or 2–3 focused prompts) producing structured JSON:
```json
{
  "summary": "...",
  "sentiment_arc": ["neutral", "frustrated", "resolved"],
  "talk_ratio": {"user_pct": 62, "agent_pct": 38},
  "issues": ["agent talked over user at 1:24", "memory item X was irrelevant"],
  "wins": ["speculative hit saved ~400ms on turn 3", "phrase cache hit on greeting"],
  "suggested_memory_updates": [...]
}
```

Optionally send audio to batch ASR (`openai_batch` or Sarvam) for a **ground-truth transcript compare** vs live ASR — surfaces word-error rate and missed barge-ins. Heavy; only if time permits.

**Why this is worth it:** live demo shows *how* the agent performs; post-call eval shows you think about *quality assurance* and continuous improvement — a story judges remember, zero risk to the live path. Pairs naturally with memory (eval can propose memory updates) and with prosody tagging (stretch).

**Prosody-flavored memory** *(stretch — only if everything else done by mid-afternoon):* tag memory entries with affect from post-call eval or live sentiment: `"user sounded frustrated when discussing pricing"`. Genuinely voice-native and novel; hard to demo convincingly in 3 minutes. Derive from post-call eval JSON rather than real-time inference to keep scope manageable.

### 11. Smart model routing *(stretch)*
Route short turns ("yes", "okay", "what time") to a cheap/fast model; substantive turns to the main model. Crude heuristic: input under 8 words → cheap model. Expect 40–60% of turns routed.

Cost pane row: **"Turns routed to cheap model: X"** + estimated $ saved. Skip unless speculative gen finishes ahead of schedule.

## UI Layout

### Top bar
- User selector dropdown (User 1, User 2, User 3 — seed a few)
- Session list for the active user (past calls, clickable to view)
- **Start Call** / **End Call** button
- Collapsible system prompt editor (single editable block, persisted)
- Toggle switches / selectors, each with a clear label:
  - `ASR Provider: Sarvam | OpenAI Realtime | OpenAI Batch | Gemini Live | Gemini Batch`
  - `TTS Provider: Sarvam | OpenAI | Gemini`
  - `Filler Audio: ON / OFF`
  - `Phrase Cache: ON / OFF`
  - `Prompt Caching: ON / OFF`
  - `Speculative Generation: ON / OFF`
  - `Memory Injection: ON / OFF`

### Left pane (~55% width) — Live Transcript
- User and agent turns, color-coded
- ASR partials shown in light gray, finalize to black/white as they stabilize
- Timestamp on each finalized turn
- Auto-scroll to latest

### Right pane (~45% width) — Observability, tabbed
**Tab 1 — Pipeline trace (live timeline):**
For each turn, show a vertical timeline of events with millisecond deltas:
`VAD start → ASR partial(s) [provider] → ASR final → [filler?] → LLM start (speculative? Y/N) → LLM first token → TTS start [provider|cache] → TTS first audio → playback start | [barge_in]`

**Tab 2 — Cost & tokens:**
Running counters for the active session:
- Prompt tokens — cached vs uncached, split out
- Completion tokens
- $ spent so far (live)
- Delta widget: "Saved by caching this session: X tokens / $Y" (computed as cached_tokens × full_price × (1 - cache_discount))
- Comparison row: same metrics from the *previous* session for the same user (so flipping the cache toggle and re-running shows the gap)
- **Turns routed to cheap model: X** *(stretch — smart routing)*

**Tab 3 — Latency:**
- Per-turn stacked bar: ASR ms | LLM TTFT ms | TTS TTFB ms | total ms
- **Perceived vs actual latency** side-by-side (filler audio win)
- Phrase cache hits (TTFB = 0 ms turns highlighted)
- Running median across all turns in the session
- Speculation hit rate % (when built)
- Barge-in count per session
- Comparison block: median latency this session vs previous session (for toggle A/B)

**Tab 4 — Memory state:**
- Current memory blob being injected (pretty-printed JSON)
- Log of memory updates: "After call 1 → added fact X", "After call 2 → updated preference Y"
- Highlight in transcript when the agent uses a memory item (e.g. underline + tooltip showing which memory entry it came from)
- Confidence gate rejections (if built): "skipped memory item X — low relevance"

**Tab 5 — Post-call report** *(populates async after End Call):*
- Session summary, sentiment arc, talk ratio
- Issues / wins flagged by post-call eval LLM
- Link to play stitched `call.wav` recording
- Optional: ASR ground-truth diff vs live transcript (via `gemini_batch` or `openai_batch` on stitched `call.wav`)

## Session / Memory Spec
- Schema: `users → sessions → turns`; `users → memory_blob` (latest); `sessions.recording_path`, `sessions.post_call_eval_json`
- During call: append PCM + `timeline.jsonl` under `recordings/{session_id}/` (see §9)
- After session end: stitch audio → trigger memory summarization + post-call eval job (async, non-blocking)
- Memory blob has a fixed key order so the cached prefix stays byte-identical across calls

## Build Priority — stop when time runs out

| # | Item | Gate | Est. effort |
|---|------|------|-------------|
| **1** | End-to-end audio loop (mic → ASR → LLM → TTS) | **Non-negotiable** | ~½ day |
| **2** | Metrics pane with **real telemetry** (pipeline trace, cost, latency) + call recording plumbing | **Non-negotiable — this is the demo** | ~2–3 h |
| **3** | Prompt caching + toggle | Cheap, big visible win | ~1 h |
| **4** | Filler audio + TTS phrase cache | Cheap, makes everything feel better | ~30 min – 1 h |
| **5** | Barge-in handling | Small effort, huge demo robustness | ~1 h |
| **6** | Speculative generation + toggle | Headline technical feature | ~2–3 h |
| **7** | Cross-session memory + toggle | **Only if 1–6 solid by ~4 pm** | ~2 h |
| **8** | Post-call eval + stitched recording playback in UI | After End Call; async; high story value, zero live risk | ~1–2 h |
| — | Smart model routing | Stretch — skip unless #6 done early | ~1 h |
| — | Prosody-flavored memory | Stretch — derive from post-call eval JSON | ~1 h |

**Rule:** never start a lower-priority item if the one above it isn't demo-ready. Recording (§9) ships with #2, not as a separate sprint. Post-call eval (#8) reuses the stitched audio from #2 — build the stitcher first, eval second.

## Measurement / Demo Requirements (most important)
Every win must be visible as a number on the right pane:
- **Cost win:** flip Prompt Caching OFF, run scripted exchange, end call. Flip ON, re-run. Show tokens and $ saved.
- **Perceived latency win:** point at perceived vs actual latency bars — filler makes agent feel faster even when pipeline ms unchanged.
- **Phrase cache win:** greeting turn shows TTS TTFB = 0 ms in latency pane.
- **Robustness win:** interrupt the agent mid-sentence — barge-in fires, pipeline trace shows cancel, conversation continues.
- **Latency win:** flip Speculative Generation OFF, run exchange, note median TTFT. Flip ON, show drop + hit rate %.
- **Memory win:** call 1 mention something specific. Call 2 — agent references it. Memory tab highlights entry used.
- **Post-call win** *(if built):* end call → Tab 5 populates with summary + flagged issues/wins while you talk judges through live metrics.

## Deliverables

1. **Build plan** — follow [Build Priority](#build-priority--stop-when-time-runs-out) table above. Cut ruthlessly.

2. **Repo skeleton** with:
   - `backend/`:
     - `main.py` — FastAPI + client WebSocket orchestrator (provider-agnostic)
     - `config.py` — `ASR_PROVIDER`, `TTS_PROVIDER`, API keys (`OPENAI`, `SARVAM`, `GEMINI`)
     - `providers/asr/` — `base.py`, `asr_sarvam.py`, `asr_openai_realtime.py`, `asr_openai_batch.py`, `asr_gemini_live.py`, `asr_gemini_batch.py`, `factory.py`
     - `providers/tts/` — `base.py`, `tts_sarvam.py`, `tts_openai.py`, `tts_gemini.py`, `factory.py`
     - `vad.py` — Silero VAD (shared; runs during agent playback for barge-in)
     - `llm_openai.py` — text LLM with prompt caching + optional cheap-model routing
     - `filler.py` — play acknowledgment clip while LLM pending
     - `phrase_cache.py` — disk lookup + pregen integration
     - `barge_in.py` — cancel LLM + TTS on user speech during playback
     - `speculation.py` — partial-triggered LLM with abort/restart logic
     - `recording.py` — PCM append + stitch to WAV on End Call
     - `post_call_eval.py` — async job: transcript + metrics → eval JSON
     - `memory.py`, `metrics.py`, `db.py`
     - `scripts/pregen_phrases.py` — one-time TTS pre-generation for phrase cache
   - `recordings/` — gitignored session audio
   - `frontend/`: Next.js app with `Transcript`, `PipelineTrace`, `CostPanel`, `LatencyPanel`, `MemoryPanel`, `PostCallPanel`, `Toggles`, `SystemPromptEditor`, `RecordingPlayer`
   - `README.md` — run instructions, env vars, provider swap guide
   - `MockASR` / `MockTTS` stubs for end-to-end before real APIs

3. **Frontend layout** — split-pane with mock data first; lock layout before backend integration.

4. **Demo script** — 3-minute judge flow: scripted utterances, interrupt once (barge-in), toggle flips, which metric to point at after each beat.

## Constraints
- One day, two-to-three people building. Cut ruthlessly.
- No vector DBs. No LangChain / LlamaIndex / agent frameworks. Plain functions, plain SQL.
- The metrics pane is mandatory. Skip a feature before you skip its measurement.
- **Provider compatibility is non-negotiable:** orchestrator code must never import Sarvam, OpenAI, or Google SDKs directly — only `factory.create_asr()` / `factory.create_tts()`. Adding a vendor = new adapter file + factory branch, zero pipeline changes.
- Ship Sarvam adapters first; OpenAI and Gemini adapters can be stubs on day one as long as mock implementations run end-to-end.
- Do not use end-to-end speech-to-speech models (OpenAI `gpt-realtime-2`, Gemini Live with model audio driving replies) — incompatible with the modular demo architecture.