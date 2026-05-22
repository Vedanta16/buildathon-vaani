# backend/tests/test_speculation.py
import asyncio
import pytest
from unittest.mock import AsyncMock
from backend.speculation import SpeculationManager

@pytest.mark.asyncio
async def test_fires_after_debounce():
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=2,
        llm_fn=AsyncMock(return_value=("hello response", {})),
        on_token=lambda t: None,
    )
    await mgr.on_partial("hello there")
    await asyncio.sleep(0.15)  # wait for debounce
    assert mgr._spec_task is not None

@pytest.mark.asyncio
async def test_commits_on_similar_final():
    committed = []
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=2,
        llm_fn=AsyncMock(return_value=("sure thing", {})),
        on_token=lambda t: None,
        on_commit=lambda text, usage: committed.append(text),
    )
    await mgr.on_partial("sure")
    await asyncio.sleep(0.15)
    result = await mgr.on_final("sure thing")
    assert result == "commit"

@pytest.mark.asyncio
async def test_discards_on_divergent_final():
    discarded = []
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=2,
        llm_fn=AsyncMock(return_value=("response for sure", {})),
        on_token=lambda t: None,
        on_discard=lambda: discarded.append(True),
    )
    await mgr.on_partial("sure")
    await asyncio.sleep(0.15)
    result = await mgr.on_final("completely different question about my policy renewal please")
    assert result == "discard"
    assert discarded

@pytest.mark.asyncio
async def test_does_not_fire_below_min_words():
    mgr = SpeculationManager(
        debounce_ms=50,
        min_words=5,
        llm_fn=AsyncMock(return_value=("ok", {})),
        on_token=lambda t: None,
    )
    await mgr.on_partial("yes")
    await asyncio.sleep(0.15)
    assert mgr._spec_task is None
