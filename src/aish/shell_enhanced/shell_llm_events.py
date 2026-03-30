"""LLM event routing utilities for the shell core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..llm import LLMCallbackResult, LLMEvent, LLMEventType


@dataclass
class LLMEventRouter:
    """Simple event router to decouple shell facade from handler map."""

    handlers: dict[LLMEventType, Callable[[LLMEvent], object | None]] = field(
        default_factory=dict
    )

    def handle(self, event: LLMEvent) -> LLMCallbackResult:
        handler = self.handlers.get(event.event_type)
        if not handler:
            return LLMCallbackResult.CONTINUE

        result = handler(event)
        if event.event_type in {
            LLMEventType.TOOL_CONFIRMATION_REQUIRED,
            LLMEventType.INTERACTION_REQUIRED,
        }:
            if isinstance(result, LLMCallbackResult):
                return result
            return LLMCallbackResult.CONTINUE
        return LLMCallbackResult.CONTINUE
