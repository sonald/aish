"""prompt_toolkit-backed editing session for the shell frontend."""

from __future__ import annotations

import os
from html import escape
from typing import TYPE_CHECKING, Callable, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.auto_suggest import (
    load_auto_suggest_bindings,
)
if TYPE_CHECKING:
    from prompt_toolkit.buffer import Buffer

    from ...history_manager import HistoryManager
    from ...interruption import InterruptionManager


class HistoryAutoSuggest(AutoSuggest):
    """Auto-suggest shell input from persisted command history."""

    def __init__(self, history_manager: Optional["HistoryManager"] = None):
        self._history_manager = history_manager

    def get_suggestion(self, buffer: "Buffer", document) -> Suggestion | None:
        _ = buffer
        if self._history_manager is None:
            return None

        current = document.text_before_cursor
        if not current.strip():
            return None

        search_prefix = current
        match = self._history_manager.search_prefix_sync(search_prefix)
        if not match or len(match) <= len(search_prefix):
            return None

        return Suggestion(match[len(search_prefix) :])


class ShellPromptController:
    """Own the editing-mode PromptSession and lightweight UI state."""

    def __init__(
        self,
        history_manager: Optional["HistoryManager"] = None,
        interruption_manager: Optional["InterruptionManager"] = None,
        on_buffer_change: Optional[Callable[[str], None]] = None,
        cwd_provider: Optional[Callable[[], str]] = None,
    ):
        self._history_manager = history_manager
        self._interruption_manager = interruption_manager
        self._on_buffer_change = on_buffer_change
        self._cwd_provider = cwd_provider
        self._history = InMemoryHistory()
        self._load_history()
        self._session = PromptSession(
            history=self._history,
            auto_suggest=HistoryAutoSuggest(history_manager),
            key_bindings=self._build_key_bindings(),
            mouse_support=False,
        )
        if hasattr(self._session, "app") and hasattr(self._session.app, "output"):
            output = self._session.app.output
            if hasattr(output, "enable_cpr"):
                setattr(output, "enable_cpr", False)

    def prompt(self) -> str:
        """Read one editing-mode line from the terminal."""

        def _pre_run() -> None:
            app = self._session.app
            buffer = app.current_buffer
            self._notify_buffer_change(buffer.text)

            def _handle_change(_buffer) -> None:
                self._notify_buffer_change(buffer.text)

            buffer.on_text_changed += _handle_change

        return self._session.prompt(
            self._build_prompt_message(),
            pre_run=_pre_run,
        )

    def remember_command(self, command: str) -> None:
        """Append a submitted command to prompt-toolkit's local history."""
        command = str(command or "").strip()
        if not command:
            return
        self._history.append_string(command)

    def _load_history(self) -> None:
        if self._history_manager is None:
            return
        commands = self._history_manager.get_recent_commands_sync()
        for command in commands:
            self._history.append_string(command)

    def _build_key_bindings(self):
        bindings = KeyBindings()
        return merge_key_bindings([bindings, load_auto_suggest_bindings()])

    def _notify_buffer_change(self, text: str) -> None:
        if self._on_buffer_change is not None:
            self._on_buffer_change(text)

    def _get_prompt_text(self) -> str:
        cwd = None
        if self._cwd_provider is not None:
            try:
                cwd = self._cwd_provider()
            except Exception:
                cwd = None

        cwd_text = str(cwd or os.getcwd())
        home = os.path.expanduser("~")
        if cwd_text == home:
            return "~"
        if cwd_text.startswith(home + os.sep):
            return "~" + cwd_text[len(home) :]
        return cwd_text

    def _build_prompt_message(self) -> HTML:
        prompt_text = escape(self._get_prompt_text())
        return HTML(f"<ansiblue>{prompt_text}</ansiblue> <ansicyan>&gt;</ansicyan> ")

    def _get_bottom_toolbar(self):
        hint = None
        if self._interruption_manager is not None:
            hint = self._interruption_manager.get_prompt_message()

        if not hint:
            return None

        hint = hint.replace("&lt;", "<").replace("&gt;", ">")
        if "<gray>" in hint and "</gray>" in hint:
            start = hint.find("<gray>") + 6
            end = hint.find("</gray>")
            hint = hint[start:end]
        return HTML(escape(hint))