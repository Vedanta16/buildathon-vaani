# Model Pipeline Benchmarks

This folder benchmarks candidate OpenAI and Gemini models for the end-to-end voice pipeline:

- Chat LLM routing: fast, smart, tool/search, and prompt-cache behavior.
- ASR: batch transcription accuracy/latency baselines.
- TTS: time to first audio byte, sentence-level streaming latency, and total synthesis latency.

The first benchmark runner focuses on REST-style calls. Realtime/OpenAI Realtime/Gemini Live models are tracked in `model_matrix.json` for planning, but live-turn benchmarks should be added after the REST baselines are stable.

## Streaming Coverage

The benchmark runners now record both `streaming` and `transport_mode` on each result row.

Current behavior:

| Area | OpenAI | Gemini |
| --- | --- | --- |
| Chat LLM | streamed via Chat Completions SSE | streamed via `generate_content_stream` |
| Search/tool chat | streamed via Responses SSE for OpenAI web search | streamed via `generate_content_stream` with Google Search grounding |
| TTS | streamed by reading audio bytes as they arrive | streamed via `generate_content_stream` audio chunks |
| ASR | batch transcription in this REST benchmark | batch audio-understanding in this REST benchmark |

ASR is intentionally not treated as streaming in these REST runners. For live ASR, add a separate benchmark path:

- OpenAI Realtime transcription over WebSocket/WebRTC.
- Gemini Live API for real-time voice/video interaction, or Google Cloud Speech-to-Text for dedicated real-time transcription.

## Setup

Fill in `.env` at the repo root:

```bash
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

Install benchmark dependencies if your environment does not already have them:

```bash
pip install -r benchmarks/model_pipeline/requirements.txt
```

## Dry Run

Print the benchmark matrix without calling APIs:

```bash
python -m benchmarks.model_pipeline.run_benchmarks --dry-run
```

Run only one provider/suite:

```bash
python -m benchmarks.model_pipeline.run_benchmarks --provider openai --suite chat_llm --dry-run
python -m benchmarks.model_pipeline.run_benchmarks --provider gemini --suite tts --dry-run
```

Run a targeted, low-cost plan:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --provider openai \
  --suite chat_llm \
  --models gpt-5.2 \
  --cases plain_chat \
  --dry-run
```

## Real Benchmarks

Run all configured REST benchmarks:

```bash
python -m benchmarks.model_pipeline.run_benchmarks
```

Run the holistic chat-model comparison and print winners:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --suite chat_llm \
  --cases plain_chat,reasoning_eval \
  --runs 1 \
  --max-output-tokens 160 \
  --winners
```

With the default `.env.example`, this runs 20 calls: 6 OpenAI models and 4 Gemini Flash/Lite models, with `plain_chat` plus `reasoning_eval` for each.

Gemini Pro-class models are intentionally excluded from routine benchmarks. Use Flash/Lite families by default for latency, cost, and quota discipline.

Run a backend-aligned finalization benchmark:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite all \
  --runs 1 \
  --max-output-tokens 160 \
  --winners \
  --dry-run
```

Backend-aligned means:

- Chat LLM uses streaming responses and tests `plain_chat` plus `reasoning_eval`.
- TTS uses `tts_sentence_stream`, matching the backend behavior of sending completed sentences to TTS one at a time.
- ASR uses batch fixtures only in this runner. Live ASR is implemented in the backend via OpenAI Realtime/Gemini Live, but it needs a separate live WebSocket benchmark with mic/audio fixtures.

Run the actual backend-aligned benchmark after checking the dry-run call count:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite all \
  --runs 1 \
  --max-output-tokens 160 \
  --winners \
  --asr-audio path/to/sample.wav
```

If you do not have an ASR audio fixture yet, run chat and TTS only:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite chat_llm \
  --runs 1 \
  --max-output-tokens 160 \
  --winners
```

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --backend-aligned \
  --suite tts \
  --runs 1
```

## End-to-End Pipeline Benchmark

Use `run_e2e_pipeline.py` to compare the current baseline approach against the optimized architecture before integrating it into the main app.

Baseline means:

- LLM-generated acknowledgement.
- Full-context prompt for answer generation.
- Smart/default chat model used for answer and post-call work.
- Post-call memory/eval work is still measured as part of the pipeline.

Optimized means:

- Cached prefilled phrase acknowledgement.
- Hybrid task router: deterministic first, small router model only when needed.
- Progressive context: retrieve only relevant facts before calling the answer model.
- Route-specific model: fast model for simple answers, smart model only for reasoning-heavy turns.
- Async post-call memory/eval lane using the cheap async model.
- Optional cached/prerendered TTS for phrase-only responses.

Dry-run the default low-cost OpenAI text pipeline:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline --dry-run
```

Default dry-run call count is 5 API calls for one `billing_fast` scenario:

- Baseline: 3 calls (`ack`, `answer`, `post_call`).
- Optimized: 2 calls (`answer`, `post_call`).

Run the default OpenAI before/after benchmark:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline
```

Run all scenarios for both providers:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline \
  --provider all \
  --scenario all
```

Force the small router model on every optimized scenario to benchmark routing latency separately:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline \
  --scenario all \
  --router-mode llm
```

Dry-run all scenarios first to see spend impact:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline \
  --provider all \
  --scenario all \
  --dry-run
```

With default settings this plans 36 API calls across OpenAI and Gemini because no-LLM scenarios still run the async post-call extraction step.

Run a cheaper no-post-call smoke test:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline \
  --scenario greeting \
  --no-post-call
```

Include TTS and/or ASR only when explicitly needed:

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline \
  --include-tts
```

```bash
python -m benchmarks.model_pipeline.run_e2e_pipeline \
  --asr-audio path/to/sample.wav
```

End-to-end results are written as JSONL under `benchmarks/model_pipeline/results/` with one row per pipeline step.

Run chat only:

```bash
python -m benchmarks.model_pipeline.run_benchmarks --suite chat_llm
```

Run one OpenAI model/case only:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --provider openai \
  --suite chat_llm \
  --models gpt-5.2 \
  --cases plain_chat \
  --runs 3 \
  --max-output-tokens 96
```

Run only cache behavior for two models:

```bash
python -m benchmarks.model_pipeline.run_benchmarks \
  --provider openai \
  --suite chat_llm \
  --models gpt-5-mini,gpt-5.2 \
  --cases prompt_cache_repeat \
  --runs 2 \
  --max-output-tokens 96
```

Run TTS only:

```bash
python -m benchmarks.model_pipeline.run_benchmarks --suite tts
```

Run ASR with a real audio fixture:

```bash
python -m benchmarks.model_pipeline.run_benchmarks --suite asr --asr-audio path/to/sample.wav
```

Results are written as JSONL under `benchmarks/model_pipeline/results/`, and generated audio artifacts are written under `benchmarks/model_pipeline/audio_out/`. Both directories are gitignored.

Each run writes a timestamped file such as:

```text
benchmarks/model_pipeline/results/benchmark_1779518509.jsonl
```

Each line is one benchmark result row, so partial results are preserved even if a later model fails.

## Metrics Captured

Each row records:

- provider
- suite
- model
- benchmark case
- status/error
- total latency
- TTFT or TTFB where available
- input tokens
- output tokens
- cached tokens
- output characters or bytes
- artifact path when audio is generated
- streaming
- transport mode

End-to-end pipeline rows additionally record:

- scenario
- variant: `baseline` or `optimized`
- step: `text_input`, `asr`, `ack`, `route`, `retrieval`, `answer`, `tts`, `post_call`
- route: `NO_LLM`, `CACHE`, `FAST_LLM`, `SMART_LLM`, `ASYNC`, etc.
- whether the step made an API call
- whether the step is user-visible
- whether the step streamed
- transport mode
- prompt character count
- per-step optimization metadata

## Benchmark Cases

Chat:

- `plain_chat`: streaming response latency and TTFT.
- `prompt_cache_repeat`: same stable prefix path as `plain_chat`; run multiple times to observe cached-token behavior.
- `web_search_tool` / `google_search_grounding`: provider-native search/tool path.

TTS:

- `tts_ttfb`: first audio byte and full synthesis time.
- `tts_sentence_stream`: calls streaming TTS once per completed sentence, matching the backend sentence-level flush behavior.

ASR:

- `batch_transcribe`: transcription latency from a provided audio fixture.

## Notes

- API availability depends on provider account, region, billing, and model rollout.
- Preview model aliases can change; benchmark output records the exact model string used.
- ASR benchmarks require a real audio file. The runner skips ASR if `--asr-audio` is not provided.
- Prompt-cache benefits require repeated calls with stable prefixes and may not show on the first run.
- Use `--models`, `--cases`, `--limit`, and `--runs` to control benchmark cost.
- Use `--max-output-tokens` for chat/search tests; generated tokens dominate latency.
- Use `--winners` to print per-provider winners for latency, token efficiency, estimated cost, and reasoning proxy.
