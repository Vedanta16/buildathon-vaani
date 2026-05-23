# Model Benchmark Learnings

## Context

We benchmarked OpenAI and Gemini candidates for the live voice-agent pipeline. The goal was not to pick the "best model" globally, but to choose models by route:

- live fast response
- cheap/simple async work
- reasoning-heavy work
- Gemini fallback/alternate path

The benchmark runner stores raw JSONL results under `benchmarks/model_pipeline/results/`. That directory is gitignored, so this document captures the durable learnings.

## Commands Used

OpenAI targeted runs:

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --provider openai \
  --suite chat_llm \
  --models gpt-5.2 \
  --cases plain_chat \
  --runs 3 \
  --max-output-tokens 96
```

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --provider openai \
  --suite chat_llm \
  --models gpt-5-mini \
  --cases plain_chat \
  --runs 3 \
  --max-output-tokens 96
```

Holistic chat comparison:

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --suite chat_llm \
  --cases plain_chat,reasoning_eval \
  --runs 1 \
  --max-output-tokens 160 \
  --winners
```

Latest raw result file:

```text
benchmarks/model_pipeline/results/benchmark_1779518793.jsonl
```

## Key Findings

### OpenAI

`gpt-5.2` was viable as a smart live model when output was capped:

- Median total latency: 1216ms.
- p95 total latency: 1600ms.
- Median TTFT: 779ms.
- p95 TTFT: 1163ms.

`gpt-5-mini` and `gpt-5-nano` were not viable under the tested chat-completions settings:

- Earlier uncapped runs produced very high TTFT and huge output-token counts.
- Capped runs consumed the full output-token budget without visible output.
- Treat these as invalid for live visible responses until a different API/settings path proves otherwise.

The latest holistic run produced these OpenAI winners:

| Category | Winner | Notes |
| --- | --- | --- |
| Latency | `gpt-4o-mini` | `plain_chat` latency 1091ms, TTFT 667ms. |
| Token efficiency | `gpt-4o-mini` | Best overall visible-output token profile in winner analysis. |
| Cost efficiency | `gpt-4.1-nano` | Lowest estimated cost among valid visible-output OpenAI rows. |
| Reasoning proxy | `gpt-4o-mini` | Scored 1.0 on the lightweight latest-fact reasoning case. |

### Gemini

Gemini Flash/Lite candidates were viable but slower than the best OpenAI candidates in this run:

| Category | Winner | Notes |
| --- | --- | --- |
| Latency | `gemini-2.5-flash` | `plain_chat` latency 2221ms, TTFT 2079ms. |
| Token efficiency | `gemini-3.5-flash` | Lowest token count among valid Gemini rows. |
| Cost efficiency | `gemini-2.5-flash-lite` | Lowest estimated cost, but high `plain_chat` latency. |
| Reasoning proxy | `gemini-2.5-flash` | Scored 0.9 on the lightweight reasoning case. |

`gemini-2.5-flash-lite/reasoning_eval` failed with a transient `503 Service Unavailable`, so it should be retried before judging reasoning quality.

Gemini Pro-class models should not be used in routine benchmarks:

- `gemini-2.5-pro` hit free-tier quota errors.
- `gemini-3-pro-preview` hit free-tier quota errors and appeared to map to a Pro-class quota bucket.
- We removed Pro-class Gemini models from default benchmark lists.

## Current Routing Recommendation

Use these as current defaults until we run repeated benchmarks:

| Route | Recommended model |
| --- | --- |
| `NO_LLM` / `CACHE` | No model. Use deterministic handlers and phrase IDs. |
| OpenAI live fast lane | `gpt-4o-mini` |
| OpenAI cheap/simple async lane | `gpt-4.1-nano` |
| OpenAI smart lane | `gpt-5.2` |
| Gemini live lane | `gemini-2.5-flash` |
| Gemini cheap/async lane | `gemini-2.5-flash-lite` |
| Gemini candidate to keep testing | `gemini-3.5-flash` |

Operationally:

- Use cached acknowledgements before any model work.
- Do not use LLMs for greetings, acknowledgements, confirmations, or simple repeats.
- Do not use `gpt-5-mini` or `gpt-5-nano` for live visible responses based on current results.
- Keep Gemini Flash/Lite only; avoid Gemini Pro-class models for this product path unless quota and latency goals change.

## End-to-End Proof Runner

We added a separate before/after pipeline benchmark:

```bash
python3 -m benchmarks.model_pipeline.run_e2e_pipeline
```

The runner does not integrate with the main application. It exists to produce step-level proof before we change production code.

Baseline path:

- Generate acknowledgement with the baseline chat model.
- Send full KB and full phrase catalogue to the answer model.
- Use the baseline smart chat model for answer and post-call extraction.
- Treat optimizations as absent.

Optimized path:

- Use prefilled phrase acknowledgement with no LLM call.
- Route by task, not model tier.
- Use deterministic routing first; use a small router model only when the deterministic router is not confident.
- Select only relevant facts before the answer prompt.
- Use `gpt-4o-mini` / `gemini-2.5-flash` for fast answers.
- Use `gpt-5.2` / `gemini-2.5-flash` for smart reasoning turns.
- Use `gpt-4.1-nano` / `gemini-2.5-flash-lite` for async post-call memory/eval.
- Use cached/prerendered TTS for phrase-only responses when TTS is enabled.

Default dry-run:

```bash
python3 -m benchmarks.model_pipeline.run_e2e_pipeline --dry-run
```

Current default planned cost shape:

| Scope | Planned calls |
| --- | ---: |
| OpenAI `billing_fast` default | 5 |
| OpenAI + Gemini, all scenarios | 36 |

The output JSONL stores one row per step so the right-panel observability design can be validated directly:

- input tokens
- output tokens
- cached tokens
- TTFT
- route
- model
- streaming
- transport mode
- API-call flag
- user-visible flag
- estimated chat cost
- prompt size
- optimization metadata

### End-to-End Run: `e2e_pipeline_1779519800.jsonl`

Command:

```bash
python3 -m benchmarks.model_pipeline.run_e2e_pipeline \
  --provider all \
  --scenario all
```

OpenAI produced clean before/after direction across all text scenarios:

| Scenario | Visible latency delta | API call delta | Cost delta |
| --- | ---: | ---: | ---: |
| `billing_fast` | -491ms | -1 | -$0.00260755 |
| `coverage_smart` | -1251ms | -1 | -$0.00228015 |
| `greeting` | -2383ms | -2 | -$0.00182910 |
| `memory_signal` | -2491ms | -2 | -$0.00326095 |

Interpretation:

- The biggest OpenAI win comes from avoiding `gpt-5.2` for simple/greeting/memory acknowledgement work.
- Cached deterministic acknowledgement makes perceived latency `0ms` for `NO_LLM` routes.
- Smart routing still allows `gpt-5.2` for `coverage_smart`, but removes the LLM acknowledgement and uses compact progressive context.

Gemini produced directionally useful results but the run is not fully clean:

| Scenario | Visible latency delta | API call delta | Cost delta | Status |
| --- | ---: | ---: | ---: | --- |
| `billing_fast` | -119ms | -1 | -$0.00001500 | usable |
| `coverage_smart` | -619ms | -1 | -$0.00000050 | usable |
| `greeting` | -3040ms | -2 | -$0.00007360 | usable |
| `memory_signal` | invalid | invalid | invalid | optimized post-call hit Gemini `503 Service Unavailable` |

Gemini caveats from this run:

- `gemini-2.5-flash-lite` can be slow or temporarily unavailable for async post-call work.
- Several Gemini outputs were too short or formatting-skewed under the current token cap, including code-fence-like fragments. The runner now enables Gemini JSON mode for structured calls while keeping acknowledgement calls as plain text.
- Failed variants must not be treated as zero-cost wins. The runner now preserves partial successful steps and prints `status=error` plus the first error in the summary.

## Streaming Coverage Learning

Current benchmark streaming behavior:

| Pipeline area | OpenAI | Gemini |
| --- | --- | --- |
| Chat LLM | Streaming via Chat Completions SSE. | Streaming via `generate_content_stream`. |
| Tool/search chat | Streaming via Responses SSE for web search. | Streaming via `generate_content_stream` with Google Search grounding. |
| TTS | Streaming audio bytes as they arrive. | Streaming audio chunks via `generate_content_stream`. |
| ASR | Batch transcription in current REST runner. | Batch audio-understanding in current REST runner. |

ASR needs a separate live benchmark path. Do not label current ASR rows as streaming:

- OpenAI live ASR should use Realtime transcription.
- Gemini live voice should use Live API for interactive voice/video, or Google Cloud Speech-to-Text if the requirement is dedicated real-time transcription.

### Live-Turn Streaming Methodology Run

Commands:

```bash
python3 -m benchmarks.model_pipeline.run_e2e_pipeline \
  --provider openai \
  --scenario all \
  --no-post-call \
  --max-output-tokens 160
```

```bash
python3 -m benchmarks.model_pipeline.run_e2e_pipeline \
  --provider gemini \
  --scenario all \
  --no-post-call \
  --max-output-tokens 160 \
  --baseline-chat-model gemini-2.5-flash
```

Result files:

- `benchmarks/model_pipeline/results/e2e_pipeline_1779521265.jsonl`
- `benchmarks/model_pipeline/results/e2e_pipeline_1779521290.jsonl`

OpenAI live-turn results:

| Scenario | Baseline visible latency | Optimized visible latency | Delta | API call delta | Cost delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| `billing_fast` | 2252ms | 1329ms | -923ms | -1 | -$0.00135265 |
| `coverage_smart` | 2558ms | 1568ms | -990ms | -1 | +$0.00027300 |
| `greeting` | 2727ms | 0ms | -2727ms | -2 | -$0.00098175 |
| `memory_signal` | 2078ms | 0ms | -2078ms | -2 | -$0.00146300 |

Gemini live-turn results:

| Scenario | Baseline visible latency | Optimized visible latency | Delta | API call delta | Cost delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| `billing_fast` | 3311ms | 1897ms | -1414ms | -1 | -$0.00003950 |
| `coverage_smart` | 3429ms | 2180ms | -1249ms | -1 | -$0.00002990 |
| `greeting` | 2936ms | 0ms | -2936ms | -2 | -$0.00009730 |
| `memory_signal` | 3278ms | 0ms | -3278ms | -2 | -$0.00008900 |

Methodology notes:

- `--no-post-call` isolates live user-perceived turn latency from async memory/eval work.
- All model-backed live-turn calls reported `streaming_api_calls` and `batch_api_calls=0`.
- Gemini baseline was forced to `gemini-2.5-flash` to avoid the `gemini-3.5-flash` free-tier quota bucket seen earlier.
- OpenAI `coverage_smart` became faster but slightly more expensive because optimized correctly routed the answer to the smart model; this is acceptable for the smart lane but should be watched with route-specific budgets.

### Backend-Aligned Model Finalization Run

The model benchmark runner now has `--backend-aligned` to match current backend behavior:

- Chat LLM: streaming `plain_chat` and `reasoning_eval`.
- TTS: `tts_sentence_stream`, which calls streaming TTS once per completed sentence.
- ASR: batch fixture transcription only; live ASR is implemented in backend providers but needs a separate WebSocket/audio fixture benchmark.

Chat command:

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite chat_llm \
  --runs 1 \
  --max-output-tokens 160 \
  --winners
```

Chat result file:

- `benchmarks/model_pipeline/results/benchmark_1779521553.jsonl`

Chat results:

| Provider | Model | Median latency | Median TTFT | Status |
| --- | --- | ---: | ---: | --- |
| OpenAI | `gpt-4.1-nano` | 878ms | 682ms | ok |
| OpenAI | `gpt-4.1-mini` | 1155ms | 667ms | ok |
| OpenAI | `gpt-4o-mini` | 1284ms | 786ms | ok |
| OpenAI | `gpt-5.2` | 1207ms | 846ms | ok |
| OpenAI | `gpt-5-nano` | 2072ms | 2072ms | ok, slow |
| OpenAI | `gpt-5-mini` | 2243ms | 2243ms | ok, slow |
| Gemini | `gemini-2.5-flash` | 2124ms | 2124ms | ok |
| Gemini | `gemini-3-flash-preview` | 2030ms | 2028ms | ok |
| Gemini | `gemini-3.5-flash` | 2268ms | 2244ms | ok |
| Gemini | `gemini-2.5-flash-lite` | unavailable | unavailable | failed with provider `503` |

Winner output from this run:

| Provider | Category | Winner |
| --- | --- | --- |
| OpenAI | latency | `gpt-4.1-mini` |
| OpenAI | token efficiency | `gpt-4o-mini` |
| OpenAI | cost efficiency | `gpt-4.1-nano` |
| OpenAI | reasoning proxy | `gpt-4.1-mini` |
| Gemini | latency | `gemini-2.5-flash` |
| Gemini | token efficiency | `gemini-3.5-flash` |
| Gemini | cost efficiency | `gemini-3.5-flash` |
| Gemini | reasoning proxy | `gemini-3-flash-preview` |

Recommended chat routing after this run:

- `NO_LLM` / `CACHE`: no model.
- OpenAI cheapest classifier/async lane: `gpt-4.1-nano`.
- OpenAI fast live answer lane: `gpt-4.1-mini` for lowest latency, or `gpt-4o-mini` when token/cost efficiency matters more.
- OpenAI smart live lane: keep `gpt-5.2` for reasoning-heavy routes, but it is not the latency winner.
- Gemini live fallback lane: `gemini-2.5-flash`.
- Do not use `gemini-2.5-flash-lite` for live chat until it stops returning provider `503` in repeated runs.

TTS command:

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite tts \
  --runs 1
```

TTS result file:

- `benchmarks/model_pipeline/results/benchmark_1779521649.jsonl`

TTS results for sentence-level streaming:

| Provider | Model | First sentence audio | Total 3-sentence latency | Status |
| --- | --- | ---: | ---: | --- |
| OpenAI | `gpt-4o-mini-tts` | 943ms | 5080ms | ok |
| OpenAI | `tts-1` | 2517ms | 6244ms | ok |
| OpenAI | `tts-1-hd` | 1782ms | 8228ms | ok |
| Gemini | `gemini-2.5-flash-preview-tts` | 3628ms | 10847ms | ok |

Recommended TTS routing after this run:

- Default live TTS: `gpt-4o-mini-tts`.
- Avoid `tts-1` as default despite being nominally low-latency; it was slower to first audio in this backend-aligned sentence test.
- Keep Gemini TTS as fallback/quality comparison only until it improves first-audio latency.

ASR finalization still needs a real audio fixture:

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite asr \
  --asr-audio path/to/sample.wav \
  --runs 1
```

Without `--asr-audio`, the ASR benchmark correctly dry-runs six cases but skips actual provider calls.

ASR command run with `backend/harvard.wav`:

```bash
python3 -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite asr \
  --asr-audio backend/harvard.wav \
  --runs 1
```

ASR result file:

- `benchmarks/model_pipeline/results/benchmark_1779521816.jsonl`

ASR results:

| Provider | Model | Latency | Streaming | Status |
| --- | --- | ---: | --- | --- |
| OpenAI | `gpt-4o-transcribe` | 1809ms | no, batch fixture | ok |
| OpenAI | `gpt-4o-mini-transcribe` | 2181ms | no, batch fixture | ok |
| OpenAI | `whisper-1` | 3621ms | no, batch fixture | ok |
| OpenAI | `gpt-4o-transcribe-diarize` | 6727ms | no, batch fixture | ok |
| Gemini | `gemini-2.5-flash` | 6251ms | no, batch fixture | ok |
| Gemini | `gemini-2.5-flash-lite` | 13044ms | no, batch fixture | ok |

Recommended ASR routing after this fixture run:

- Batch transcription winner: `gpt-4o-transcribe`.
- Cheap/fast expected OpenAI realtime default remains `gpt-4o-mini-transcribe`, but the batch fixture run was slower than `gpt-4o-transcribe`; run a live Realtime ASR benchmark before finalizing live ASR default.
- Avoid Gemini batch ASR for latency-sensitive transcription on this fixture.
- Keep `gpt-4o-transcribe-diarize` only when diarization is required; it is much slower.

## Benchmark Design Learnings

Generated tokens dominate latency. The first OpenAI nano/mini runs looked slow mostly because the models emitted large completion-token counts. The benchmark runner now supports:

- `--models`
- `--cases`
- `--limit`
- `--runs`
- `--max-output-tokens`
- `--winners`

Prompt-cache behavior is working in OpenAI runs; cached token counts appeared in raw results. However, prompt caching alone does not fix poor latency if output generation or hidden token usage is large.

Winner analysis ignores rows with no visible output. This matters for models that spend the output budget on hidden tokens.

## Remaining Benchmark Work

Before final production defaults:

- Re-run holistic chat comparison with `--runs 3`.
- Add phrase-ID compliance scoring.
- Add stricter structured-output validation.
- Benchmark TTS separately for TTFB and quality.
- Benchmark ASR separately with a short real audio fixture.
- Run the end-to-end proof runner across all scenarios with `--runs 3`.
- Consider testing OpenAI Responses API for GPT-5-family models if Chat Completions keeps producing hidden-token/no-visible-output behavior.
