from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


AsrProvider = Literal["gemini_live", "openai_realtime", "mock"]
VadMode = Literal["local_manual"]
RouteLane = Literal["NO_LLM", "CACHE", "FAST_LLM", "SMART_LLM", "ASYNC"]
SegmentType = Literal["text", "prefilled_phrase", "silence", "earcon"]
CachePolicy = Literal["required", "prefer", "bypass"]
MemoryCategory = Literal[
    "user_preference",
    "durable_fact",
    "recent_issue",
    "sentiment_trend",
    "private_note",
]


@dataclass(slots=True)
class ConversationMessage:
    role: Literal["user", "assistant", "system"]
    content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TurnTimestamps:
    vad_start_ms: int | None = None
    asr_first_partial_ms: int | None = None
    asr_final_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeFlags:
    smart_routing: bool = True
    speculative: bool = True
    phrase_cache: bool = True
    filler: bool = True
    memory: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryItem:
    id: str
    category: MemoryCategory
    text: str
    confidence: float = 1.0
    sensitivity: Literal["normal", "sensitive", "private"] = "normal"
    stale: bool = False
    source: Literal["reviewed", "inferred", "imported"] = "reviewed"
    tags: list[str] = field(default_factory=list)

    def safe_for_prompt(self) -> bool:
        return (
            self.confidence >= 0.75
            and not self.stale
            and self.sensitivity == "normal"
            and self.category != "private_note"
            and self.source == "reviewed"
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryContext:
    items: list[MemoryItem] = field(default_factory=list)
    included_ids: list[str] = field(default_factory=list)
    excluded_ids: list[str] = field(default_factory=list)
    reason: str = "memory_disabled"

    def to_prompt_block(self) -> str:
        included = [item for item in self.items if item.id in set(self.included_ids)]
        if not included:
            return ""
        lines = ["Reviewed memory relevant to this turn:"]
        lines.extend(f"- {item.text}" for item in included)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "included_ids": list(self.included_ids),
            "excluded_ids": list(self.excluded_ids),
            "reason": self.reason,
        }


@dataclass(slots=True)
class UserTurn:
    session_id: str
    user_id: str
    turn_id: str
    user_text: str
    asr_provider: AsrProvider
    vad_mode: VadMode
    timestamps: TurnTimestamps = field(default_factory=TurnTimestamps)
    conversation_history: list[ConversationMessage] = field(default_factory=list)
    memory_context: MemoryContext | None = None
    runtime_flags: RuntimeFlags = field(default_factory=RuntimeFlags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "turn_id": self.turn_id,
            "user_text": self.user_text,
            "asr_provider": self.asr_provider,
            "vad_mode": self.vad_mode,
            "timestamps": self.timestamps.to_dict(),
            "conversation_history": [m.to_dict() for m in self.conversation_history],
            "memory_context": self.memory_context.to_dict() if self.memory_context else None,
            "runtime_flags": self.runtime_flags.to_dict(),
        }


@dataclass(slots=True)
class RouteDecision:
    lane: RouteLane
    reason: str
    route: str
    model: str | None = None
    confidence: float = 1.0
    phrase_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SpokenSegment:
    id: str
    type: SegmentType
    should_display: bool
    should_speak: bool
    cache_policy: CachePolicy
    text: str | None = None
    phrase_id: str | None = None
    voice: str | None = None
    locale: str | None = None

    def spoken_text(self) -> str:
        return self.text or ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryUpdate:
    category: MemoryCategory
    text: str
    confidence: float
    requires_review: bool = True
    source: Literal["post_call", "live_turn", "operator"] = "post_call"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PostCallJob:
    job_type: Literal["summary", "sentiment", "memory_extraction", "quality_eval"]
    reason: str
    priority: Literal["low", "normal", "high"] = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PlanValidation:
    display_matches_spoken: bool
    contains_private_memory: bool
    safe_to_speak: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AssistantPlan:
    assistant_turn_id: str
    display_text: str
    spoken_segments: list[SpokenSegment]
    route: RouteDecision
    validation: PlanValidation
    metrics: dict[str, Any] = field(default_factory=dict)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
    post_call_jobs: list[PostCallJob] = field(default_factory=list)

    def spoken_text(self) -> str:
        return " ".join(
            segment.spoken_text()
            for segment in self.spoken_segments
            if segment.should_speak and segment.spoken_text()
        ).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "assistant_turn_id": self.assistant_turn_id,
            "display_text": self.display_text,
            "spoken_segments": [segment.to_dict() for segment in self.spoken_segments],
            "route": self.route.to_dict(),
            "memory_updates": [update.to_dict() for update in self.memory_updates],
            "post_call_jobs": [job.to_dict() for job in self.post_call_jobs],
            "validation": self.validation.to_dict(),
            "metrics": dict(self.metrics),
        }

