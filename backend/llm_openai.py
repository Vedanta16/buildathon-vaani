import asyncio
from typing import Callable
from openai import AsyncOpenAI
from backend.config import cfg

ROUTING_ENABLED: bool = True

_client: AsyncOpenAI | None = None

def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client

def select_model(text: str) -> str:
    if not ROUTING_ENABLED:
        return cfg.large_model
    normalized = text.strip().lower().rstrip(".,!?")
    if normalized in cfg.short_answer_set:
        return cfg.small_model
    if len(text.split()) <= cfg.routing_word_threshold:
        return cfg.small_model
    return cfg.large_model

async def stream_response(
    messages: list[dict],
    system_prompt: str,
    memory_block: str = "",
    on_token: Callable[[str], None] | None = None,
    model: str | None = None,
) -> tuple[str, dict]:
    """
    Stream LLM response tokens. Returns (full_text, usage_dict).

    Prompt structure for caching:
      1. Static system prompt (cacheable)
      2. Memory block (cacheable when unchanged)
      3. Conversation history + current turn
    """
    if model is None:
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        model = select_model(last_user)

    full_messages = [{"role": "system", "content": system_prompt}]
    if memory_block:
        full_messages.append({"role": "system", "content": memory_block})
    full_messages.extend(messages)

    full_text = ""
    usage: dict = {"model": model, "routed_small": model == cfg.small_model}

    client = _get_client()
    stream = await client.chat.completions.create(
        model=model,
        messages=full_messages,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            full_text += token
            if on_token:
                on_token(token)
        if chunk.usage:
            details = getattr(chunk.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) if details else 0
            usage.update({
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "cached_tokens": cached,
            })
    return full_text, usage
