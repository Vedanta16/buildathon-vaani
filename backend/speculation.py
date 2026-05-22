# backend/speculation.py
import asyncio
from difflib import SequenceMatcher
from typing import Callable, Awaitable
from backend.config import cfg

class SpeculationManager:
    def __init__(
        self,
        llm_fn: Callable[..., Awaitable[tuple[str, dict]]],
        on_token: Callable[[str], None],
        on_commit: Callable[[str, dict], None] | None = None,
        on_discard: Callable[[], None] | None = None,
        debounce_ms: int = cfg.spec_debounce_ms,
        min_words: int = cfg.spec_min_words,
        commit_ratio: float = cfg.spec_commit_ratio,
    ):
        self._llm_fn = llm_fn
        self._on_token = on_token
        self._on_commit = on_commit or (lambda text, usage: None)
        self._on_discard = on_discard or (lambda: None)
        self._debounce_ms = debounce_ms
        self._min_words = min_words
        self._commit_ratio = commit_ratio

        self._spec_task: asyncio.Task | None = None
        self._debounce_task: asyncio.Task | None = None
        self._last_partial: str = ""
        self._spec_input: str = ""
        self._spec_output: str = ""
        self._spec_usage: dict = {}

    async def on_partial(self, text: str) -> None:
        self._last_partial = text
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce(text))

    async def _debounce(self, text: str) -> None:
        try:
            await asyncio.sleep(self._debounce_ms / 1000.0)
        except asyncio.CancelledError:
            return
        if len(text.split()) < self._min_words:
            return
        if self._spec_task and not self._spec_task.done():
            self._spec_task.cancel()
        self._spec_input = text
        self._spec_task = asyncio.create_task(self._run_speculation(text))

    async def _run_speculation(self, text: str) -> None:
        try:
            full_text, usage = await self._llm_fn(text, on_token=self._on_token)
            self._spec_output = full_text
            self._spec_usage = usage
        except asyncio.CancelledError:
            pass

    async def on_final(self, final_text: str) -> str:
        """Returns 'commit' or 'discard'. Call when ASR final arrives."""
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        if self._spec_task is None:
            # No speculation was started — compare final to last partial using word overlap
            compare_base = self._spec_input or self._last_partial
            if compare_base:
                partial_words = set(compare_base.lower().split())
                final_words = set(final_text.lower().split())
                overlap = len(partial_words & final_words) / len(partial_words) if partial_words else 0.0
                if overlap >= self._commit_ratio:
                    # Run LLM now and commit
                    try:
                        full_text, usage = await self._llm_fn(final_text, on_token=self._on_token)
                        self._on_commit(full_text, usage)
                    except Exception:
                        pass
                    self._reset()
                    return "commit"
            self._on_discard()
            self._reset()
            return "discard"

        # Wait briefly for spec task to finish
        try:
            await asyncio.wait_for(asyncio.shield(self._spec_task), timeout=0.05)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        ratio = SequenceMatcher(None, self._spec_input, final_text).ratio()
        if ratio >= self._commit_ratio and self._spec_output:
            self._on_commit(self._spec_output, self._spec_usage)
            self._reset()
            return "commit"
        else:
            if self._spec_task and not self._spec_task.done():
                self._spec_task.cancel()
            self._on_discard()
            self._reset()
            return "discard"

    def _reset(self) -> None:
        self._spec_task = None
        self._spec_input = ""
        self._spec_output = ""
        self._spec_usage = {}
