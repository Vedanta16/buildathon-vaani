# Cost, Latency, Memory Architecture

## Purpose

This document defines the production-v1 architecture for making the voice agent feel fast, stay cost-efficient, and become personalized without feeling creepy.

The core design principle is:

> Route by task, not by model tier.

The system should decide what kind of work is needed before building an LLM prompt. Many turns should not need a large model, and some should not need any LLM at all.

## Current Repo Fit

The current product plan and code already include several useful primitives:

- ASR and TTS provider adapters.
- Server-side VAD.
- Barge-in cancellation.
- Phrase cache with fuzzy matching.
- Filler audio hooks.
- Speculative LLM generation.
- Per-turn latency and token metrics UI.
- SQLite tables for sessions, turns, recordings, and memory blobs.

Important gaps:

- The written plan says OpenAI LLM with prompt caching, but the live backend currently imports the Gemini LLM path in `backend/main.py`, so cached-token reporting is not active in the main loop.
- Smart routing exists as a simple word-count/model heuristic in `backend/llm_openai.py`, but the live path does not use it as a full task router.
- Several frontend toggles are not fully wired to backend behavior, including prompt cache, phrase cache, filler, and memory.
- Memory is schema/UI/planned, not a production recall/update loop.
- Post-call eval is mock-backed.
- Route, retry, outcome, and quality metrics are not persisted consistently per turn.

## Architecture Overview

Every user turn should flow through a task router before any model call.

```text
User utterance
  -> normalize
  -> classify task
  -> choose lane
  -> emit immediate product state
  -> run deterministic/cache/model/tool work
  -> emit answer or async result
  -> log route, cost, latency, and outcome
```

The target lanes are:

| Lane | Use case | Typical latency | LLM |
| --- | --- | --- | --- |
| `NO_LLM` | greetings, thanks, repeat, simple confirms, state transitions | immediate | none |
| `CACHE` | acknowledgements, transitions, common clarifications, cached KB/retrieval | immediate to low | none or optional |
| `FAST_LLM` | simple rewrite, classification, lightweight clarification, short FAQ | low | small model |
| `SMART_LLM` | reasoning, ambiguous tasks, KB synthesis, tool-heavy work | higher | stronger model |
| `ASYNC` | post-call eval, memory extraction, summaries, quality scoring | off critical path | small or smart |

## Product State Streaming

Do not stream raw tokens as the primary product abstraction. Stream product state.

Core states:

- `ack`: immediate acknowledgement that work has started.
- `clarify`: ask for missing information.
- `progress`: tell the user what is happening if work is slow.
- `interrupt`: cancel playback and in-flight work.
- `answer`: final spoken response.
- `async_result`: post-call or deferred result.

Recommended event flow:

```text
ASR final
  -> route selected
  -> ack emitted immediately
  -> tool/model work starts
  -> progress emitted if slow
  -> answer emitted
  -> async eval/memory jobs scheduled
```

The important rule:

> The initial acknowledgement must not wait for the LLM.

## Smart Routing

Smart routing should happen in two layers:

1. Task routing: decide whether this is deterministic, cacheable, fast-model, smart-model, or async work.
2. Provider/model routing: choose the concrete provider and model for that lane.

### Routing Inputs

The router should consider:

- Normalized user text.
- ASR final vs partial status.
- Current conversation state.
- Whether the user is interrupting or correcting.
- Required tools or KB lookups.
- Memory relevance.
- Safety/risk level.
- Latency budget.
- Cache availability.
- Feature flags.

### Example Routes

| User utterance | Route | Behavior |
| --- | --- | --- |
| "Hi" | `NO_LLM` / `CACHE` | Play cached greeting. |
| "Okay" | `NO_LLM` / `CACHE` | Play short cached acknowledgement or continue silently. |
| "Thanks" | `NO_LLM` / `CACHE` | Play cached closing or acknowledgement. |
| "Can you repeat that?" | `NO_LLM` | Replay or restate the last assistant response. |
| "What do you mean?" | `FAST_LLM` | Generate one short clarification. |
| "Can you check my policy?" | `FAST_LLM` plus tool/KB | Ack immediately, then retrieve policy. |
| "Compare both renewal options" | `SMART_LLM` | Use larger model with relevant policy context. |
| "Summarize this call" | `ASYNC` | Schedule post-call summary/eval. |

### Small vs Large LLM

Small models should handle:

- Intent classification.
- Slot extraction.
- Short clarification.
- Voice-style rewrite.
- Simple FAQ responses.
- Memory suggestion extraction.
- Post-call summaries when quality bar is moderate.

Large or reasoning models should handle:

- Multi-step reasoning.
- Conflicting context.
- Tool-heavy synthesis.
- Sensitive or high-risk answers.
- Complex comparison.
- Ambiguous user goals that require judgment.

Word count can be a fallback heuristic, but it should not be the primary decision rule. A short query can be complex, and a long query can be operationally simple.

Examples:

| Query | Bad routing by length | Better routing by task |
| --- | --- | --- |
| "Hi" | small LLM | `NO_LLM` cached greeting |
| "Why?" | small LLM | depends on previous turn; may need `SMART_LLM` |
| "Please send the same link again to my phone" | large LLM due to length | deterministic/tool route |
| "Compare liability and collision coverage" | small if short | `SMART_LLM` |

## Prefilled Phrase Cache

Prefilled phrases should cover conversational moments where the agent needs to feel responsive but does not need dynamic language.

Phrase categories:

- `ack`: "Got it, let me check that."
- `progress`: "I am pulling that up now."
- `clarify`: "Can you clarify what you mean?"
- `handoff`: "I may need a little more information."
- `closing`: "Anything else I can help with?"
- `error`: "I had trouble loading that. Let me try again."
- `barge_in`: "Go ahead."

The phrase cache should store:

- Short phrase ID.
- Text.
- Category.
- Optional route tags.
- Cached audio bytes or path.
- Voice/provider metadata.
- Locale.
- Version.

Example:

```json
{
  "A1": {
    "text": "Got it, let me check that.",
    "category": "ack",
    "routes": ["policy_lookup", "kb_lookup"],
    "cacheable": true
  },
  "A2": {
    "text": "One moment, I am pulling that up.",
    "category": "ack",
    "routes": ["tool_call", "retrieval"],
    "cacheable": true
  },
  "C1": {
    "text": "Can you clarify what you mean?",
    "category": "clarify",
    "routes": ["clarify"],
    "cacheable": true
  }
}
```

## Phrase IDs Instead Of Text Matching

Do not rely only on prompting the LLM to reproduce cached text exactly. That is brittle and wastes tokens.

Use phrase IDs.

The model may output:

```json
{
  "segments": [
    { "p": "A1" },
    { "t": "Your roadside coverage is active through June fourth." }
  ]
}
```

Pipeline behavior:

```text
{"p":"A1"} -> phrase registry -> cached PCM -> instant playback
{"t":"..."} -> TTS provider -> streamed/generated audio
```

The router owns the initial acknowledgement. The LLM can use phrase IDs for later transitions, clarifications, progress updates, and closing phrases.

## Token-Efficient Prompt Design

Phrase IDs should be sent to the LLM as a compact manifest, not as verbose metadata.

Bad:

```text
You can use the following acknowledgement phrase when you want to tell the user that you understood their question and will now check the relevant system: "Got it, let me check that."
```

Better:

```text
Phrases:
A1=Got it, let me check that.
A2=One moment, I am pulling that up.
C1=Can you clarify that?
```

Compact response format:

```text
Return JSON: {"segments":[{"p":"ID"}|{"t":"spoken text"}]}
Use p only for exact approved phrases.
```

Example full compact prompt section:

```text
Return JSON: {"segments":[{"p":"ID"}|{"t":"spoken text"}]}
Use p only for exact approved phrases. Do not invent IDs.

Phrases:
A1=Got it, let me check that.
A2=One moment, I am pulling that up.
C1=Can you clarify that?
S1=Anything else I can help with?
```

Prompt efficiency rules:

- Use short IDs such as `A1`, `C1`, `S1`.
- Send only phrases relevant to the selected route.
- Keep globally common phrase IDs stable in the cacheable prompt prefix.
- Put route-specific phrases lower in the prompt.
- Do not include descriptions unless the model needs them.
- Do not send the entire phrase DB every turn.
- Do not include cached audio metadata in the prompt.

## Prompt Layout

For prompt caching and progressive context, keep stable content at the top and dynamic content at the bottom.

Recommended structure:

```text
[Static system instructions]
[Stable response schema]
[Stable common phrase IDs]
[Stable tool-use rules]
[Reviewed long-term memory block, if enabled and unchanged]
[Route-specific instructions]
[Route-specific phrase IDs]
[Selected relevant tools only]
[Selected relevant KB/context only]
[Recent conversation slice]
[Current user turn]
```

The model should not receive all tools, all memory, or all conversation history by default. Context should be added progressively after routing and discovery.

## Acknowledgement Strategy

Acknowledgements should be selected before the LLM call.

```text
User finishes speaking
  -> router selects route
  -> router picks ack phrase ID
  -> cached audio plays immediately
  -> LLM/tool/API work runs
```

Examples:

| Route | Ack phrase |
| --- | --- |
| policy lookup | `A1=Got it, let me check that.` |
| KB retrieval | `A2=One moment, I am pulling that up.` |
| complex reasoning | `A3=Let me think that through.` |
| no LLM greeting | `G1=Hi, how can I help?` |

This improves perceived latency without making the model responsible for the first response.

## Caching Layers

Use multiple caches with different goals.

| Cache | Purpose | Key |
| --- | --- | --- |
| Phrase cache | Instant spoken common phrases | phrase ID and voice/version |
| Prompt cache | Lower LLM input cost | stable prompt prefix |
| Route cache | Avoid repeated classification | normalized utterance plus state |
| Retrieval cache | Avoid repeated KB/tool lookup | query/tool params |
| Response cache | Reuse safe deterministic responses | task and normalized slots |

Prompt cache depends on byte-stable prefixes. Avoid injecting volatile data into the top of the prompt.

## API Contracts

Production v1 should define explicit backend contracts so the frontend does not infer state from ad hoc messages.

Minimum WebSocket events:

| Event | Direction | Purpose |
| --- | --- | --- |
| `turn.route` | server -> client | Route/lane/model decision for the current turn. |
| `turn.ack` | server -> client | Immediate acknowledgement phrase ID and playback metadata. |
| `turn.progress` | server -> client | Slow-work progress state, such as retrieval or tool execution. |
| `turn.answer` | server -> client | Final structured answer segments. |
| `metrics.turn` | server -> client | Turn-level observability row for the right panel. |
| `playback.cancel` | server -> client | Stop active audio due to barge-in or cancellation. |
| `async.started` | server -> client | Post-call or background job started. |
| `async.completed` | server -> client | Post-call or background job completed. |

Minimum HTTP endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /sessions` | Session history. |
| `GET /sessions/{id}` | Session detail with turns and metrics. |
| `GET /sessions/{id}/post-call-report` | Real post-call analysis. |
| `GET /sessions/{id}/recording.wav` | Play/download stitched recording. |
| `GET /users/{id}/memory` | Reviewed long-term memory. |
| `GET /sessions/{id}/memory-suggestions` | Pending suggestions from post-call eval. |
| `POST /memory-suggestions/{id}/decision` | Accept or reject a memory suggestion. |

All events and endpoints should return explicit empty/loading/error states. The frontend should not synthesize fake fallback content.

## Memory And Personalization

Memory should move the product from "who are you?" to "it knows me" without feeling invasive.

Current repo state:

- `memory_blobs` exists in SQLite schema.
- `memory_block` exists as an optional LLM argument in both LLM modules.
- The frontend has a memory toggle and mock memory references in transcript UI.
- The post-call panel has mock memory suggestions.
- There is no real memory retrieval, relevance gating, acceptance flow, or long-term memory update path yet.

Memory types:

- Short-term memory: current call facts, unresolved slots, recent user corrections.
- Session summary: compact summary created after a call.
- Long-term reviewed memory: explicit user facts/preferences approved before persistence.
- Negative memory: things not to assume or not to mention.
- Tone/prosody memory candidates: emotional context from post-call analysis, such as frustration during pricing or satisfaction after issue resolution. These should be suggestions only, not auto-saved.

Production-v1 rule:

> Long-term memory requires explicit review before it is saved.

Memory flow:

```text
Call ends
  -> async eval extracts memory suggestions
  -> suggestions shown to user/operator
  -> accepted suggestions become long-term memory
  -> rejected suggestions are not used
```

Avoid creepy cold starts by:

- Using declared profile data before inferred data.
- Asking lightweight preference questions.
- Showing when memory was used.
- Letting the user remove memory.
- Avoiding sensitive inferences unless explicitly provided and needed.

### Post-Call Recording Analysis

The system should use the completed call recording and timeline after the live call ends. This is async and should never block the real-time voice loop.

Inputs:

- Stitched call recording path.
- Turn transcript.
- Speaker timeline.
- Per-turn latency/cost/route metrics.
- Barge-in/interruption events.

Outputs:

- Summary.
- Sentiment arc.
- User tone timeline.
- Talk ratio.
- Key moments.
- Wins.
- Issues.
- Suggested memory updates.
- Follow-up actions.
- Quality score.

Tone analysis should be displayed separately from factual memory. Tone can inform coaching and memory suggestions, but it should not silently become long-term memory.

Example post-call analysis shape:

```json
{
  "summary": "User called to renew a policy and resolve a broken link.",
  "sentiment_arc": ["neutral", "frustrated", "relieved"],
  "tone_segments": [
    {
      "start_ms": 4000,
      "end_ms": 18000,
      "speaker": "user",
      "tone": "frustrated",
      "confidence": 0.82,
      "evidence": "Broken renewal link mentioned with elevated urgency."
    }
  ],
  "talk_ratio": {
    "user_pct": 42,
    "agent_pct": 58
  },
  "wins": [
    "Agent acknowledged quickly before lookup."
  ],
  "issues": [
    "Agent response overlapped user barge-in."
  ],
  "suggested_memory_updates": [
    {
      "type": "preference",
      "text": "Prefers SMS links for policy renewal.",
      "confidence": 0.91,
      "requires_review": true
    }
  ],
  "quality_score": 0.86
}
```

### Functional Memory UI

The frontend should show real memory state, not mock data:

- Current reviewed memory for the selected user.
- Memory used during this call, with transcript highlights.
- Memory suggestions generated after the call.
- Accept/reject buttons for each suggestion.
- Rejected suggestions should remain rejected and not reappear unchanged.
- Tone-derived suggestions should be visually marked as inferred and require review.

Acceptance behavior:

```text
User clicks accept
  -> POST memory suggestion decision
  -> backend writes reviewed memory
  -> UI updates current memory block
  -> future calls can retrieve it
```

Rejection behavior:

```text
User clicks reject
  -> backend records rejection
  -> suggestion is not injected into future prompts
```

## Privacy, Consent, And Retention

Recording and memory features require clear boundaries.

Production requirements:

- Show that recording is active during a call.
- Store recordings only for sessions where recording is enabled.
- Make recording retention configurable.
- Keep transcript, recording, post-call eval, and memory records linked to the session/user.
- Do not save sensitive inferred facts as long-term memory without explicit approval.
- Let reviewed memory be deleted or edited.
- Keep rejected memory suggestions out of future prompts.
- Mark tone/prosody output as inferred analysis, not factual truth.
- Avoid using tone labels to make high-impact decisions about the user.

Default retention policy for v1:

- Keep session transcript and metrics.
- Keep recording while the session report is needed.
- Keep accepted memory until deleted.
- Keep rejected memory suggestion fingerprints only to avoid repeated suggestions.

## Failure And Degraded Modes

The product should fail explicitly and preserve the live call when possible.

| Failure | Behavior |
| --- | --- |
| Phrase cache miss | Fall back to live TTS. |
| Prompt cache miss | Continue normally, log cache miss. |
| Router uncertain | Use conservative route, usually `FAST_LLM` or `SMART_LLM` based on risk. |
| Small model low confidence | Escalate to `SMART_LLM`. |
| LLM error | Play cached apology/progress phrase, retry once if safe, then show failure. |
| TTS error | Show text response and log audio failure. |
| Post-call eval failure | Keep session data and allow retry. |
| Recording stitch failure | Keep raw segments if available and show report without audio. |
| Memory write failure | Do not lose suggestion decision; retry or show explicit error. |
| Backend unavailable | Frontend shows unavailable state, not mock data. |

Retry policy should be route-aware:

- Do not retry deterministic/cache routes unless the cache read itself failed.
- Retry transient provider errors once for live responses.
- Move long retries to async where possible.
- Log retry count per turn.

## Async Work

Async work should never block the live voice loop.

Async jobs:

- Post-call summary.
- Recording transcription or transcript correction.
- User tone and sentiment analysis from recording/timeline.
- Quality evaluation.
- Memory suggestion extraction.
- Cost/latency analysis.
- Follow-up generation.
- Long-running tool workflows.

Async events should update UI as background results:

```text
post_call_eval.started
post_call_eval.completed
memory_suggestions.available
memory_suggestion.accepted
memory_suggestion.rejected
quality_outcome.logged
```

## Observability

Every turn should log:

- `route`
- `lane`
- `provider`
- `model`
- `input_tokens`
- `output_tokens`
- `cached_tokens`
- `ttft_ms`
- `tts_ttfb_ms`
- `perceived_latency_ms`
- `actual_latency_ms`
- `phrase_cache_hit`
- `prompt_cache_hit`
- `retrieval_cache_hit`
- `retry_count`
- `interrupted`
- `outcome`

### Live Right Panel

During a sample call, the right panel should show turn-level observability as soon as each turn completes. The minimum live row per turn is:

| Field | Meaning |
| --- | --- |
| `turn_id` | Sequential turn number for the active call. |
| `route` | Chosen task route, such as `greeting`, `policy_lookup`, `clarify`, or `reason`. |
| `lane` | `NO_LLM`, `CACHE`, `FAST_LLM`, `SMART_LLM`, or `ASYNC`. |
| `model` | Concrete model used, or `none` for deterministic/cache-only turns. |
| `input_tokens` | Total model input tokens for the turn. |
| `output_tokens` | Total model output tokens for the turn. |
| `cached_tokens` | Prompt tokens served from provider prompt cache. |
| `ttft_ms` | Time to first model token for LLM-backed routes. |
| `tts_ttfb_ms` | Time to first answer audio byte. |
| `perceived_latency_ms` | Time to first user-perceived audio, including ack/filler. |
| `cache_hit` | Whether phrase, prompt, retrieval, or response cache was used. |
| `retry_count` | Number of provider/tool retries for the turn. |
| `outcome` | Success, clarification needed, interrupted, failed, or deferred. |

The right panel should also show active-call totals:

- Input tokens.
- Output tokens.
- Cached tokens.
- Cache hit rate.
- Route distribution.
- Average and p95 TTFT.
- Estimated cost by route.
- Retry rate.

For `NO_LLM` and `CACHE` turns, token fields should display `0`, model should display `none`, and route/lane should still be visible. This makes the cost win obvious during the demo instead of hiding deterministic turns.

### Removing Mock Data

Production v1 should remove or strictly isolate frontend mock data. Mock fixtures can remain only for tests/storybook-like local demos, not as fallback UI during real calls.

Current mock-backed areas:

- Transcript fallback in `frontend/Sample Buildathon/transcript.jsx`.
- Pipeline, latency, cost, and post-call report fallback in `frontend/Sample Buildathon/right-pane.jsx`.
- Mock users, sessions, transcript, metrics, post-call report, memory suggestions, and cost turns in `frontend/Sample Buildathon/data.js`.
- Equivalent mock imports under `frontend/src/`.

Required backend-backed replacements:

| UI area | Backend source |
| --- | --- |
| Active transcript | Live WebSocket `asr.final` and `llm.response` events. |
| Turn metrics | Live WebSocket `metrics.turn` events persisted in `turns.metrics_json`. |
| Session history | `GET /sessions`. |
| Post-call report | `GET /sessions/{id}/post-call-report`. |
| Recording player | `GET /sessions/{id}/recording.wav` or equivalent static file route. |
| Memory block | `GET /users/{id}/memory`. |
| Memory suggestions | `GET /sessions/{id}/memory-suggestions`. |
| Accept/reject memory | `POST /memory-suggestions/{id}/decision`. |

Frontend behavior:

- If real data is loading, show loading/empty states.
- If backend data is unavailable, show an explicit error state.
- Do not silently fall back to fake post-call reports, fake waveforms, fake memory suggestions, or fake costs.
- Keep test fixtures separate from runtime app bundles.

Every session should aggregate:

- Route distribution.
- Cost per route.
- Cache hit rate by cache type.
- Retry rate.
- Median and p95 perceived latency.
- Median and p95 actual latency.
- Speculation hit rate.
- Memory suggestions accepted/rejected.
- Quality score.

## Evaluation Loop

Post-call eval should assess:

- Did the agent complete the user task?
- Was the selected route appropriate?
- Did the agent use an LLM unnecessarily?
- Was latency acceptable?
- Were retries or interruptions handled cleanly?
- Did memory help or hurt?
- Did the agent reveal or infer anything creepy?

Eval output should feed dashboards and future routing improvements. It should not silently mutate memory or routing policies without review.

## Suggested Implementation Plan

1. Add a design-level `TaskRouter` interface and route metadata shape.
2. Add phrase registry with short IDs and route/category tags.
3. Change response handling to support mixed phrase/text segments.
4. Make router-owned acknowledgement happen before model calls.
5. Add provider abstraction for normalized OpenAI/Gemini usage metrics.
6. Wire OpenAI provider where prompt-cache metrics are required.
7. Persist route, cache, latency, retry, and outcome data per turn.
8. Add async post-call eval and memory suggestion jobs.
9. Add explicit memory review UI/backend flow.
10. Update metrics UI to show route mix, cache hit rate, retry rate, and quality outcome.

## Test Plan

Router tests:

- "Hi" routes to `NO_LLM`/cached greeting.
- "Okay" routes to `NO_LLM`/cached acknowledgement.
- "Can you repeat that?" routes to deterministic replay.
- Simple clarification routes to `FAST_LLM`.
- Complex comparison routes to `SMART_LLM`.
- Post-call summary routes to `ASYNC`.

Phrase tests:

- Valid phrase ID resolves to cached audio.
- Invalid phrase ID is rejected and logged.
- Mixed phrase/text output is parsed correctly.
- Route-specific manifest includes only relevant phrases.
- Initial ack is emitted before the model call starts.

Prompt tests:

- Static prompt prefix remains byte-stable.
- Dynamic route context appears below stable prefix.
- Full phrase DB is not sent every turn.
- Selected tools only are included.

Metrics tests:

- Route, model, provider, tokens, cached tokens, TTFT, and cache hit fields are logged.
- Retry count and interruption are logged.
- Perceived latency uses ack/filler audio start.
- Actual latency uses first answer audio start.

Memory tests:

- Suggestions are generated async.
- Accepted memory is persisted.
- Rejected memory is not used.
- Sensitive inferred memory is not auto-saved.

## Open Questions

- Which exact model names should production v1 use for `FAST_LLM` and `SMART_LLM`?
- Should phrase manifests be stored in SQLite, JSON files, or generated from code constants?
- Should phrase audio be generated per provider/voice at deploy time or lazily on first use?
- Should route decisions be deterministic only, or should a small classifier model handle ambiguous routing?
- What quality score threshold should trigger a route policy review?
