from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_MATRIX = BENCH_DIR / "model_matrix.json"
RESULTS_DIR = BENCH_DIR / "results"
AUDIO_DIR = BENCH_DIR / "audio_out"

CHAT_STATIC_PREFIX = (
    "You are benchmarking a production voice-agent pipeline. "
    "Return only compact JSON. Prefer approved phrase IDs when they fit. "
    "Never include reasoning, markdown, explanations, or extra keys. "
    "Keep static instructions byte-stable for prompt-cache tests. "
    "Approved phrases: A1=Got it, let me check that. "
    "A2=One moment, I am pulling that up. C1=Can you clarify that? "
    "S1=Anything else I can help with? "
    * 24
)

CHAT_USER_PROMPT = (
    "User says: 'Can you check whether roadside assistance is still included, "
    "then explain the answer in one sentence?' "
    "Return exactly this schema: {\"segments\":[{\"p\":\"A1\"},{\"t\":\"...\"}]}. "
    "The text segment must be under 18 words."
)

SEARCH_PROMPT = "Use web/search grounding only if needed: what is today's date?"
TTS_TEXT = "Got it, let me check that."
TTS_SENTENCES = [
    "Got it, let me check that.",
    "Your next premium is due on June fifth.",
    "Roadside assistance is not active right now.",
]
ASR_PROMPT = (
    "Transcribe this audio exactly. Return only the transcript text. "
    "If the audio is silent or unintelligible, say UNINTELLIGIBLE."
)
REASONING_PROMPT = (
    "You are testing route reasoning for an insurance voice agent. "
    "Facts: In March the user added roadside coverage. In April the user removed roadside coverage. "
    "Today the user asks whether roadside coverage is active. "
    "Return compact JSON only: {\"answer\":\"yes|no\",\"route\":\"FAST_LLM|SMART_LLM\",\"why\":\"...\"}. "
    "Correct answer is based on the latest fact."
)

PRICING_PER_MILLION = {
    "openai": {
        "gpt-4.1-nano": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
        "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
        "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60},
        "gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.40},
        "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
        "gpt-5.2": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    },
    "gemini": {
        "gemini-2.5-flash-lite": {"input": 0.10, "cached_input": 0.01, "output": 0.40},
        "gemini-2.5-flash": {"input": 0.30, "cached_input": 0.03, "output": 2.50},
        "gemini-3.5-flash": {"input": 0.25, "cached_input": 0.025, "output": 1.50},
        "gemini-3-flash-preview": {"input": 0.25, "cached_input": 0.025, "output": 1.50},
    },
}


@dataclass
class BenchResult:
    provider: str
    suite: str
    model: str
    case: str
    status: str
    latency_ms: int | None = None
    ttft_ms: int | None = None
    streaming: bool = False
    transport_mode: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    output_chars: int | None = None
    output_bytes: int | None = None
    sentence_count: int | None = None
    first_sentence_audio_ms: int | None = None
    estimated_cost_usd: float | None = None
    reasoning_score: float | None = None
    artifact_path: str | None = None
    error: str | None = None
    meta: dict[str, Any] | None = None


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def load_matrix(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def env_list(name: str) -> list[str] | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def csv_arg(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    values = {item.strip() for item in raw.split(",") if item.strip()}
    return values or None


def selected_models(matrix: dict[str, Any], provider: str, suite: str) -> list[dict[str, Any]]:
    defaults = matrix["providers"][provider][suite]
    override_name = f"BENCHMARK_{provider.upper()}_{suite.upper()}_MODELS"
    override = env_list(override_name)
    if not override:
        return defaults
    by_name = {entry["model"]: entry for entry in defaults}
    return [by_name.get(model, {"model": model, "benchmark_cases": []}) for model in override]


def filter_models(entries: list[dict[str, Any]], allowed: set[str] | None) -> list[dict[str, Any]]:
    if not allowed:
        return entries
    return [entry for entry in entries if entry["model"] in allowed]


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


def usage_value(obj: Any, *names: str) -> int | None:
    cur = obj
    for name in names:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(name)
        else:
            cur = getattr(cur, name, None)
    return cur if isinstance(cur, int) else None


def write_jsonl(path: Path, results: list[BenchResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for result in results:
            fh.write(json.dumps(asdict(result), sort_keys=True) + "\n")


def write_wav(path: Path, pcm: bytes, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def estimate_cost_usd(result: BenchResult) -> float | None:
    pricing = PRICING_PER_MILLION.get(result.provider, {}).get(result.model)
    if not pricing:
        return None
    input_tokens = result.input_tokens or 0
    cached_tokens = min(result.cached_tokens or 0, input_tokens)
    uncached_tokens = max(0, input_tokens - cached_tokens)
    output_tokens = result.output_tokens or 0
    cost = (
        uncached_tokens * pricing["input"]
        + cached_tokens * pricing["cached_input"]
        + output_tokens * pricing["output"]
    ) / 1_000_000
    return round(cost, 8)


def reasoning_score(text: str) -> float:
    normalized = text.lower().replace(" ", "")
    score = 0.0
    if '"answer":"no"' in normalized or "'answer':'no'" in normalized:
        score += 0.7
    elif "no" in normalized and "yes" not in normalized:
        score += 0.4
    if "april" in normalized or "latest" in normalized or "removed" in normalized:
        score += 0.2
    if "smart_llm" in normalized:
        score += 0.1
    return min(1.0, score)


def finalize_result(result: BenchResult) -> BenchResult:
    result.estimated_cost_usd = estimate_cost_usd(result)
    return result


def metric_summary(values: list[int]) -> str:
    if not values:
        return "-"
    ordered = sorted(values)
    median = round(statistics.median(ordered))
    p95_index = min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))
    p95 = ordered[p95_index]
    return f"median={median} p95={p95}"


def print_summary(results: list[BenchResult]) -> None:
    completed = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status not in {"ok", "skipped"}]
    skipped = [r for r in results if r.status == "skipped"]
    print(f"completed={len(completed)} failed={len(failed)} skipped={len(skipped)}")

    groups: dict[tuple[str, str, str], list[BenchResult]] = {}
    for result in completed:
        groups.setdefault((result.provider, result.suite, result.model), []).append(result)
    for key, rows in sorted(groups.items()):
        latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
        ttfts = [r.ttft_ms for r in rows if r.ttft_ms is not None]
        streaming = sum(1 for r in rows if r.streaming)
        batch = sum(1 for r in rows if not r.streaming)
        print(
            f"{key[0]}/{key[1]}/{key[2]} "
            f"latency_ms({metric_summary(latencies)}) "
            f"ttft_ms({metric_summary(ttfts)}) "
            f"streaming={streaming} batch={batch}"
        )

    for result in failed[:10]:
        print(f"FAILED {result.provider}/{result.suite}/{result.model}/{result.case}: {result.error}")


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def print_winners(results: list[BenchResult]) -> None:
    rows = [
        r for r in results
        if r.status == "ok"
        and r.suite == "chat_llm"
        and not (r.meta or {}).get("visible_output") is False
    ]
    if not rows:
        print("No valid visible-output chat rows available for winner analysis.")
        return

    print("\nWINNERS")
    for provider in sorted({r.provider for r in rows}):
        provider_rows = [r for r in rows if r.provider == provider]
        by_model: dict[str, list[BenchResult]] = {}
        for row in provider_rows:
            by_model.setdefault(row.model, []).append(row)

        summaries = []
        for model, model_rows in by_model.items():
            latency_rows = [r for r in model_rows if r.case == "plain_chat"]
            reasoning_rows = [r for r in model_rows if r.case == "reasoning_eval"]
            cost_values = [r.estimated_cost_usd for r in model_rows if r.estimated_cost_usd is not None]
            token_values = [
                (r.input_tokens or 0) + (r.output_tokens or 0)
                for r in model_rows
                if r.input_tokens is not None or r.output_tokens is not None
            ]
            summaries.append({
                "model": model,
                "latency_ms": _median([r.latency_ms for r in latency_rows if r.latency_ms is not None]),
                "ttft_ms": _median([r.ttft_ms for r in latency_rows if r.ttft_ms is not None]),
                "tokens": _median([float(v) for v in token_values]),
                "cost": _median([float(v) for v in cost_values]),
                "reasoning": _median([r.reasoning_score for r in reasoning_rows if r.reasoning_score is not None]),
            })

        def best_min(key: str) -> dict[str, Any] | None:
            candidates = [s for s in summaries if s[key] is not None]
            return min(candidates, key=lambda s: s[key]) if candidates else None

        def best_max(key: str) -> dict[str, Any] | None:
            candidates = [s for s in summaries if s[key] is not None]
            return max(candidates, key=lambda s: (s[key], -(s["latency_ms"] or 10**9))) if candidates else None

        print(f"\n{provider}")
        for label, key, fn in [
            ("latency", "latency_ms", best_min),
            ("token_efficiency", "tokens", best_min),
            ("cost_efficiency", "cost", best_min),
            ("reasoning", "reasoning", best_max),
        ]:
            winner = fn(key)
            if not winner:
                print(f"  {label}: unavailable")
                continue
            metric = winner[key]
            print(
                f"  {label}: {winner['model']} "
                f"({key}={metric}, ttft_ms={winner.get('ttft_ms')}, cost=${winner.get('cost')})"
            )


async def bench_openai_chat(model: str, case: str, max_output_tokens: int) -> BenchResult:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if case == "web_search_tool":
        if not hasattr(client, "responses"):
            return BenchResult("openai", "chat_llm", model, case, "skipped", error="OpenAI SDK lacks Responses API")
        start = now_ms()
        first_token_ms: int | None = None
        text = ""
        completed_response = None
        stream = await client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=SEARCH_PROMPT,
            max_output_tokens=max_output_tokens,
            stream=True,
        )
        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta and first_token_ms is None:
                    first_token_ms = now_ms() - start
                text += delta
            elif event_type == "response.completed":
                completed_response = getattr(event, "response", None)
        latency = now_ms() - start
        usage = getattr(completed_response, "usage", None)
        return finalize_result(BenchResult(
            "openai",
            "chat_llm",
            model,
            case,
            "ok",
            latency_ms=latency,
            ttft_ms=first_token_ms,
            streaming=True,
            transport_mode="sse_responses_web_search",
            input_tokens=usage_value(usage, "input_tokens") or usage_value(usage, "prompt_tokens"),
            output_tokens=usage_value(usage, "output_tokens") or usage_value(usage, "completion_tokens"),
            cached_tokens=usage_value(usage, "input_tokens_details", "cached_tokens")
            or usage_value(usage, "prompt_tokens_details", "cached_tokens"),
            output_chars=len(text),
            meta={"visible_output": bool(text.strip())},
        ))

    if case == "reasoning_eval":
        messages = [
            {"role": "system", "content": "Return compact JSON only."},
            {"role": "user", "content": REASONING_PROMPT},
        ]
    else:
        messages = [
            {"role": "system", "content": CHAT_STATIC_PREFIX},
            {"role": "user", "content": CHAT_USER_PROMPT},
        ]
    start = now_ms()
    first_token_ms: int | None = None
    text = ""
    usage = None
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        max_completion_tokens=max_output_tokens,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            if first_token_ms is None:
                first_token_ms = now_ms() - start
            text += chunk.choices[0].delta.content
        if getattr(chunk, "usage", None):
            usage = chunk.usage
    latency = now_ms() - start
    if first_token_ms is None and usage_value(usage, "completion_tokens"):
        first_token_ms = latency
    return finalize_result(BenchResult(
        "openai",
        "chat_llm",
        model,
        case,
        "ok",
        latency_ms=latency,
        ttft_ms=first_token_ms,
        streaming=True,
        transport_mode="sse_chat_completions",
        input_tokens=usage_value(usage, "prompt_tokens"),
        output_tokens=usage_value(usage, "completion_tokens"),
        cached_tokens=usage_value(usage, "prompt_tokens_details", "cached_tokens"),
        output_chars=len(text),
        reasoning_score=reasoning_score(text) if case == "reasoning_eval" else None,
        meta={"visible_output": bool(text.strip())},
    ))


async def _bench_openai_tts_once(client: Any, model: str, text: str) -> tuple[int, int | None, bytes]:
    start = now_ms()
    first_byte_ms: int | None = None
    chunks: list[bytes] = []
    async with client.audio.speech.with_streaming_response.create(
        model=model,
        voice="coral",
        input=text,
        response_format="wav",
    ) as response:
        async for chunk in response.iter_bytes(chunk_size=4096):
            if chunk and first_byte_ms is None:
                first_byte_ms = now_ms() - start
            chunks.append(chunk)
    return now_ms() - start, first_byte_ms, b"".join(chunks)


async def bench_openai_tts(model: str, case: str) -> BenchResult:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    start = now_ms()
    first_byte_ms: int | None = None
    first_sentence_audio_ms: int | None = None
    chunks: list[bytes] = []
    texts = TTS_SENTENCES if case == "tts_sentence_stream" else [TTS_TEXT]
    for index, text in enumerate(texts):
        _, sentence_first_byte_ms, audio = await _bench_openai_tts_once(client, model, text)
        if index == 0:
            first_byte_ms = sentence_first_byte_ms
            first_sentence_audio_ms = sentence_first_byte_ms
        chunks.append(audio)
    latency = now_ms() - start
    audio = b"".join(chunks)
    out = AUDIO_DIR / f"openai_tts_{model}_{case}_{int(time.time())}.wav"
    out.write_bytes(audio)
    return BenchResult(
        "openai",
        "tts",
        model,
        case,
        "ok",
        latency_ms=latency,
        ttft_ms=first_byte_ms,
        streaming=True,
        transport_mode="http_audio_byte_stream",
        output_bytes=len(audio),
        sentence_count=len(texts),
        first_sentence_audio_ms=first_sentence_audio_ms,
        artifact_path=str(out.relative_to(ROOT)),
    )


async def bench_openai_asr(model: str, case: str, audio_path: Path | None) -> BenchResult:
    from openai import AsyncOpenAI

    if not audio_path or not audio_path.exists():
        return BenchResult("openai", "asr", model, case, "skipped", error="No ASR audio path provided")
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    start = now_ms()
    with audio_path.open("rb") as audio:
        response = await client.audio.transcriptions.create(model=model, file=audio)
    latency = now_ms() - start
    text = getattr(response, "text", "") or str(response)
    return BenchResult(
        "openai",
        "asr",
        model,
        case,
        "ok",
        latency_ms=latency,
        streaming=False,
        transport_mode="batch_transcription",
        output_chars=len(text),
        artifact_path=str(audio_path),
    )


async def bench_gemini_chat(model: str, case: str, max_output_tokens: int) -> BenchResult:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    tools = None
    prompt = f"{CHAT_STATIC_PREFIX}\n\n{CHAT_USER_PROMPT}"
    if case == "google_search_grounding":
        tools = [types.Tool(google_search=types.GoogleSearch())]
        prompt = SEARCH_PROMPT
    elif case == "reasoning_eval":
        prompt = REASONING_PROMPT
    config = types.GenerateContentConfig(
        tools=tools,
        max_output_tokens=max_output_tokens,
    )

    start = now_ms()
    first_token_ms: int | None = None
    text = ""
    usage = None
    async for chunk in await client.aio.models.generate_content_stream(
        model=model,
        contents=prompt,
        config=config,
    ):
        token = chunk.text or ""
        if token and first_token_ms is None:
            first_token_ms = now_ms() - start
        text += token
        if getattr(chunk, "usage_metadata", None):
            usage = chunk.usage_metadata
    latency = now_ms() - start
    return finalize_result(BenchResult(
        "gemini",
        "chat_llm",
        model,
        case,
        "ok",
        latency_ms=latency,
        ttft_ms=first_token_ms,
        streaming=True,
        transport_mode="sse_generate_content_stream",
        input_tokens=usage_value(usage, "prompt_token_count"),
        output_tokens=usage_value(usage, "candidates_token_count"),
        cached_tokens=usage_value(usage, "cached_content_token_count"),
        output_chars=len(text),
        reasoning_score=reasoning_score(text) if case == "reasoning_eval" else None,
        meta={"visible_output": bool(text.strip())},
    ))


async def _bench_gemini_tts_once(client: Any, model: str, text: str) -> tuple[int, int | None, bytes, int]:
    from google.genai import types

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
            )
        ),
    )
    start = now_ms()
    first_byte_ms: int | None = None
    audio_chunks: list[bytes] = []
    sample_rate = 24000
    async for chunk in await client.aio.models.generate_content_stream(
        model=model,
        contents=text,
        config=config,
    ):
        parts = getattr(chunk, "parts", None) or []
        if not parts and getattr(chunk, "candidates", None):
            parts = getattr(chunk.candidates[0].content, "parts", []) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if not data:
                continue
            if isinstance(data, str):
                import base64

                data = base64.b64decode(data)
            if first_byte_ms is None:
                first_byte_ms = now_ms() - start
            audio_chunks.append(bytes(data))
            mime_type = getattr(inline, "mime_type", "") or ""
            if "rate=" in mime_type:
                try:
                    sample_rate = int(mime_type.split("rate=", 1)[1].split(";", 1)[0])
                except ValueError:
                    pass
    return now_ms() - start, first_byte_ms, b"".join(audio_chunks), sample_rate


async def bench_gemini_tts(model: str, case: str) -> BenchResult:
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    start = now_ms()
    first_byte_ms: int | None = None
    first_sentence_audio_ms: int | None = None
    audio_chunks: list[bytes] = []
    sample_rate = 24000
    texts = TTS_SENTENCES if case == "tts_sentence_stream" else [TTS_TEXT]
    for index, text in enumerate(texts):
        _, sentence_first_byte_ms, audio, sentence_sample_rate = await _bench_gemini_tts_once(client, model, text)
        if index == 0:
            first_byte_ms = sentence_first_byte_ms
            first_sentence_audio_ms = sentence_first_byte_ms
        audio_chunks.append(audio)
        sample_rate = sentence_sample_rate
    latency = now_ms() - start
    audio = b"".join(audio_chunks)
    out = AUDIO_DIR / f"gemini_tts_{model}_{case}_{int(time.time())}.wav"
    write_wav(out, audio, sample_rate=sample_rate)
    return BenchResult(
        "gemini",
        "tts",
        model,
        case,
        "ok",
        latency_ms=latency,
        ttft_ms=first_byte_ms,
        streaming=True,
        transport_mode="sse_generate_content_audio_stream",
        output_bytes=len(audio),
        sentence_count=len(texts),
        first_sentence_audio_ms=first_sentence_audio_ms,
        artifact_path=str(out.relative_to(ROOT)),
    )


async def bench_gemini_asr(model: str, case: str, audio_path: Path | None) -> BenchResult:
    from google import genai

    if not audio_path or not audio_path.exists():
        return BenchResult("gemini", "asr", model, case, "skipped", error="No ASR audio path provided")
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    start = now_ms()

    def run_sync() -> Any:
        uploaded = client.files.upload(file=audio_path)
        return client.models.generate_content(model=model, contents=[ASR_PROMPT, uploaded])

    response = await asyncio.to_thread(run_sync)
    latency = now_ms() - start
    text = getattr(response, "text", "") or ""
    usage = getattr(response, "usage_metadata", None)
    return BenchResult(
        "gemini",
        "asr",
        model,
        case,
        "ok",
        latency_ms=latency,
        streaming=False,
        transport_mode="batch_audio_understanding",
        input_tokens=usage_value(usage, "prompt_token_count"),
        output_tokens=usage_value(usage, "candidates_token_count"),
        cached_tokens=usage_value(usage, "cached_content_token_count"),
        output_chars=len(text),
        artifact_path=str(audio_path),
    )


async def run_case(
    provider: str,
    suite: str,
    model: str,
    case: str,
    audio_path: Path | None,
    max_output_tokens: int,
) -> BenchResult:
    try:
        if provider == "openai" and suite == "chat_llm":
            return await bench_openai_chat(model, case, max_output_tokens)
        if provider == "openai" and suite == "tts":
            return await bench_openai_tts(model, case)
        if provider == "openai" and suite == "asr":
            return await bench_openai_asr(model, case, audio_path)
        if provider == "gemini" and suite == "chat_llm":
            return await bench_gemini_chat(model, case, max_output_tokens)
        if provider == "gemini" and suite == "tts":
            return await bench_gemini_tts(model, case)
        if provider == "gemini" and suite == "asr":
            return await bench_gemini_asr(model, case, audio_path)
        return BenchResult(provider, suite, model, case, "skipped", error="Unsupported provider/suite")
    except Exception as exc:
        return BenchResult(provider, suite, model, case, "error", error=f"{type(exc).__name__}: {exc}")


def cases_for(entry: dict[str, Any], suite: str) -> list[str]:
    configured = entry.get("benchmark_cases") or []
    if configured:
        return configured
    if suite == "chat_llm":
        return ["plain_chat"]
    if suite == "tts":
        return ["tts_ttfb"]
    if suite == "asr":
        return ["batch_transcribe"]
    return []


def backend_aligned_case_allowlist(suite: str) -> set[str] | None:
    if suite == "chat_llm":
        return {"plain_chat", "reasoning_eval"}
    if suite == "tts":
        return {"tts_sentence_stream"}
    if suite == "asr":
        return {"batch_transcribe"}
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark OpenAI and Gemini models for the voice-agent pipeline.")
    parser.add_argument("--provider", choices=["openai", "gemini", "all"], default="all")
    parser.add_argument("--suite", choices=["chat_llm", "asr", "tts", "all"], default="all")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--asr-audio", type=Path, default=None, help="WAV/MP3/etc. file for ASR benchmarks")
    parser.add_argument("--dry-run", action="store_true", help="Print benchmark plan without calling APIs")
    parser.add_argument("--output", type=Path, default=None, help="JSONL output path")
    parser.add_argument("--models", default=None, help="Comma-separated model allowlist")
    parser.add_argument("--cases", default=None, help="Comma-separated benchmark case allowlist")
    parser.add_argument("--limit", type=int, default=None, help="Maximum planned calls to run")
    parser.add_argument("--runs", type=int, default=1, help="Repeat each planned benchmark N times")
    parser.add_argument("--max-output-tokens", type=int, default=96, help="Chat/search max output tokens")
    parser.add_argument("--winners", action="store_true", help="Print chat winners by provider")
    parser.add_argument(
        "--backend-aligned",
        action="store_true",
        help="Use cases that match current backend behavior: streaming chat, sentence-level streaming TTS, and batch ASR fixtures.",
    )
    args = parser.parse_args()

    load_env()
    matrix = load_matrix(args.matrix)
    providers = ["openai", "gemini"] if args.provider == "all" else [args.provider]
    suites = ["chat_llm", "tts", "asr"] if args.suite == "all" else [args.suite]
    output = args.output or RESULTS_DIR / f"benchmark_{int(time.time())}.jsonl"
    model_allowlist = csv_arg(args.models)
    case_allowlist = csv_arg(args.cases)

    planned: list[tuple[str, str, str, str]] = []
    for provider in providers:
        key_name = "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
        if not args.dry_run and not os.getenv(key_name):
            print(f"Skipping {provider}: {key_name} is not set")
            continue
        for suite in suites:
            suite_case_allowlist = case_allowlist
            if args.backend_aligned and suite_case_allowlist is None:
                suite_case_allowlist = backend_aligned_case_allowlist(suite)
            for entry in filter_models(selected_models(matrix, provider, suite), model_allowlist):
                for case in cases_for(entry, suite):
                    if suite_case_allowlist and case not in suite_case_allowlist:
                        continue
                    planned.append((provider, suite, entry["model"], case))
    planned = planned * max(1, args.runs)
    if args.limit is not None:
        planned = planned[: max(0, args.limit)]

    if args.dry_run:
        for item in planned:
            print("/".join(item))
        print(f"planned={len(planned)}")
        return 0

    results: list[BenchResult] = []
    for provider, suite, model, case in planned:
        print(f"running {provider}/{suite}/{model}/{case}")
        result = await run_case(provider, suite, model, case, args.asr_audio, args.max_output_tokens)
        results.append(result)
        write_jsonl(output, [result])
        print(f"  -> {result.status} latency_ms={result.latency_ms} ttft_ms={result.ttft_ms}")

    print(f"wrote {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")
    print_summary(results)
    if args.winners:
        print_winners(results)
    return 1 if any(r.status == "error" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
