"""Shell runtime package."""

from .entry import run_shell
from .pty.executor import execute_command_with_pty
from .runtime.ai import AIHandler
from .runtime.app import PTYAIShell
from .runtime.events import LLMEventRouter
from .runtime.output import OutputProcessor
from .types import (
    ActionContext,
    ActionOutcome,
    CommandResult,
    CommandStatus,
    InputIntent,
)
from .ui.interaction import PTYUserInteraction

__all__ = [
    "AIHandler",
    "ActionContext",
    "ActionOutcome",
    "CommandResult",
    "CommandStatus",
    "InputIntent",
    "LLMEventRouter",
    "OutputProcessor",
    "PTYUserInteraction",
    "PTYAIShell",
    "execute_command_with_pty",
    "run_shell",
]