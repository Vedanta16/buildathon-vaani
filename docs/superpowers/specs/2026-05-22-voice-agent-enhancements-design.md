# Voice Agent Enhancements — Design Spec
**Date:** 2026-05-22  
**Status:** Approved  
**Scope:** Echo cancellation, provider swap (OpenAI + Gemini), smart model routing, speculative generation (full streaming), phrase cache fuzzy matching

---

## 1. Echo Cancellation

### Problem
Without echo cancellation, VAD on the server picks up the agent's own TTS output through the mic, triggering false barge-ins during agent speech.

### Design

**Browser layer (first defense):**  
`getUserMedia` called with:
```js
{ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }
```
Activates hardware-level AEC in the browser before audio reaches the server. Handles the majority of echo with zero server-side cost.

**Server layer (second defense):**  
`main.py` maintains `agent_playing: bool`.
- Set `True` when first `tts.audio_chunk` event is sent to client
- Set `False` on `tts.done` or `playback.cancel`

VAD processor skips frame analysis entirely while `agent_playing is True`. Even if browser AEC passes residual echo, VAD never processes it.

**Files affected:** `frontend/` (getUserMedia call), `main.py` (agent_playing flag + VAD gate), `vad.py` (accept/ignore gate parameter)

**No new dependencies.**

---

## 2. Provider Architecture — OpenAI + Gemini Only

Sarvam removed entirely. Factory pattern retained for clean adapter swap.

### ASR Adapters

| Key | API | Streaming partials | Notes |
|-----|-----|--------------------|-------|
| `openai_realtime` *(default)* | OpenAI Realtime API | Yes — `transcript.text.delta` | Primary; reliable partials |
| `gemini_live` | Gemini Live API WebSocket | Yes — `inputTranscription.text` | Adapter discards model audio turns; ASR-only mode |

Both emit `asr.partial` and `asr.final` via the internal event contract. Silero VAD remains server-side for both.

### TTS Adapters

| Key | API | Streaming | TTFB | Demo use |
|-----|-----|-----------|------|----------|
| `openai` *(default)* | `/v1/audio/speech` | Yes — chunked PCM | Low | Low-latency path |
| `gemini` | `generateContent` + audio modalities | No — full audio per request | Higher | Contrast demo: swap to show TTFB difference in latency pane |

Gemini TTS chunks on sentence boundaries (already planned). Each sentence → one `generateContent` call → one or more `tts.audio_chunk` events.

### LLM
OpenAI text API only (`gpt-4o` / `gpt-4o-mini`). Prompt caching always enabled. Gemini not used for LLM — OpenAI caching is the cost demo story.

### Config
```
ASR_PROVIDER=openai_realtime | gemini_live        # default: openai_realtime
TTS_PROVIDER=openai | gemini                      # default: openai
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```
Top-bar dropdowns mirror these and persist choice per session.

### Files
`providers/asr/asr_openai_realtime.py`, `providers/asr/asr_gemini_live.py`, `providers/asr/factory.py`  
`providers/tts/tts_openai.py`, `providers/tts/tts_gemini.py`, `providers/tts/factory.py`

---

## 3. Smart Model Routing

### Design
Routing decision in `llm_openai.py`, runs synchronously before every API call. Adds zero latency.

**Heuristic (evaluated in order):**
1. If normalized turn text is in `SHORT_ANSWER_SET` → small model
2. Else if `word_count(text) <= 8` → small model  
3. Else → large model

```python
SHORT_ANSWER_SET = {
    "yes", "no", "ok", "okay", "sure", "thanks", "thank you",
    "got it", "sounds good", "perfect", "great", "fine",
    "yep", "nope", "correct", "exactly", "absolutely",
    "go ahead", "send it", "do it", "keep it"
}
```

**Model map:**
```python
LARGE_MODEL = "gpt-4o"
SMALL_MODEL = "gpt-4o-mini"
```

### Metrics emitted per turn
```python
{
  "model_used": "gpt-4o-mini",
  "routed_small": True,
  "input_tokens": 312,
  "estimated_cost_usd": 0.000062
}
```
Feeds cost pane row: **"Turns routed to small model: X / est. $Y saved"**

### Toggle
`Smart Routing: ON/OFF` in top bar. When OFF, always use `LARGE_MODEL`.

---

## 4. Speculative Generation (Debounce-Triggered, Streaming Commit)

### Design

**Trigger condition:**  
ASR partial has not changed for **400ms** AND contains **≥ 5 words** → fire speculative LLM call.

**Model selection:**  
Speculative call passes through the smart routing heuristic. Short partials → `gpt-4o-mini`. Longer partials → `gpt-4o`. This means short turns often have their speculative call complete before the user finishes speaking.

**On final ASR arrival:**
```python
ratio = SequenceMatcher(None, speculative_input, final_text).ratio()
if ratio >= 0.85:
    # commit — let in-flight generation continue
    commit_speculative_task()
else:
    # discard — cancel and fire fresh call
    speculative_task.cancel()
    fire_llm_call(final_text)
```

**Abort mechanism:** `asyncio.Task.cancel()` on the speculative task. Clean, no partial token waste beyond what was already streamed.

**Pipeline trace tag:** Turns with committed speculation show `spec·mini` or `spec·4o` tag in the pipeline trace swimlane.

**Metric:** Spec hit rate % per session (committed / total turns).

**Toggle:** `Speculative Gen: ON/OFF` in top bar.

### Files
`speculation.py` — debounce timer, task management, similarity check, commit/discard logic

---

## 5. Phrase Cache with Fuzzy Matching

### Pre-generation
`scripts/pregen_phrases.py --tts-provider openai|gemini` generates ~50 stock phrases via the active TTS adapter. Output stored as:
```python
PHRASE_CACHE: dict[str, bytes] = {
    "got it one moment": b"<pcm_bytes>",
    "let me pull that up": b"<pcm_bytes>",
    "is there anything else i can help you with": b"<pcm_bytes>",
    # ...
}
```
Key = normalized form (lowercase, punctuation stripped, whitespace collapsed). Loaded into memory at server startup.

### Runtime Matching (per sentence chunk)
```python
def lookup_phrase(sentence: str) -> bytes | None:
    normalized = normalize(sentence)  # lowercase, strip punct, collapse ws
    
    # 1. Exact match — O(1)
    if normalized in PHRASE_CACHE:
        return PHRASE_CACHE[normalized]
    
    # 2. Fuzzy match
    best_ratio, best_key = max(
        ((SequenceMatcher(None, normalized, key).ratio(), key)
         for key in PHRASE_CACHE),
        key=lambda x: x[0]
    )
    if best_ratio >= 0.88:
        return PHRASE_CACHE[best_key]
    
    return None  # fall through to TTS
```

**Cache hit:** emit `tts.audio_chunk` with `source: "phrase_cache"`. TTFB = 0ms. No TTS API call.  
**Cache miss:** call TTS adapter normally.

### What caches in practice
- Ack/filler phrases: "Got it, one moment", "Let me check that", "One second"
- Transitions: "Let me pull that up", "I see that here", "Looking into that now"  
- Sign-offs: "Is there anything else I can help with?", "Have a great day"
- Dynamic content (names, numbers, dates) intentionally falls through to TTS

Realistic hit rate: ~15–20 of 50 phrases per call, but these are the highest-frequency turns.

### Metrics
- Cache hit count per session
- Latency pane: ⚡ icon on cache-hit turns, TTFB shown as 0ms
- TTS cost savings: estimated (cache_hits × avg_tts_cost_per_sentence)

### Files
`phrase_cache.py` — cache dict, `lookup_phrase()`, `normalize()`  
`scripts/pregen_phrases.py` — one-time generation script

---

## 6. Interaction Between Features

| Scenario | What fires |
|----------|-----------|
| Short turn ("yeah keep it") | Spec fires early on `gpt-4o-mini` → commits → agent response is ack phrase → phrase cache hit → TTFB 0ms |
| Long turn (substantive question) | Spec fires on `gpt-4o` after 400ms stable partial → may commit → TTS streams PCM chunks |
| Barge-in during TTS | `agent_playing = False` immediately → VAD resumes → `playback.cancel` sent to client |
| TTS provider = gemini | Full audio per sentence, higher TTFB → visible in latency pane vs openai streaming |

---

## 7. Demo Beats Enabled by This Design

1. **Short turn, everything fires:** "Yeah keep it" → spec·mini committed → phrase cache hit → latency pane shows sub-300ms perceived, TTFB = 0ms, small model badge
2. **Provider swap:** Switch TTS to Gemini live → TTFB jumps visibly in latency chart → switch back → drops again
3. **Echo test:** Speak over the agent mid-sentence → barge-in fires cleanly, no false triggers from agent audio
4. **Cost A/B:** Prompt cache OFF → run exchange → note $ spent. Cache ON → same exchange → "Saved by caching" widget shows delta. Smart routing row shows additional $ saved.

---

## 8. Out of Scope (this spec)

- Sarvam adapters (removed)
- Gemini LLM (OpenAI only for LLM to preserve prompt caching story)
- Cross-session memory (separate feature, priority 7 in build plan)
- Post-call eval (separate feature, priority 8)
- Smart routing with Gemini models (LLM stays on OpenAI)
