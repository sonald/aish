"""Exit code tracking via PROMPT_COMMAND marker."""

import re
from typing import Optional, Tuple


class ExitCodeTracker:
    """Track bash command exit codes using PROMPT_COMMAND marker.

    The marker format is: [AISH_EXIT:N] where N is the exit code.
    This is injected via PROMPT_COMMAND in bash initialization.
    """

    # Pattern to match exit code marker
    MARKER_PATTERN = re.compile(rb"\[AISH_EXIT:(-?\d+)\]")

    def __init__(self):
        self._last_exit_code: int = 0
        self._last_command: str = ""
        self._has_error: bool = False
        self._exit_code_available: bool = False

    @property
    def last_exit_code(self) -> int:
        """Get the last command's exit code."""
        return self._last_exit_code

    @property
    def has_error(self) -> bool:
        """Check if last command had non-zero exit code."""
        return self._has_error

    @property
    def last_command(self) -> str:
        """Get the last executed command."""
        return self._last_command

    def set_last_command(self, command: str) -> None:
        """Set the command that's about to be executed."""
        self._last_command = command

    def parse_and_update(self, data: bytes) -> bytes:
        """Parse exit code marker from PTY output, update state, return cleaned output.

        Args:
            data: Raw PTY output bytes

        Returns:
            Cleaned output with exit code markers removed
        """
        # Find all markers in the output
        markers = list(self.MARKER_PATTERN.finditer(data))

        if markers:
            # Get the last marker's exit code
            last_marker = markers[-1]
            exit_code = int(last_marker.group(1))
            self._last_exit_code = exit_code
            self._has_error = exit_code != 0
            self._exit_code_available = True  # Mark that exit code is available

            # Remove all markers from output
            cleaned = self.MARKER_PATTERN.sub(b"", data)
            return cleaned

        return data

    def consume_error(self) -> Optional[Tuple[str, int]]:
        """Consume and return error info if there was an error.

        Returns:
            (command, exit_code) if error existed, None otherwise
        """
        if self._has_error:
            cmd = self._last_command
            code = self._last_exit_code
            self._has_error = False
            return cmd, code
        return None

    def consume_exit_code(self) -> Optional[Tuple[str, int]]:
        """Consume and return exit code info if a command completed.

        Returns:
            (command, exit_code) if a command completed (success or error), None otherwise
        """
        if self._exit_code_available:
            cmd = self._last_command
            code = self._last_exit_code
            self._last_command = ""  # Clear command after consuming
            self._exit_code_available = False  # Clear the flag
            return cmd, code
        return None

    def clear_exit_available(self) -> None:
        """Clear exit code available flag without consuming command info.

        Use this to discard stale exit code state (e.g. before a new command)
        while preserving _last_command for error correction features.
        """
        self._exit_code_available = False

    def has_exit_code(self) -> bool:
        """Check if a command completed and exit code is available.

        Returns:
            True if a command completed (exit code is available)
        """
        # Exit code is available after parse_and_update is called with a marker
        # We use a private flag to track this state
        return hasattr(self, '_exit_code_available') and self._exit_code_available

    def reset(self) -> None:
        """Reset all state."""
        self._last_exit_code = 0
        self._last_command = ""
        self._has_error = False
        self._exit_code_available = False
