# Voice Pipeline Work Distribution

## Purpose

This document splits the voice agent pipeline into independent workstreams for two engineers. The goal is to let both people work in parallel, merge cleanly, and avoid ambiguity around what is displayed, what is spoken, what is cached, and what is measured.

The split is based on ownership boundaries:

- Engineer A owns conversation intelligence: memory, prompts, planning, post-call analysis, and phrase decision logic.
- Engineer B owns voice runtime: ASR, VAD, TTS, audio cache, playback, streaming events, and latency measurement.

Both engineers integrate through a small shared contract: `UserTurn` in, `AssistantPlan` out, `SpokenSegment` to the voice runtime.

## Shared Contract

The shared contract is the highest-priority implementation boundary. Do this before deep feature work.

### UserTurn

`UserTurn` is the normalized input from the voice runtime to the conversation engine.

Expected fields:

```ts
type UserTurn = {
  session_id: string;
  user_id: string;
  turn_id: string;
  user_text: string;
  asr_provider: "gemini_live" | "openai_realtime" | "mock";
  vad_mode: "local_manual";
  timestamps: {
    vad_start_ms?: number;
    asr_first_partial_ms?: number;
    asr_final_ms?: number;
  };
  conversation_history: ConversationMessage[];
  memory_context?: MemoryContext;
  runtime_flags: {
    smart_routing: boolean;
    speculative: boolean;
    phrase_cache: boolean;
    filler: boolean;
    memory: boolean;
  };
};
```

### AssistantPlan

`AssistantPlan` is the normalized output from the conversation engine.

Expected fields:

```ts
type AssistantPlan = {
  assistant_turn_id: string;
  display_text: string;
  spoken_segments: SpokenSegment[];
  route: {
    lane: "NO_LLM" | "CACHE" | "FAST_LLM" | "SMART_LLM" | "ASYNC";
    model?: string;
    reason: string;
  };
  memory_updates?: MemoryUpdate[];
  post_call_jobs?: PostCallJob[];
  validation: {
    display_matches_spoken: boolean;
    contains_private_memory: boolean;
    safe_to_speak: boolean;
  };
  metrics: Record<string, unknown>;
};
```

### SpokenSegment

`SpokenSegment` decides exactly what is spoken and what is shown.

Expected fields:

```ts
type SpokenSegment = {
  id: string;
  type: "text" | "prefilled_phrase" | "silence" | "earcon";
  text?: string;
  phrase_id?: string;
  should_display: boolean;
  should_speak: boolean;
  cache_policy: "required" | "prefer" | "bypass";
  voice?: string;
  locale?: string;
};
```

Rules:

- The UI displays `display_text`, not a reconstructed TTS string.
- TTS speaks only segments where `should_speak=true`.
- Transcript appends only text where `should_display=true`, unless a segment is explicitly marked internal.
- Cached phrase audio is selected by `phrase_id`, not by fuzzy matching generated text.
- Any mismatch between displayed text and spoken text must be intentional and visible in `validation.display_matches_spoken`.

## Engineer A: Conversation Intelligence

Engineer A owns what the assistant should say and why.

### A1. Chat LLM Prompting

Expected work:

- Create a Jinja-style prompt template structure for the live chat LLM.
- Separate system policy, voice style, memory context, session facts, conversation history, and current user turn.
- Define which data is always included and which data is conditionally included.
- Define prompt variants for fast answers, smart reasoning, clarifications, and post-call jobs.
- Keep voice responses short, natural, and TTS-safe.
- Avoid markdown, bullets, tables, and visual-only formatting in spoken responses.

Expected files or modules:

- `backend/conversation/templates/`
- `backend/conversation/planner.py`
- `backend/conversation/routes.py`
- Tests under `backend/tests/`

Acceptance criteria:

- Given a `UserTurn`, the planner can produce an `AssistantPlan`.
- Prompt tests prove memory is included only when enabled and relevant.
- Prompt tests prove hidden policy/system content is not leaked into `display_text` or spoken segments.
- The live backend can still produce a normal response when memory is disabled.

### A2. Routing Logic

Expected work:

- Implement route selection before calling the LLM.
- Define lanes:
  - `NO_LLM`: greetings, thanks, simple confirmation, repeat last response.
  - `CACHE`: common acknowledgements and transition phrases.
  - `FAST_LLM`: simple answer, rewrite, short clarification.
  - `SMART_LLM`: reasoning-heavy or ambiguous user requests.
  - `ASYNC`: post-call summaries, sentiment, memory extraction.
- Replace word-count-only routing with route reasons that are inspectable in metrics.

Acceptance criteria:

- Unit tests cover at least 15 representative user utterances.
- Each route includes a machine-readable reason.
- Metrics show route lane and selected model.
- Route logic can be tested without ASR/TTS providers.

### A3. Memory

Expected work:

- Define memory schema for durable user facts, preferences, prior issues, and call outcomes.
- Define what can be recalled into the live prompt.
- Define what must remain post-call only.
- Build relevance filtering so unrelated memory is not injected.
- Add safety rules for sensitive or stale memory.

Expected memory categories:

- User preference: communication style, preferred name, channel preference.
- Durable fact: policy type, region, product context.
- Recent issue: open complaint, unresolved billing problem.
- Sentiment trend: frustration, satisfaction, confusion.
- Do-not-use/private notes: internal-only or low-confidence observations.

Acceptance criteria:

- Memory retrieval returns structured `MemoryContext`, not raw database rows.
- Low-confidence memory is not injected by default.
- Sensitive memory requires explicit classification before use.
- Tests prove unrelated memory does not enter the prompt.

### A4. Post-Call Analysis

Expected work:

- Build post-call jobs that run after the live conversation path.
- Analyze transcript and optionally audio-derived signals.
- Produce summary, sentiment, outcome, action items, and memory candidates.
- Keep post-call work off the latency-critical response path.

Expected outputs:

```ts
type PostCallAnalysis = {
  summary: string;
  user_sentiment: "positive" | "neutral" | "negative" | "mixed";
  sentiment_evidence: string[];
  outcome: "resolved" | "unresolved" | "handoff" | "abandoned";
  action_items: string[];
  memory_candidates: MemoryUpdate[];
  quality_flags: string[];
};
```

Acceptance criteria:

- Post-call analysis can run from stored turns without a live WebSocket.
- It does not block `tts.done` or `metrics.turn`.
- It stores durable summaries separately from raw generated benchmark or test artifacts.
- Tests cover positive, negative, mixed, and unresolved call transcripts.

### A5. Prefilled Phrase Decision Logic

Expected work:

- Define the phrase taxonomy.
- Decide when to use a phrase ID instead of generated text.
- Ensure phrase choices can be displayed, spoken, both, or neither.
- Avoid relying only on fuzzy text matching of generated LLM output.

Phrase categories:

- `ack`: "Got it, let me check that."
- `progress`: "I am pulling that up now."
- `clarify`: "Can you clarify what you mean?"
- `handoff`: "I may need a little more information."
- `closing`: "Anything else I can help with?"
- `error`: "I had trouble loading that. Let me try again."
- `barge_in`: "Go ahead."

Acceptance criteria:

- Phrase decisions produce `SpokenSegment(type="prefilled_phrase", phrase_id=...)`.
- Phrase IDs are stable and versioned.
- Tests verify no phrase is used in a semantically wrong context.
- The planner can fall back to text if a cached phrase is unavailable.

## Engineer B: Voice Runtime

Engineer B owns how speech and audio move through the product.

### B1. ASR

Expected work:

- Maintain provider adapters for `gemini_live`, `openai_realtime`, and `mock`.
- Normalize partial and final transcript events.
- Keep ASR selection runtime-configurable from the UI.
- Track ASR timings:
  - VAD start.
  - First partial.
  - Final transcript.
  - Provider used.
- Handle provider startup failures with clear WebSocket `error` messages.

Acceptance criteria:

- Invalid provider names fail before connecting to external APIs.
- Missing keys fail with actionable messages.
- Every user turn emits `asr.final` before a conversation plan is created.
- Metrics include `asr_streaming=true`, `asr_provider`, and `asr_transport`.

### B2. VAD And Barge-In

Expected work:

- Preserve local manual VAD mode.
- Keep silence flushing stable.
- Detect speech during agent playback with a separate barge-in path.
- Cancel active LLM/TTS/playback on barge-in.
- Prevent stale audio chunks from being emitted after cancellation.

Acceptance criteria:

- Barge-in emits `barge_in` and `playback.cancel`.
- Active generation tasks are cancelled or ignored cleanly.
- Playback queue is cleared in the frontend.
- Metrics include `barge_in_ms` and `playback_cancelled` when relevant.

### B3. TTS Structure

Expected work:

- Accept `SpokenSegment[]` from the conversation engine.
- Speak only segments with `should_speak=true`.
- Use cached phrase audio when `type="prefilled_phrase"` and cache exists.
- Use provider TTS for text segments.
- Keep sentence-level streaming for text segments.
- Emit consistent runtime events:
  - `tts.sentence_start`
  - `tts.audio_chunk`
  - `tts.done`
  - `metrics.turn`

Acceptance criteria:

- Every assistant turn emits at least one of:
  - spoken audio chunks, or
  - an explicit no-speech completion event for silent turns.
- Text-to-speech does not wait for the entire response when sentence streaming is possible.
- `llm.response` is emitted for transcript/display.
- `tts.done` is emitted after all spoken segments complete.

### B4. TTS Latency

Expected work:

- Measure TTS time to first byte per provider.
- Measure total TTS synthesis duration.
- Separate LLM TTFT from TTS TTFB.
- Compare latency across:
  - Gemini TTS.
  - OpenAI TTS.
  - Cached phrase audio.
  - Sentence-level streaming.
  - Full-response synthesis.
- Add metrics to the UI for current and historical turns.

Acceptance criteria:

- `metrics.turn` contains `tts_start_ms`, `tts_first_audio_ms`, `tts_provider`, and `tts_streaming`.
- Cached phrase TTFB is measured separately from provider TTS.
- Live smoke confirms at least one `tts.audio_chunk`.
- A benchmark or smoke path can be run for both Gemini and OpenAI TTS.

### B5. Phrase Audio Cache

Expected work:

- Build cache around phrase IDs, not generated text.
- Cache key must include:
  - `phrase_id`
  - phrase text version
  - provider
  - voice
  - locale
  - sample rate
  - audio format
- Add pre-generation support for common phrase audio.
- Add runtime fallback when cache misses.

Acceptance criteria:

- Cache hits are deterministic for the same phrase ID and voice config.
- Cache invalidates when phrase text or voice changes.
- Metrics include `phrase_cache_hit`.
- Cache artifacts remain ignored unless deliberately promoted as durable fixtures.

### B6. Frontend Playback And Display Validation

Expected work:

- Show `display_text` in the transcript.
- Play only runtime audio chunks tied to `SpokenSegment.should_speak=true`.
- Cancel playback immediately on `playback.cancel`.
- Show ASR/TTS provider choices in the UI.
- Show current turn latency breakdown.
- Make it clear when a segment was spoken but not displayed, or displayed but not spoken.

Acceptance criteria:

- UI provider selectors remain the runtime source of truth.
- Transcript is not inferred from TTS audio chunks.
- If `display_text` differs from spoken text, the difference is represented in metrics/debug view.
- Frontend build passes.

## Integration Plan

### Phase 1: Contract First

Owner: both engineers.

Tasks:

- Add shared Python dataclasses or Pydantic models for `UserTurn`, `AssistantPlan`, and `SpokenSegment`.
- Add serialization tests.
- Convert the current live path to create a simple `AssistantPlan`.
- Keep current provider selectors and live WebSocket behavior unchanged.

Acceptance criteria:

- Existing live smoke still passes.
- Existing backend tests still pass.
- The current assistant response can be represented as one text `SpokenSegment`.

### Phase 2: Parallel Development

Engineer A:

- Build planner, prompt templates, route logic, memory context, and post-call analysis.
- Produce `AssistantPlan` objects with phrase IDs and display/speech flags.

Engineer B:

- Update voice runtime to consume `SpokenSegment[]`.
- Build phrase audio cache keyed by phrase ID.
- Improve TTS latency instrumentation and UI metrics.

Acceptance criteria:

- Engineer A can run planner tests without live ASR/TTS.
- Engineer B can run voice runtime tests with mock `AssistantPlan` fixtures.
- Neither engineer needs to call the other's live provider code for unit tests.

### Phase 3: Merge And Live Validation

Tasks:

- Wire conversation planner into the WebSocket loop after `asr.final`.
- Send `AssistantPlan.spoken_segments` to the TTS runtime.
- Send `AssistantPlan.display_text` to the frontend transcript.
- Persist route, memory, phrase, and latency metrics.
- Run live smokes for Gemini and OpenAI provider combinations.

Acceptance criteria:

- Gemini smoke passes:

```bash
python3 -m backend.scripts.live_smoke \
  --audio backend/harvard.wav \
  --asr-provider gemini_live \
  --tts-provider gemini
```

- OpenAI smoke passes:

```bash
python3 -m backend.scripts.live_smoke \
  --audio backend/harvard.wav \
  --asr-provider openai_realtime \
  --tts-provider openai
```

- Smoke receives:
  - At least one `asr.final`.
  - One non-empty `llm.response`.
  - At least one `tts.audio_chunk`.
  - `tts.done`.
  - `metrics.turn` with `asr_streaming=true`, `tts_streaming=true`, and `vad_mode=local_manual`.

## Validation Matrix

| Area | Owner | Required validation |
| --- | --- | --- |
| Provider selection | Engineer B | Unknown providers fail with WebSocket `error`; UI-selected providers are used at runtime |
| Missing keys | Engineer B | Missing selected provider keys fail before provider connection |
| ASR final | Engineer B | Every user turn appends transcript on `asr.final` |
| Chat planning | Engineer A | `UserTurn` produces valid `AssistantPlan` |
| Memory injection | Engineer A | Relevant memory included; unrelated/sensitive memory excluded |
| Post-call analysis | Engineer A | Runs after call; summary/sentiment/outcome persisted |
| Prefilled phrase decision | Engineer A | Phrase ID selected only in valid context |
| Phrase audio cache | Engineer B | Cache hit uses phrase audio; miss falls back to TTS |
| TTS latency | Engineer B | TTFB and total TTS are measured separately |
| Display vs speech | Both | `display_text` and `spoken_segments` behavior is explicit |
| Barge-in | Engineer B | Cancels playback and active generation cleanly |
| Frontend metrics | Engineer B | Latency/provider/route metrics visible in platform |

## Non-Goals For This Split

Do not include these in the first parallel slice unless explicitly reprioritized:

- Full user account management.
- Long-term vector search infrastructure.
- Production authentication.
- Replacing the whole frontend.
- Removing all mock post-call UI.
- Building a complete contact-center analytics product.

## Final Definition Of Done

The split is complete when:

- The backend has a stable `UserTurn -> AssistantPlan -> SpokenSegment[]` flow.
- Engineer A can evolve memory, prompts, route logic, phrase decisions, and post-call analysis independently.
- Engineer B can evolve ASR, VAD, TTS, phrase audio cache, playback, and latency independently.
- Live smoke passes for Gemini and OpenAI provider pairs.
- The UI clearly shows what happened in the turn: user transcript, assistant display text, spoken output status, providers, route, phrase/cache status, and latency.
