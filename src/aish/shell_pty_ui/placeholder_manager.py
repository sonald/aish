"""Placeholder manager for PTY mode.

Manages display and clearing of placeholder text after bash prompt.
"""

from __future__ import annotations

from typing import Optional
from wcwidth import wcwidth

from ..i18n import t
from ..interruption import InterruptionManager, ShellState


class PlaceholderManager:
    """Manage placeholder display in PTY mode.

    Displays gray hint text after bash prompt that disappears when
    user starts typing. Content is context-aware based on shell state.
    """

    # ANSI escape sequences
    GRAY = "\x1b[90m"
    RESET = "\x1b[0m"

    def __init__(self, interruption_manager: InterruptionManager):
        """Initialize PlaceholderManager.

        Args:
            interruption_manager: Used to determine placeholder content
        """
        self._interruption_manager = interruption_manager
        self._current_placeholder: Optional[str] = None
        self._placeholder_visible = False
        self._cleared_for_current_line = False

    def get_placeholder_text(self) -> Optional[str]:
        """Get placeholder text based on current shell state.

        Returns:
            Placeholder text string, or None if no placeholder should be shown
        """
        # Check InterruptionManager state first
        state = self._interruption_manager.state

        # Don't show placeholder during AI operations
        if state in (
            ShellState.AI_THINKING,
            ShellState.SANDBOX_EVAL,
            ShellState.COMMAND_EXEC,
        ):
            return None

        # Don't show if user is already inputting
        if state == ShellState.INPUTTING:
            return None

        # Don't show for correct pending (left prompt only)
        if state == ShellState.CORRECT_PENDING:
            return None

        # Check for state-specific messages from InterruptionManager
        prompt_message = self._interruption_manager.get_prompt_message()
        if prompt_message:
            # Extract text from HTML-like format
            # Format: <gray>&lt;message&gt;</gray>
            if "<gray>" in prompt_message and "</gray>" in prompt_message:
                start = prompt_message.find("<gray>") + 6
                end = prompt_message.find("</gray>")
                content = prompt_message[start:end]
                # Unescape HTML entities
                content = content.replace("&lt;", "<").replace("&gt;", ">")
                return content

        # Default: show AI hint
        return t("shell.prompt.ai_hint")

    def show_placeholder(self) -> bytes:
        """Generate ANSI sequence to show placeholder.

        The placeholder is displayed in gray, then the cursor is moved back
        to the start of the placeholder so user input overwrites it.

        Returns:
            Bytes to write to stdout to display the placeholder
        """
        text = self.get_placeholder_text()
        if not text:
            self._placeholder_visible = False
            return b""

        self._current_placeholder = text
        self._placeholder_visible = True
        self._cleared_for_current_line = False

        # Calculate display width using wcwidth
        width = 0
        for char in text:
            w = wcwidth(char)
            if w < 0:
                w = 1  # Default to 1 for unprintable chars
            width += w

        # Generate ANSI sequence: GRAY + text + RESET + move_cursor_back
        # Moving cursor back makes input start from placeholder beginning
        sequence = f"{self.GRAY}{text}{self.RESET}\x1b[{width}D"
        return sequence.encode()

    def clear_placeholder(self) -> bytes:
        """Generate ANSI sequence to clear placeholder.

        When cursor is at placeholder start (after show_placeholder), we need to:
        1. Move cursor forward to overwrite the gray text
        2. Use backspace to clear

        Returns:
            Bytes to write to stdout to clear the placeholder
        """
        if not self._placeholder_visible or not self._current_placeholder:
            return b""

        text = self._current_placeholder
        # Calculate display width using wcwidth
        width = 0
        for char in text:
            w = wcwidth(char)
            if w < 0:
                w = 1  # Default to 1 for unprintable chars
            width += w

        # Since cursor is at start, move forward to overwrite gray text,
        # then backspace to clear
        clear_sequence = f"\x1b[{width}C" + ("\b \b" * width)
        self._placeholder_visible = False
        self._current_placeholder = None
        return clear_sequence.encode()

    def mark_cleared(self) -> None:
        """Mark placeholder as cleared for current line."""
        self._cleared_for_current_line = True
        self._placeholder_visible = False

    def reset_for_new_line(self) -> None:
        """Reset state for new prompt line."""
        self._placeholder_visible = False
        self._current_placeholder = None
        self._cleared_for_current_line = False

    def is_visible(self) -> bool:
        """Check if placeholder is currently visible."""
        return self._placeholder_visible

    def is_cleared(self) -> bool:
        """Check if placeholder was cleared for current line."""
        return self._cleared_for_current_line
