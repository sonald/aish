"""PTY mode user interaction handler.

This module provides user interaction functions specifically for PTY mode,
where we need to temporarily exit raw mode to get user input.
"""

from __future__ import annotations

import sys
import termios
import tty
from typing import Optional, Tuple


class PTYUserInteraction:
    """Handle user interactions in PTY raw mode.

    When the shell is in raw mode (for PTY passthrough), we need to
    temporarily restore normal terminal mode to get user input.
    """

    def __init__(self, original_termios: Optional[list] = None):
        """Initialize with original terminal settings.

        Args:
            original_termios: Original terminal settings from tcgetattr
        """
        self._original_termios = original_termios
        self._saved_settings: Optional[list] = None

    def _restore_terminal(self) -> None:
        """Temporarily restore normal terminal mode for user interaction."""
        if self._original_termios:
            try:
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._original_termios
                )
                sys.stdout.flush()
            except Exception:
                pass

    def _set_raw_mode(self) -> None:
        """Return to raw mode after user interaction."""
        if self._original_termios:
            try:
                tty.setraw(sys.stdin.fileno())
                sys.stdout.flush()
            except Exception:
                pass

    def get_confirmation(self, prompt: str = "") -> bool:
        """Get Y/n confirmation from user.

        Args:
            prompt: Optional prompt message

        Returns:
            True if user confirmed (Y/y), False otherwise
        """
        if prompt:
            print(f"{prompt}", end="", flush=True)
        else:
            print("\n按 Y 执行，其他键忽略: ", end="", flush=True)

        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            result = ch.lower() == "y"
            if result:
                print("Y")
            else:
                print()
            return result
        except Exception:
            print()
            return False

    def request_choice(
        self,
        message: str,
        options: list[dict],
        allow_custom_input: bool = False,
    ) -> Tuple[Optional[str], str]:
        """Request user choice from options.

        Args:
            message: Prompt message
            options: List of option dicts with 'id', 'label', 'description' keys
            allow_custom_input: Whether to allow custom text input

        Returns:
            Tuple of (option_id or None, custom_input or empty string)
        """
        print(f"\n{message}\n")

        for i, opt in enumerate(options, 1):
            label = opt.get("label", opt.get("id", ""))
            desc = opt.get("description", "")
            if desc:
                print(f"  {i}. {label} - {desc}")
            else:
                print(f"  {i}. {label}")

        if allow_custom_input:
            print("  0. Other (custom input)")

        print(f"\n选择 (1-{len(options)}): ", end="", flush=True)

        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                line = ""
                while True:
                    ch = sys.stdin.read(1)
                    if ch == "\r" or ch == "\n":
                        break
                    if ch == "\x03":  # Ctrl+C
                        print("^C")
                        return None, ""
                    if ch == "\x7f" or ch == "\x08":  # Backspace
                        if line:
                            line = line[:-1]
                            print("\b \b", end="", flush=True)
                    elif ch.isdigit():
                        line += ch
                        print(ch, end="", flush=True)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            print()

            if not line:
                return None, ""

            choice = int(line)
            if choice == 0 and allow_custom_input:
                print("请输入: ", end="", flush=True)
                custom = input()
                return None, custom
            if 1 <= choice <= len(options):
                selected = options[choice - 1]
                return selected.get("value") or selected.get("id"), ""
            return None, ""
        except (ValueError, EOFError):
            print()
            return None, ""
        except Exception:
            print()
            return None, ""
