"""Fish-style auto-suggestion engine for PTY mode."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..history_manager import HistoryManager


# ANSI escape codes for suggestion display
SUGGESTION_START = "\x1b[90m"  # Dark gray
SUGGESTION_END = "\x1b[0m"     # Reset


class SuggestionEngine:
    """Manages auto-suggestion state and display for PTY mode."""

    def __init__(
        self,
        history_manager: Optional[HistoryManager] = None,
        enabled: bool = True,
    ):
        self._history_manager = history_manager
        self._enabled = enabled
        self._suggestion: str = ""      # The full suggested command
        self._suffix: str = ""          # The part after current input (what's displayed)
        self._displayed_len: int = 0    # Display width of currently shown suggestion

    @property
    def enabled(self) -> bool:
        return (
            self._enabled
            and self._history_manager is not None
            and hasattr(self._history_manager, "search_prefix_sync")
        )

    @property
    def suggestion(self) -> str:
        return self._suggestion

    @property
    def suffix(self) -> str:
        return self._suffix

    def update(self, current_input: str, ai_mode: bool = False) -> None:
        """Search history for a match and display suggestion.

        Args:
            current_input: What the user has typed so far (without ';' prefix in AI mode).
            ai_mode: Whether we're in AI mode.
        """
        if not self.enabled or not current_input.strip():
            self._clear_display()
            self._suggestion = ""
            self._suffix = ""
            return

        # Build the prefix to search for
        if ai_mode:
            search_prefix = ";" + current_input
        else:
            search_prefix = current_input

        # Search history
        search_prefix_sync = getattr(self._history_manager, "search_prefix_sync", None)
        if search_prefix_sync is None:
            self._clear_display()
            self._suggestion = ""
            self._suffix = ""
            return

        match = search_prefix_sync(search_prefix)

        if match and len(match) > len(search_prefix):
            self._suggestion = match
            self._suffix = match[len(search_prefix):]
            self._display()
        else:
            self._clear_display()
            self._suggestion = ""
            self._suffix = ""

    def accept(self) -> str:
        """Accept the current suggestion and return the suffix text.

        Clears the displayed suggestion.
        """
        suffix = self._suffix
        self._clear_display()
        self._suggestion = ""
        self._suffix = ""
        return suffix

    def clear(self) -> None:
        """Clear any displayed suggestion and reset state."""
        self._clear_display()
        self._suggestion = ""
        self._suffix = ""

    def _display(self) -> None:
        """Show suggestion in dark gray after cursor."""
        self._clear_display()
        if self._suffix:
            text = f"{SUGGESTION_START}{self._suffix}{SUGGESTION_END}"
            sys.stdout.write(text)
            sys.stdout.flush()
            self._displayed_len = len(self._suffix)

    def _clear_display(self) -> None:
        """Erase the currently displayed suggestion."""
        if self._displayed_len > 0:
            # Move cursor back, overwrite with spaces, move back again
            sys.stdout.write("\b" * self._displayed_len + " " * self._displayed_len + "\b" * self._displayed_len)
            sys.stdout.flush()
            self._displayed_len = 0
