import asyncio
from typing import Callable
from google import genai
from google.genai import types
from backend.config import cfg

_client: genai.Client | None = None

def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=cfg.gemini_api_key)
    return _client


async def stream_response(
    messages: list[dict],
    system_prompt: str,
    memory_block: str = "",
    on_token: Callable[[str], None] | None = None,
    model: str | None = None,
) -> tuple[str, dict]:
    """Stream Gemini response. Returns (full_text, usage_dict)."""
    if model is None:
        model = "gemini-2.5-flash"

    # Build contents from conversation history
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    sys_instruction = system_prompt
    if memory_block:
        sys_instruction = f"{system_prompt}\n\n{memory_block}"

    client = _get_client()
    full_text = ""
    usage: dict = {"model": model, "routed_small": False}

    async for chunk in await client.aio.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=sys_instruction,
            temperature=0.7,
        ),
    ):
        token = chunk.text or ""
        if token:
            full_text += token
            if on_token:
                on_token(token)
        if chunk.usage_metadata:
            usage.update({
                "prompt_tokens": chunk.usage_metadata.prompt_token_count or 0,
                "completion_tokens": chunk.usage_metadata.candidates_token_count or 0,
                "cached_tokens": 0,
            })

    return full_text, usage
