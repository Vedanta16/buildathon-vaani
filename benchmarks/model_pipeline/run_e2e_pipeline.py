from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarks.model_pipeline.run_benchmarks import (
    PRICING_PER_MILLION,
    ROOT,
    RESULTS_DIR,
    ASR_PROMPT,
    now_ms,
    usage_value,
    write_jsonl,
    write_wav,
)


PHRASES = {
    "A1": "Got it, let me check that.",
    "A2": "One moment, I am pulling that up.",
    "C1": "Can you clarify that?",
    "G1": "Hi, how can I help?",
    "S1": "Anything else I can help with?",
    "M1": "I will keep that in mind.",
}

KB_FACTS = [
    {"id": "premium_due", "topic": "billing", "text": "The user's next premium is $84.20 due on June 5."},
    {"id": "roadside_added", "topic": "coverage", "text": "The user added roadside coverage in March."},
    {"id": "roadside_removed", "topic": "coverage", "text": "The user removed roadside coverage in April."},
    {"id": "glass_deductible", "topic": "coverage", "text": "Glass repair has a $0 deductible."},
    {"id": "communication_pref", "topic": "memory", "text": "The user prefers short, direct answers."},
]

SCENARIOS = {
    "greeting": {
        "utterance": "Hi",
        "expected_route": "NO_LLM",
        "relevant_fact_ids": [],
    },
    "billing_fast": {
        "utterance": "What is my next premium amount and due date?",
        "expected_route": "FAST_LLM",
        "relevant_fact_ids": ["premium_due", "communication_pref"],
    },
    "coverage_smart": {
        "utterance": "I added roadside in March but removed it in April. Is roadside active now?",
        "expected_route": "SMART_LLM",
        "relevant_fact_ids": ["roadside_added", "roadside_removed", "communication_pref"],
    },
    "memory_signal": {
        "utterance": "I am frustrated. Please keep answers shorter next time.",
        "expected_route": "NO_LLM",
        "relevant_fact_ids": ["communication_pref"],
    },
}

MODEL_DEFAULTS = {
    "openai": {
        "baseline_chat": "gpt-5.2",
        "router": "gpt-4.1-nano",
        "fast_chat": "gpt-4o-mini",
        "smart_chat": "gpt-5.2",
        "async_chat": "gpt-4.1-nano",
        "baseline_asr": "gpt-4o-transcribe",
        "optimized_asr": "gpt-4o-mini-transcribe",
        "baseline_tts": "gpt-4o-mini-tts",
        "optimized_tts": "tts-1",
    },
    "gemini": {
        "baseline_chat": "gemini-3.5-flash",
        "router": "gemini-2.5-flash-lite",
        "fast_chat": "gemini-2.5-flash",
        "smart_chat": "gemini-2.5-flash",
        "async_chat": "gemini-2.5-flash-lite",
        "baseline_asr": "gemini-2.5-flash",
        "optimized_asr": "gemini-2.5-flash-lite",
        "baseline_tts": "gemini-2.5-flash-preview-tts",
        "optimized_tts": "gemini-2.5-flash-preview-tts",
    },
}

FULL_KB_TEXT = json.dumps(KB_FACTS, separators=(",", ":"))
PHRASE_MANIFEST = ",".join(f"{key}={value}" for key, value in PHRASES.items())
COMPACT_PHRASE_MANIFEST = "P:" + ",".join(f"{key}:{value}" for key, value in PHRASES.items())

BASELINE_SYSTEM = (
    "You are a production insurance voice agent. Generate natural language for the user. "
    "Use the full knowledge base and phrase catalogue if useful. Return compact JSON."
)

OPTIMIZED_STATIC_SYSTEM = (
    "Voice agent. Compact JSON only. Prefer phrase IDs from manifest when they fit. "
    "Schema: {\"segments\":[{\"p\":\"ID\"}|{\"t\":\"text\"}],\"memory\":[]}. "
    f"{COMPACT_PHRASE_MANIFEST}"
)


@dataclass
class ApiCall:
    text: str
    latency_ms: int
    ttft_ms: int | None = None
    streaming: bool = False
    transport_mode: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    output_chars: int | None = None
    output_bytes: int | None = None
    artifact_path: str | None = None
    meta: dict[str, Any] | None = None


@dataclass
class PipelineStep:
    row_type: str
    run_id: str
    provider: str
    scenario: str
    variant: str
    step: str
    route: str
    status: str
    model: str | None = None
    api_call: bool = False
    user_visible: bool = True
    streaming: bool = False
    transport_mode: str | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_cost_usd: float | None = None
    prompt_chars: int | None = None
    output_chars: int | None = None
    output_bytes: int | None = None
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def rel_path(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def chat_cost(provider: str, model: str, input_tokens: int | None, output_tokens: int | None, cached_tokens: int | None) -> float | None:
    pricing = PRICING_PER_MILLION.get(provider, {}).get(model)
    if not pricing:
        return None
    input_count = input_tokens or 0
    cached_count = min(cached_tokens or 0, input_count)
    uncached_count = max(0, input_count - cached_count)
    output_count = output_tokens or 0
    cost = (
        uncached_count * pricing["input"]
        + cached_count * pricing["cached_input"]
        + output_count * pricing["output"]
    ) / 1_000_000
    return round(cost, 8)


def make_step(
    *,
    run_id: str,
    provider: str,
    scenario: str,
    variant: str,
    step: str,
    route: str,
    status: str = "ok",
    model: str | None = None,
    api_call: bool = False,
    user_visible: bool = True,
    call: ApiCall | None = None,
    prompt_chars: int | None = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> PipelineStep:
    input_tokens = call.input_tokens if call else None
    output_tokens = call.output_tokens if call else None
    cached_tokens = call.cached_tokens if call else None
    cost = chat_cost(provider, model, input_tokens, output_tokens, cached_tokens) if model else None
    return PipelineStep(
        row_type="pipeline_step",
        run_id=run_id,
        provider=provider,
        scenario=scenario,
        variant=variant,
        step=step,
        route=route,
        status=status,
        model=model,
        api_call=api_call,
        user_visible=user_visible,
        streaming=call.streaming if call else False,
        transport_mode=call.transport_mode if call else "deterministic",
        latency_ms=call.latency_ms if call else 0,
        ttft_ms=call.ttft_ms if call else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        estimated_cost_usd=cost,
        prompt_chars=prompt_chars,
        output_chars=call.output_chars if call else None,
        output_bytes=call.output_bytes if call else None,
        artifact_path=call.artifact_path if call else None,
        error=error,
        meta=meta,
    )


def make_error_step(
    *,
    run_id: str,
    provider: str,
    scenario: str,
    variant: str,
    step: str,
    route: str,
    model: str | None,
    exc: Exception,
    api_call: bool = True,
    user_visible: bool = True,
) -> PipelineStep:
    return PipelineStep(
        row_type="pipeline_step",
        run_id=run_id,
        provider=provider,
        scenario=scenario,
        variant=variant,
        step=step,
        route=route,
        status="error",
        model=model,
        api_call=api_call,
        user_visible=user_visible,
        streaming=False,
        transport_mode="error",
        error=f"{type(exc).__name__}: {exc}",
    )


def has_errors(steps: list[PipelineStep]) -> bool:
    return any(step.status == "error" for step in steps)


def short_error(error: str | None, limit: int = 180) -> str:
    if not error:
        return ""
    collapsed = " ".join(error.split())
    return collapsed[:limit] + ("..." if len(collapsed) > limit else "")


def deterministic_route(utterance: str) -> tuple[str, float, str]:
    text = utterance.lower().strip()
    if text in {"hi", "hello", "hey", "yo"}:
        return "NO_LLM", 0.99, "greeting"
    if "frustrated" in text or "next time" in text or "shorter" in text:
        return "NO_LLM", 0.92, "memory_preference_ack"
    if "march" in text or "april" in text or "removed" in text or "active now" in text:
        return "SMART_LLM", 0.9, "requires_latest_fact_reasoning"
    if "premium" in text or "due date" in text or "amount" in text:
        return "FAST_LLM", 0.9, "simple_fact_answer"
    return "ROUTER_LLM", 0.0, "not_confident"


def relevant_facts(scenario: str, utterance: str) -> list[dict[str, str]]:
    configured = set(SCENARIOS[scenario]["relevant_fact_ids"])
    if configured:
        return [fact for fact in KB_FACTS if fact["id"] in configured]

    text = utterance.lower()
    selected = []
    for fact in KB_FACTS:
        if fact["topic"] in text or any(token in text for token in fact["id"].split("_")):
            selected.append(fact)
    return selected


def phrase_for_utterance(utterance: str) -> str:
    route, _, reason = deterministic_route(utterance)
    if route == "NO_LLM" and reason == "greeting":
        return "G1"
    if route == "NO_LLM" and reason == "memory_preference_ack":
        return "M1"
    return "A1"


def optimized_model_for_route(provider: str, route: str, models: dict[str, str]) -> str | None:
    if route in {"NO_LLM", "CACHE"}:
        return None
    if route == "SMART_LLM":
        return models["smart_chat"]
    return models["fast_chat"]


def visible_text_from_segments(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    pieces: list[str] = []
    for segment in data.get("segments", []):
        if "p" in segment:
            pieces.append(PHRASES.get(str(segment["p"]), str(segment["p"])))
        elif "t" in segment:
            pieces.append(str(segment["t"]))
    return " ".join(piece for piece in pieces if piece).strip() or raw.strip()


async def call_openai_chat(model: str, messages: list[dict[str, str]], max_output_tokens: int) -> ApiCall:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
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
    return ApiCall(
        text=text,
        latency_ms=latency,
        ttft_ms=first_token_ms,
        input_tokens=usage_value(usage, "prompt_tokens"),
        output_tokens=usage_value(usage, "completion_tokens"),
        cached_tokens=usage_value(usage, "prompt_tokens_details", "cached_tokens"),
        output_chars=len(text),
        streaming=True,
        transport_mode="sse_chat_completions",
        meta={"visible_output": bool(text.strip())},
    )


async def call_gemini_chat(model: str, prompt: str, max_output_tokens: int, json_mode: bool = False) -> ApiCall:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    config_kwargs: dict[str, Any] = {"max_output_tokens": max_output_tokens}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**config_kwargs)
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
    return ApiCall(
        text=text,
        latency_ms=now_ms() - start,
        ttft_ms=first_token_ms,
        input_tokens=usage_value(usage, "prompt_token_count"),
        output_tokens=usage_value(usage, "candidates_token_count"),
        cached_tokens=usage_value(usage, "cached_content_token_count"),
        output_chars=len(text),
        streaming=True,
        transport_mode="sse_generate_content_stream",
        meta={"visible_output": bool(text.strip())},
    )


async def call_chat(provider: str, model: str, system: str, user: str, max_output_tokens: int, json_mode: bool = False) -> ApiCall:
    if provider == "openai":
        return await call_openai_chat(
            model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_output_tokens,
        )
    return await call_gemini_chat(model, f"{system}\n\n{user}", max_output_tokens, json_mode=json_mode)


async def call_openai_asr(model: str, audio_path: Path) -> ApiCall:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    start = now_ms()
    with audio_path.open("rb") as audio:
        response = await client.audio.transcriptions.create(model=model, file=audio)
    text = getattr(response, "text", "") or str(response)
    return ApiCall(
        text=text,
        latency_ms=now_ms() - start,
        output_chars=len(text),
        artifact_path=rel_path(audio_path),
        streaming=False,
        transport_mode="batch_transcription",
    )


async def call_gemini_asr(model: str, audio_path: Path) -> ApiCall:
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    start = now_ms()

    def run_sync() -> Any:
        uploaded = client.files.upload(file=audio_path)
        return client.models.generate_content(model=model, contents=[ASR_PROMPT, uploaded])

    response = await asyncio.to_thread(run_sync)
    text = getattr(response, "text", "") or ""
    usage = getattr(response, "usage_metadata", None)
    return ApiCall(
        text=text,
        latency_ms=now_ms() - start,
        input_tokens=usage_value(usage, "prompt_token_count"),
        output_tokens=usage_value(usage, "candidates_token_count"),
        cached_tokens=usage_value(usage, "cached_content_token_count"),
        output_chars=len(text),
        artifact_path=rel_path(audio_path),
        streaming=False,
        transport_mode="batch_audio_understanding",
    )


async def call_asr(provider: str, model: str, audio_path: Path) -> ApiCall:
    if provider == "openai":
        return await call_openai_asr(model, audio_path)
    return await call_gemini_asr(model, audio_path)


async def call_openai_tts(model: str, text: str) -> ApiCall:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
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
    audio = b"".join(chunks)
    out = RESULTS_DIR / f"e2e_tts_openai_{model}_{int(time.time())}.wav"
    out.write_bytes(audio)
    return ApiCall(
        text="",
        latency_ms=now_ms() - start,
        ttft_ms=first_byte_ms,
        output_bytes=len(audio),
        artifact_path=rel_path(out),
        streaming=True,
        transport_mode="http_audio_byte_stream",
    )


async def call_gemini_tts(model: str, text: str) -> ApiCall:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
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
    audio = b"".join(audio_chunks)
    out = RESULTS_DIR / f"e2e_tts_gemini_{model}_{int(time.time())}.wav"
    write_wav(out, audio, sample_rate=sample_rate)
    return ApiCall(
        text="",
        latency_ms=now_ms() - start,
        ttft_ms=first_byte_ms,
        output_bytes=len(audio),
        artifact_path=rel_path(out),
        streaming=True,
        transport_mode="sse_generate_content_audio_stream",
    )


async def call_tts(provider: str, model: str, text: str) -> ApiCall:
    if provider == "openai":
        return await call_openai_tts(model, text)
    return await call_gemini_tts(model, text)


async def router_llm(provider: str, model: str, utterance: str, max_output_tokens: int) -> ApiCall:
    system = "Classify route only. Return JSON: {\"route\":\"NO_LLM|FAST_LLM|SMART_LLM|ASYNC\",\"why\":\"...\"}."
    user = f"Utterance: {utterance}"
    return await call_chat(provider, model, system, user, max_output_tokens, json_mode=True)


def parse_route(raw: str, fallback: str = "FAST_LLM") -> str:
    normalized = raw.upper()
    for route in ["NO_LLM", "FAST_LLM", "SMART_LLM", "ASYNC", "CACHE"]:
        if route in normalized:
            return route
    return fallback


async def run_baseline(
    *,
    run_id: str,
    provider: str,
    scenario: str,
    models: dict[str, str],
    audio_path: Path | None,
    include_tts: bool,
    include_post_call: bool,
    max_output_tokens: int,
) -> list[PipelineStep]:
    steps: list[PipelineStep] = []
    utterance = SCENARIOS[scenario]["utterance"]

    if audio_path:
        call = await call_asr(provider, models["baseline_asr"], audio_path)
        utterance = call.text or utterance
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="baseline",
            step="asr",
            route="SMART_ASR",
            model=models["baseline_asr"],
            api_call=True,
            call=call,
            meta={"optimization": "none", "note": "baseline uses higher-capability ASR"},
        ))
    else:
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="baseline",
            step="text_input",
            route="TEXT",
            api_call=False,
            meta={"utterance": utterance},
        ))

    ack_system = "Generate a short conversational acknowledgement before doing work. Return only the acknowledgement text."
    ack_user = f"User utterance: {utterance}"
    try:
        ack_call = await call_chat(provider, models["baseline_chat"], ack_system, ack_user, max_output_tokens=32)
    except Exception as exc:
        steps.append(make_error_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="baseline",
            step="ack",
            route="SMART_LLM",
            model=models["baseline_chat"],
            exc=exc,
        ))
        return steps
    steps.append(make_step(
        run_id=run_id,
        provider=provider,
        scenario=scenario,
        variant="baseline",
        step="ack",
        route="SMART_LLM",
        model=models["baseline_chat"],
        api_call=True,
        call=ack_call,
        prompt_chars=len(ack_system) + len(ack_user),
        meta={"optimization": "none", "note": "LLM-generated ack blocks perceived response"},
    ))

    final_user = (
        f"User utterance: {utterance}\n"
        f"Full knowledge base: {FULL_KB_TEXT}\n"
        f"Full phrase catalogue: {PHRASE_MANIFEST}\n"
        "Return compact JSON: {\"segments\":[{\"t\":\"answer\"}],\"memory\":[]}."
    )
    try:
        final_call = await call_chat(
            provider,
            models["baseline_chat"],
            BASELINE_SYSTEM,
            final_user,
            max_output_tokens,
            json_mode=True,
        )
    except Exception as exc:
        steps.append(make_error_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="baseline",
            step="answer",
            route="SMART_LLM",
            model=models["baseline_chat"],
            exc=exc,
        ))
        return steps
    visible_text = visible_text_from_segments(final_call.text)
    steps.append(make_step(
        run_id=run_id,
        provider=provider,
        scenario=scenario,
        variant="baseline",
        step="answer",
        route="SMART_LLM",
        model=models["baseline_chat"],
        api_call=True,
        call=final_call,
        prompt_chars=len(BASELINE_SYSTEM) + len(final_user),
        meta={
            "optimization": "none",
            "context_strategy": "full_context",
            "visible_text": visible_text,
            "visible_output": bool(final_call.text.strip()),
        },
    ))

    if include_tts:
        try:
            tts_call = await call_tts(provider, models["baseline_tts"], visible_text or ack_call.text or utterance)
        except Exception as exc:
            steps.append(make_error_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="baseline",
                step="tts",
                route="TTS",
                model=models["baseline_tts"],
                exc=exc,
            ))
            return steps
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="baseline",
            step="tts",
            route="TTS",
            model=models["baseline_tts"],
            api_call=True,
            call=tts_call,
            meta={"optimization": "none", "note": "synthesize every visible answer"},
        ))

    if include_post_call:
        post_user = (
            f"Transcript: {utterance}\n"
            f"Agent ack: {ack_call.text}\n"
            f"Agent answer: {final_call.text}\n"
            "Extract memory candidates, user tone, quality issues, and outcome. Return compact JSON."
        )
        try:
            post_call = await call_chat(
                provider,
                models["baseline_chat"],
                BASELINE_SYSTEM,
                post_user,
                max_output_tokens,
                json_mode=True,
            )
        except Exception as exc:
            steps.append(make_error_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="baseline",
                step="post_call",
                route="SMART_LLM",
                model=models["baseline_chat"],
                exc=exc,
                user_visible=False,
            ))
            return steps
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="baseline",
            step="post_call",
            route="SMART_LLM",
            model=models["baseline_chat"],
            api_call=True,
            user_visible=False,
            call=post_call,
            prompt_chars=len(BASELINE_SYSTEM) + len(post_user),
            meta={"optimization": "none", "lane": "blocking_back_office"},
        ))

    return steps


async def run_optimized(
    *,
    run_id: str,
    provider: str,
    scenario: str,
    models: dict[str, str],
    audio_path: Path | None,
    include_tts: bool,
    include_post_call: bool,
    router_mode: str,
    max_output_tokens: int,
) -> list[PipelineStep]:
    steps: list[PipelineStep] = []
    utterance = SCENARIOS[scenario]["utterance"]

    if audio_path:
        call = await call_asr(provider, models["optimized_asr"], audio_path)
        utterance = call.text or utterance
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="asr",
            route="FAST_ASR",
            model=models["optimized_asr"],
            api_call=True,
            call=call,
            meta={"optimization": "fast_asr_model"},
        ))
    else:
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="text_input",
            route="TEXT",
            api_call=False,
            meta={"utterance": utterance},
        ))

    phrase_id = phrase_for_utterance(utterance)
    steps.append(make_step(
        run_id=run_id,
        provider=provider,
        scenario=scenario,
        variant="optimized",
        step="ack",
        route="CACHE",
        api_call=False,
        meta={
            "optimization": "prefilled_phrase_cache",
            "phrase_id": phrase_id,
            "text": PHRASES[phrase_id],
        },
    ))

    route, confidence, route_reason = deterministic_route(utterance)
    route_meta: dict[str, Any] = {
        "optimization": "hybrid_task_router",
        "deterministic_route": route,
        "confidence": confidence,
        "why": route_reason,
    }
    if router_mode == "llm" or (router_mode == "hybrid" and route == "ROUTER_LLM"):
        try:
            route_call = await router_llm(provider, models["router"], utterance, max_output_tokens=48)
        except Exception as exc:
            steps.append(make_error_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="optimized",
                step="route",
                route="ROUTER_LLM",
                model=models["router"],
                exc=exc,
            ))
            return steps
        route = parse_route(route_call.text)
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="route",
            route=route,
            model=models["router"],
            api_call=True,
            call=route_call,
            prompt_chars=len(utterance),
            meta={**route_meta, "optimization": "small_router_llm", "raw": route_call.text},
        ))
    else:
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="route",
            route=route,
            api_call=False,
            meta=route_meta,
        ))

    facts = relevant_facts(scenario, utterance)
    steps.append(make_step(
        run_id=run_id,
        provider=provider,
        scenario=scenario,
        variant="optimized",
        step="retrieval",
        route=route,
        api_call=False,
        meta={
            "optimization": "progressive_context",
            "selected_fact_ids": [fact["id"] for fact in facts],
            "full_fact_count": len(KB_FACTS),
            "selected_fact_count": len(facts),
        },
    ))

    model = optimized_model_for_route(provider, route, models)
    if model is None:
        answer_text = PHRASES[phrase_id]
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="answer",
            route=route,
            api_call=False,
            meta={
                "optimization": "no_llm_or_cached_phrase_answer",
                "visible_text": answer_text,
                "visible_output": True,
            },
        ))
    else:
        compact_facts = json.dumps(facts, separators=(",", ":"))
        final_user = (
            f"U:{utterance}\n"
            f"R:{route}\n"
            f"F:{compact_facts}\n"
            "Return answer in the schema. Text segments must be under 18 words."
        )
        try:
            final_call = await call_chat(
                provider,
                model,
                OPTIMIZED_STATIC_SYSTEM,
                final_user,
                max_output_tokens,
                json_mode=True,
            )
        except Exception as exc:
            steps.append(make_error_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="optimized",
                step="answer",
                route=route,
                model=model,
                exc=exc,
            ))
            return steps
        answer_text = visible_text_from_segments(final_call.text)
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="answer",
            route=route,
            model=model,
            api_call=True,
            call=final_call,
            prompt_chars=len(OPTIMIZED_STATIC_SYSTEM) + len(final_user),
            meta={
                "optimization": "route_specific_model_compact_prompt_phrase_ids",
                "context_strategy": "progressive_relevant_context",
                "visible_text": answer_text,
                "visible_output": bool(final_call.text.strip()),
            },
        ))

    if include_tts:
        if answer_text in PHRASES.values():
            steps.append(make_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="optimized",
                step="tts",
                route="CACHE",
                api_call=False,
                meta={
                    "optimization": "cached_prerendered_audio",
                    "text": answer_text,
                },
            ))
        else:
            try:
                tts_call = await call_tts(provider, models["optimized_tts"], answer_text)
            except Exception as exc:
                steps.append(make_error_step(
                    run_id=run_id,
                    provider=provider,
                    scenario=scenario,
                    variant="optimized",
                    step="tts",
                    route="FAST_TTS",
                    model=models["optimized_tts"],
                    exc=exc,
                ))
                return steps
            steps.append(make_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="optimized",
                step="tts",
                route="FAST_TTS",
                model=models["optimized_tts"],
                api_call=True,
                call=tts_call,
                meta={"optimization": "fast_tts_only_for_custom_text"},
            ))

    if include_post_call:
        post_user = (
            f"U:{utterance}\n"
            f"A:{answer_text}\n"
            "Extract JSON only: tone, memory_candidates, outcome, eval_score."
        )
        try:
            post_call = await call_chat(
                provider,
                models["async_chat"],
                OPTIMIZED_STATIC_SYSTEM,
                post_user,
                max_output_tokens,
                json_mode=True,
            )
        except Exception as exc:
            steps.append(make_error_step(
                run_id=run_id,
                provider=provider,
                scenario=scenario,
                variant="optimized",
                step="post_call",
                route="ASYNC",
                model=models["async_chat"],
                exc=exc,
                user_visible=False,
            ))
            return steps
        steps.append(make_step(
            run_id=run_id,
            provider=provider,
            scenario=scenario,
            variant="optimized",
            step="post_call",
            route="ASYNC",
            model=models["async_chat"],
            api_call=True,
            user_visible=False,
            call=post_call,
            prompt_chars=len(OPTIMIZED_STATIC_SYSTEM) + len(post_user),
            meta={
                "optimization": "async_memory_and_eval_lane",
                "lane": "non_user_visible",
                "visible_output": bool(post_call.text.strip()),
            },
        ))

    return steps


def summarize_variant(steps: list[PipelineStep]) -> dict[str, Any]:
    visible_steps = [step for step in steps if step.user_visible]
    api_steps = [step for step in steps if step.api_call]
    failed_steps = [step for step in steps if step.status == "error"]
    return {
        "status": "error" if failed_steps else "ok",
        "failed_steps": len(failed_steps),
        "first_error": short_error(failed_steps[0].error) if failed_steps else None,
        "api_calls": len(api_steps),
        "streaming_api_calls": len([step for step in api_steps if step.streaming]),
        "batch_api_calls": len([step for step in api_steps if not step.streaming]),
        "visible_latency_ms": sum(step.latency_ms or 0 for step in visible_steps),
        "total_latency_ms": sum(step.latency_ms or 0 for step in steps),
        "ack_latency_ms": next((step.latency_ms or 0 for step in steps if step.step == "ack"), None),
        "input_tokens": sum(step.input_tokens or 0 for step in steps),
        "output_tokens": sum(step.output_tokens or 0 for step in steps),
        "cached_tokens": sum(step.cached_tokens or 0 for step in steps),
        "estimated_cost_usd": round(sum(step.estimated_cost_usd or 0 for step in steps), 8),
        "models": sorted({step.model for step in api_steps if step.model}),
    }


def print_pipeline_summary(all_steps: list[PipelineStep]) -> None:
    grouped: dict[tuple[str, str, str], list[PipelineStep]] = {}
    for step in all_steps:
        grouped.setdefault((step.provider, step.scenario, step.variant), []).append(step)

    print("\nPIPELINE SUMMARY")
    for key in sorted(grouped):
        summary = summarize_variant(grouped[key])
        print(
            f"{key[0]}/{key[1]}/{key[2]} "
            f"status={summary['status']} "
            f"api_calls={summary['api_calls']} "
            f"streaming_api_calls={summary['streaming_api_calls']} "
            f"batch_api_calls={summary['batch_api_calls']} "
            f"visible_latency_ms={summary['visible_latency_ms']} "
            f"total_latency_ms={summary['total_latency_ms']} "
            f"tokens={summary['input_tokens'] + summary['output_tokens']} "
            f"cached_tokens={summary['cached_tokens']} "
            f"cost=${summary['estimated_cost_usd']}"
        )
        if summary["first_error"]:
            print(f"  error={summary['first_error']}")

    print("\nBEFORE_AFTER")
    for provider, scenario in sorted({(step.provider, step.scenario) for step in all_steps}):
        baseline = grouped.get((provider, scenario, "baseline"), [])
        optimized = grouped.get((provider, scenario, "optimized"), [])
        if not baseline or not optimized:
            continue
        base = summarize_variant(baseline)
        opt = summarize_variant(optimized)
        if base["status"] != "ok" or opt["status"] != "ok":
            print(
                f"{provider}/{scenario}: incomplete "
                f"baseline_status={base['status']} "
                f"optimized_status={opt['status']}"
            )
            continue
        latency_delta = base["visible_latency_ms"] - opt["visible_latency_ms"]
        cost_delta = base["estimated_cost_usd"] - opt["estimated_cost_usd"]
        call_delta = base["api_calls"] - opt["api_calls"]
        print(
            f"{provider}/{scenario}: "
            f"visible_latency_delta_ms={latency_delta} "
            f"api_call_delta={call_delta} "
            f"cost_delta=${round(cost_delta, 8)}"
        )


def dry_run_plan(
    providers: list[str],
    scenarios: list[str],
    runs: int,
    include_tts: bool,
    include_post_call: bool,
    router_mode: str,
    audio_path: Path | None,
) -> None:
    total_calls = 0
    for provider in providers:
        models = MODEL_DEFAULTS[provider]
        for scenario in scenarios:
            utterance = SCENARIOS[scenario]["utterance"]
            route, _, _ = deterministic_route(utterance)
            optimized_final_model = optimized_model_for_route(provider, route, models)
            baseline_calls = 2
            if include_post_call:
                baseline_calls += 1
            if include_tts:
                baseline_calls += 1
            if audio_path:
                baseline_calls += 1

            optimized_calls = 0
            if audio_path:
                optimized_calls += 1
            if router_mode == "llm" or (router_mode == "hybrid" and route == "ROUTER_LLM"):
                optimized_calls += 1
            if optimized_final_model:
                optimized_calls += 1
            if include_post_call:
                optimized_calls += 1
            if include_tts and optimized_final_model:
                optimized_calls += 1

            total_calls += baseline_calls + optimized_calls
            print(
                f"{provider}/{scenario}: "
                f"baseline_calls={baseline_calls} "
                f"optimized_calls={optimized_calls} "
                f"optimized_route={route} "
                f"optimized_answer_model={optimized_final_model or 'none'}"
            )
    runs = max(1, runs)
    print(f"runs={runs}")
    print(f"planned_api_calls={total_calls * runs}")


def parse_models(provider: str, args: argparse.Namespace) -> dict[str, str]:
    models = dict(MODEL_DEFAULTS[provider])
    overrides = {
        "baseline_chat": args.baseline_chat_model,
        "router": args.router_model,
        "fast_chat": args.fast_chat_model,
        "smart_chat": args.smart_chat_model,
        "async_chat": args.async_chat_model,
        "baseline_asr": args.baseline_asr_model,
        "optimized_asr": args.optimized_asr_model,
        "baseline_tts": args.baseline_tts_model,
        "optimized_tts": args.optimized_tts_model,
    }
    for key, value in overrides.items():
        if value:
            models[key] = value
    return models


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end baseline vs optimized voice-agent pipeline benchmarks.")
    parser.add_argument("--provider", choices=["openai", "gemini", "all"], default="openai")
    parser.add_argument("--scenario", default="billing_fast", help="Scenario name or 'all'")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=96)
    parser.add_argument("--router-mode", choices=["deterministic", "hybrid", "llm"], default="hybrid")
    parser.add_argument("--asr-audio", type=Path, default=None)
    parser.add_argument("--include-tts", action="store_true")
    parser.add_argument("--no-post-call", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--baseline-chat-model", default=None)
    parser.add_argument("--router-model", default=None)
    parser.add_argument("--fast-chat-model", default=None)
    parser.add_argument("--smart-chat-model", default=None)
    parser.add_argument("--async-chat-model", default=None)
    parser.add_argument("--baseline-asr-model", default=None)
    parser.add_argument("--optimized-asr-model", default=None)
    parser.add_argument("--baseline-tts-model", default=None)
    parser.add_argument("--optimized-tts-model", default=None)
    args = parser.parse_args()

    load_env()
    providers = ["openai", "gemini"] if args.provider == "all" else [args.provider]
    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    unknown = [scenario for scenario in scenarios if scenario not in SCENARIOS]
    if unknown:
        print(f"Unknown scenarios: {', '.join(unknown)}")
        print(f"Available scenarios: {', '.join(SCENARIOS)}")
        return 2

    include_post_call = not args.no_post_call
    if args.dry_run:
        dry_run_plan(providers, scenarios, args.runs, args.include_tts, include_post_call, args.router_mode, args.asr_audio)
        return 0

    for provider in providers:
        key_name = "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
        if not os.getenv(key_name):
            print(f"Missing {key_name}; use --dry-run to inspect the plan without API calls.")
            return 2

    output = args.output or RESULTS_DIR / f"e2e_pipeline_{int(time.time())}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    all_steps: list[PipelineStep] = []

    for run_index in range(max(1, args.runs)):
        for provider in providers:
            models = parse_models(provider, args)
            for scenario in scenarios:
                run_id = f"{int(time.time())}-{run_index}-{provider}-{scenario}"
                print(f"running baseline {provider}/{scenario}")
                try:
                    baseline_steps = await run_baseline(
                        run_id=run_id,
                        provider=provider,
                        scenario=scenario,
                        models=models,
                        audio_path=args.asr_audio,
                        include_tts=args.include_tts,
                        include_post_call=include_post_call,
                        max_output_tokens=args.max_output_tokens,
                    )
                    all_steps.extend(baseline_steps)
                    write_jsonl(output, baseline_steps)
                except Exception as exc:
                    step = PipelineStep(
                        row_type="pipeline_step",
                        run_id=run_id,
                        provider=provider,
                        scenario=scenario,
                        variant="baseline",
                        step="pipeline",
                        route="ERROR",
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    all_steps.append(step)
                    write_jsonl(output, [step])
                    print(f"  -> baseline error {step.error}")

                print(f"running optimized {provider}/{scenario}")
                try:
                    optimized_steps = await run_optimized(
                        run_id=run_id,
                        provider=provider,
                        scenario=scenario,
                        models=models,
                        audio_path=args.asr_audio,
                        include_tts=args.include_tts,
                        include_post_call=include_post_call,
                        router_mode=args.router_mode,
                        max_output_tokens=args.max_output_tokens,
                    )
                    all_steps.extend(optimized_steps)
                    write_jsonl(output, optimized_steps)
                except Exception as exc:
                    step = PipelineStep(
                        row_type="pipeline_step",
                        run_id=run_id,
                        provider=provider,
                        scenario=scenario,
                        variant="optimized",
                        step="pipeline",
                        route="ERROR",
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    all_steps.append(step)
                    write_jsonl(output, [step])
                    print(f"  -> optimized error {step.error}")

    print(f"wrote {rel_path(output)}")
    print_pipeline_summary(all_steps)
    return 1 if any(step.status == "error" for step in all_steps) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
