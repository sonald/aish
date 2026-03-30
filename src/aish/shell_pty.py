"""AI Shell using direct PTY architecture with AI integration.

Design:
- bash runs in PTY with complete terminal control
- InputRouter intercepts stdin to detect ';' at line start
- ';' triggers AI features (question or error correction)
- Otherwise stdin passes through to PTY
- Perfect interactive command support (vim, top, less, etc.)
"""

from __future__ import annotations

import anyio
import asyncio
import os
import re
import select
import shutil
import signal
import sys
import termios
import threading
import time
import tty
from typing import TYPE_CHECKING, Optional, Any

from rich.console import Console
from rich.live import Live
from wcwidth import wcwidth

from .config import Config, ConfigModel, ToolArgPreviewSettings
from .pty import PTYManager
from .welcome_screen import build_welcome_renderable
from .context_manager import ContextManager, MemoryType
from .prompts import PromptManager
from .cancellation import CancellationToken
from .i18n import t
from .skills.hotreload import SkillHotReloadService
from .shell_enhanced.shell_llm_events import LLMEventRouter
from .shell_enhanced.shell_prompt_io import (
    display_security_panel,
    get_user_confirmation,
)
from .shell_pty_ui import PTYUserInteraction, PlaceholderManager
from .shell_enhanced.suggestion_engine import SuggestionEngine
from .utils import get_or_fetch_static_env_info, get_current_env_info, get_output_language

if TYPE_CHECKING:
    from .llm import LLMSession, LLMEventType
    from .skills import SkillManager
    from .interruption import InterruptionManager, InterruptAction, ShellState
else:
    from .llm import LLMEventType
    from .interruption import InterruptionManager, InterruptAction, ShellState


class InputRouter:
    """Route user input to PTY or AI handler.

    Detects ';' at line start to trigger AI mode.
    """

    # Semicolon marks from various languages
    SEMICOLON_MARKS = frozenset(
        {
            ";",  # Latin Semicolon (U+003B)
            "；",  # Fullwidth Semicolon (U+FF1B) - Chinese, Japanese, Korean
        }
    )

    # Zero-width and invisible characters that may be inserted by some IMEs
    # These should be skipped when checking for AI prefix marks
    # Reference: https://unicode.org/reports/tr44/#Default_Ignorable_Code_Point
    INVISIBLE_CHARS = frozenset(
        {
            # Zero-width characters
            "\u200b",  # Zero Width Space
            "\u200c",  # Zero Width Non-Joiner
            "\u200d",  # Zero Width Joiner
            # Directional marks
            "\u200e",  # Left-to-Right Mark
            "\u200f",  # Right-to-Left Mark
            "\u061c",  # Arabic Letter Mark
            # BOM and special spaces
            "\ufeff",  # BOM / Zero Width No-Break Space
            "\u00ad",  # Soft Hyphen
            "\u180e",  # Mongolian Vowel Separator
            # Invisible operators
            "\u2060",  # Word Joiner
            "\u2061",  # Function Application
            "\u2062",  # Invisible Times
            "\u2063",  # Invisible Separator
            "\u2064",  # Invisible Plus
            # Bidirectional isolate markers
            "\u2066",  # Left-to-Right Isolate
            "\u2067",  # Right-to-Left Isolate
            "\u2068",  # First Strong Isolate
            "\u2069",  # Pop Directional Isolate
            # Deprecated formatting characters
            "\u206a",  # Inhibit Symmetric Swapping
            "\u206b",  # Activate Symmetric Swapping
            "\u206c",  # Inhibit Arabic Form Shaping
            "\u206d",  # Activate Arabic Form Shaping
            "\u206e",  # National Digit Shapes
            "\u206f",  # Nominal Digit Shapes
            # Combining marks
            "\u034f",  # Combining Grapheme Joiner
            # Khmer vowels
            "\u17b4",  # Khmer Vowel Inherent Aq
            "\u17b5",  # Khmer Vowel Inherent Aa
        }
    )

    # ANSI escape sequence start
    ESC = "\x1b"

    def __init__(
        self,
        pty_manager: PTYManager,
        ai_handler: "AIHandler",
        output_processor: Optional["OutputProcessor"] = None,
        placeholder_manager: Optional[PlaceholderManager] = None,
        interruption_manager: Optional[InterruptionManager] = None,
        history_manager=None,
    ):
        self.pty_manager = pty_manager
        self.ai_handler = ai_handler
        self.output_processor = output_processor
        self.placeholder_manager = placeholder_manager
        self.interruption_manager = interruption_manager
        self._buffer = ""
        self._at_line_start = True
        self._in_ai_mode = False
        self._ai_buffer = ""
        # Track current command for exit code tracking
        self._current_cmd = ""
        # Bracketed paste mode state
        self._in_bracketed_paste = False
        self._paste_buffer = b""
        # Track if placeholder has been cleared for current line
        self._placeholder_cleared = False
        # Timer for auto-refreshing placeholder after Ctrl+C timeout
        self._placeholder_refresh_timer: Optional[threading.Timer] = None
        # Auto-suggestion engine
        self._suggestion_engine = SuggestionEngine(history_manager=history_manager)

    def handle_input(self, data: bytes) -> None:
        """Process input bytes and route to PTY or AI."""
        # Handle bracketed paste mode
        if self._in_bracketed_paste:
            # Check for end of bracketed paste: \x1b[201~
            if b"\x1b[201~" in data:
                # Split at the end marker
                parts = data.split(b"\x1b[201~", 1)
                self._paste_buffer += parts[0]
                self._in_bracketed_paste = False
                # Process the pasted content
                pasted = self._paste_buffer
                self._paste_buffer = b""
                if pasted:
                    self._process_normal_input(pasted)
                # Process any remaining data after the end marker
                if parts[1]:
                    self.handle_input(parts[1])
                return
            else:
                # Accumulate pasted content
                self._paste_buffer += data
                return

        # Check for start of bracketed paste: \x1b[200~
        if data.startswith(b"\x1b[200~"):
            self._in_bracketed_paste = True
            self._paste_buffer = b""
            # Check if there's more data after the start marker
            remaining = data[6:]  # Skip \x1b[200~
            if remaining:
                self.handle_input(remaining)
            return

        # Check for other ANSI escape sequences (arrow keys, function keys, etc.)
        if len(data) > 0 and data[0] == 0x1B:
            # Check for Right arrow (\x1b[C) to accept auto-suggestion
            if not self._in_ai_mode and data == b"\x1b[C":
                suffix = self._suggestion_engine.accept()
                if suffix:
                    # Send the suggestion text to PTY so bash receives it
                    self.pty_manager.send(suffix.encode("utf-8"))
                    self._current_cmd += suffix
                    return
            if self._in_ai_mode:
                # In AI mode, ignore arrow keys and editing keys
                return
            self.pty_manager.send(data)
            return

        # Normal input processing
        self._process_normal_input(data)

    def _process_normal_input(self, data: bytes) -> None:
        """Process normal (non-escape) input."""
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            # Fallback: forward raw bytes to PTY
            self.pty_manager.send(data)
            return

        for char in text:
            self._handle_char(char)

    def _handle_char(self, char: str) -> None:
        """Handle a single character."""
        # Clear placeholder on first character input (skip control chars)
        # Skip these control characters that shouldn't trigger clear
        SKIP_CLEAR_CHARS = {
            "\x01",  # Ctrl+A
            "\x03",  # Ctrl+C
            "\x04",  # Ctrl+D
            "\x05",  # Ctrl+E
            "\x07",  # Ctrl+G
            "\x08",  # Backspace (BS)
            "\x09",  # Tab
            "\x0b",  # Ctrl+K
            "\x0c",  # Ctrl+L
            "\x0e",  # Ctrl+N
            "\x0f",  # Ctrl+O
            "\x10",  # Ctrl+P
            "\x1b",  # ESC
            "\x7f",  # Delete (DEL)
        }

        if (
            not self._placeholder_cleared
            and self.placeholder_manager
            and char not in SKIP_CLEAR_CHARS
        ):
            self._clear_placeholder()

        # Check for Enter key (end of input)
        if char in ("\n", "\r"):
            if self._in_ai_mode:
                self._suggestion_engine.clear()
                # Complete AI input
                self._process_ai_input()
                self._in_ai_mode = False
                self._ai_buffer = ""
                self._at_line_start = True
            else:
                self._suggestion_engine.clear()
                # Set last command before sending Enter
                if self._current_cmd.strip():
                    cmd_stripped = self._current_cmd.strip()
                    self.pty_manager.exit_tracker.set_last_command(cmd_stripped)
                    # Set waiting for result flag, but not for exit commands
                    # Exit commands should not trigger error hints
                    is_exit_cmd = cmd_stripped in (
                        "exit",
                        "logout",
                    ) or cmd_stripped.startswith(("exit ", "logout "))
                    if self.output_processor:
                        if not is_exit_cmd:
                            self.output_processor.set_waiting_for_result(True)
                        else:
                            # Filter exit command echo in PTY output
                            self.output_processor.set_filter_exit_echo(True)
                # Forward to PTY - always send \r for Enter in raw mode
                self.pty_manager.send(b"\r")
                self._at_line_start = True
                self._current_cmd = ""

            # Reset placeholder state for new line
            if self.placeholder_manager:
                self.placeholder_manager.reset_for_new_line()
            self._placeholder_cleared = False
            return

        # Check for Ctrl+C
        if char == "\x03":
            self._suggestion_engine.clear()
            if self._in_ai_mode:
                # Cancel AI input
                self._in_ai_mode = False
                self._ai_buffer = ""
                self._at_line_start = True
                # Print cancellation message
                sys.stdout.write("\r\n^C\r\n")
                sys.stdout.flush()
                return
            elif self.interruption_manager:
                # Use InterruptionManager for double Ctrl+C exit logic
                has_input = bool(self._current_cmd.strip())
                action = self.interruption_manager.handle_ctrl_c(has_input)

                # Clear current placeholder before forwarding Ctrl+C
                if self.placeholder_manager and self.placeholder_manager.is_visible():
                    clear_seq = self.placeholder_manager.clear_placeholder()
                    sys.stdout.buffer.write(clear_seq)
                    sys.stdout.buffer.flush()

                # Forward \x03 to PTY so bash receives it properly
                self.pty_manager.send(char.encode())
                self._current_cmd = ""

                # Handle the action
                if action == InterruptAction.CONFIRM_EXIT:
                    # Second Ctrl+C within window -> exit
                    # Cancel any pending refresh timer
                    if self._placeholder_refresh_timer:
                        self._placeholder_refresh_timer.cancel()
                        self._placeholder_refresh_timer = None
                    shell = self.ai_handler.shell if self.ai_handler else None
                    if shell is not None:
                        shell._running = False
                elif action == InterruptAction.REQUEST_EXIT:
                    # First Ctrl+C - placeholder will be shown after bash redisplays prompt
                    # Schedule auto-refresh after EXIT_WINDOW (1.5s)
                    if self.interruption_manager:
                        self._schedule_placeholder_refresh(self.interruption_manager.EXIT_WINDOW)
                return
            else:
                # Fallback: forward to PTY
                self.pty_manager.send(char.encode())
                self._current_cmd = ""
                return

        # Check for backspace in AI mode
        if char in ("\x7f", "\x08"):  # DEL or BS
            if self._in_ai_mode:
                if self._ai_buffer:
                    # Get the last character and its display width
                    last_char = self._ai_buffer[-1]
                    self._ai_buffer = self._ai_buffer[:-1]
                    # Calculate display width (default to 1 for control chars)
                    char_width = wcwidth(last_char)
                    if char_width < 1:
                        char_width = 1
                    # Echo backspace with correct width
                    # Each cell needs: backspace, space, backspace
                    sys.stdout.write("\b \b" * char_width)
                    sys.stdout.flush()
                    # Update suggestion after backspace
                    self._suggestion_engine.update(self._ai_buffer, ai_mode=True)
                else:
                    # _ai_buffer is empty, delete the semicolon and exit AI mode
                    # Semicolon could be fullwidth (width 2) or ASCII (width 1)
                    # We need to track which semicolon was used
                    semicolon_width = getattr(self, '_semicolon_width', 1)
                    sys.stdout.write("\b \b" * semicolon_width)
                    sys.stdout.flush()
                    self._suggestion_engine.clear()
                    self._in_ai_mode = False
                    self._at_line_start = True
                return
            else:
                self.pty_manager.send(char.encode())
                # Remove last char from command buffer
                if self._current_cmd:
                    self._current_cmd = self._current_cmd[:-1]
                    # Reset _at_line_start when command buffer becomes empty
                    if not self._current_cmd:
                        self._at_line_start = True
                    # Update suggestion after backspace
                    self._suggestion_engine.update(self._current_cmd)
                return

        # At line start, check for semicolon (both ASCII and fullwidth)
        # Skip invisible characters that may be inserted by IMEs
        if self._at_line_start:
            # Skip invisible characters - don't affect line start state
            if char in self.INVISIBLE_CHARS:
                return
            if char in self.SEMICOLON_MARKS:
                self._in_ai_mode = True
                self._ai_buffer = ""
                self._at_line_start = False
                # Store semicolon width for backspace handling
                self._semicolon_width = wcwidth(char)
                if self._semicolon_width < 1:
                    self._semicolon_width = 1
                # Echo the semicolon
                sys.stdout.write(char)
                sys.stdout.flush()
                return
            elif char == self.ESC:
                # Start of escape sequence - forward to PTY
                self.pty_manager.send(char.encode())
                return
            elif char == "\x01":  # Ctrl+A (beginning of line) - keep at line start
                self.pty_manager.send(char.encode())
                return

        # In AI mode, collect input
        if self._in_ai_mode:
            self._ai_buffer += char
            # Echo the character
            sys.stdout.write(char)
            sys.stdout.flush()
            self._suggestion_engine.update(self._ai_buffer, ai_mode=True)
            return

        # Normal mode: forward to PTY and collect command
        self.pty_manager.send(char.encode())
        self._current_cmd += char
        self._at_line_start = False
        self._suggestion_engine.update(self._current_cmd)

    def _process_ai_input(self) -> None:
        """Process collected AI input."""
        if not self._ai_buffer.strip():
            # Just ';' alone - error correction
            self.ai_handler.handle_error_correction()
        else:
            # ';' followed by question
            self.ai_handler.handle_question(self._ai_buffer)

    def _clear_placeholder(self) -> None:
        """Clear placeholder if visible."""
        if self.placeholder_manager and self.placeholder_manager.is_visible():
            clear_seq = self.placeholder_manager.clear_placeholder()
            sys.stdout.buffer.write(clear_seq)
            sys.stdout.buffer.flush()
            self.placeholder_manager.mark_cleared()
            self._placeholder_cleared = True

    def _schedule_placeholder_refresh(self, delay_seconds: float) -> None:
        """Schedule a placeholder refresh after Ctrl+C timeout."""
        # Cancel any existing timer
        if self._placeholder_refresh_timer:
            self._placeholder_refresh_timer.cancel()

        # Create new timer
        self._placeholder_refresh_timer = threading.Timer(
            delay_seconds,
            self._refresh_placeholder_after_timeout
        )
        self._placeholder_refresh_timer.daemon = True
        self._placeholder_refresh_timer.start()

    def _refresh_placeholder_after_timeout(self) -> None:
        """Refresh placeholder after timeout without newline.

        Strategy: Clear current placeholder and display new one directly,
        without sending anything to PTY (which would cause newline).
        """
        if not self.interruption_manager or not self.placeholder_manager:
            self._placeholder_refresh_timer = None
            return

        # Force state back to NORMAL
        from .interruption import ShellState
        if self.interruption_manager.state != ShellState.NORMAL:
            self.interruption_manager.set_state(ShellState.NORMAL)
            self.interruption_manager.clear_prompt()

        # Clear current placeholder if visible
        try:
            if self.placeholder_manager.is_visible():
                clear_seq = self.placeholder_manager.clear_placeholder()
                sys.stdout.buffer.write(clear_seq)
                sys.stdout.buffer.flush()
        except Exception:
            pass

        # Show new placeholder (NORMAL state)
        try:
            show_seq = self.placeholder_manager.show_placeholder()
            if show_seq:
                sys.stdout.buffer.write(show_seq)
                sys.stdout.buffer.flush()
        except Exception:
            pass

        self._placeholder_refresh_timer = None


class AIHandler:
    """Handle AI questions and error correction using LLMSession directly."""

    def __init__(
        self,
        pty_manager: PTYManager,
        llm_session: "LLMSession",
        prompt_manager: PromptManager,
        context_manager: ContextManager,
        skill_manager: SkillManager,
        user_interaction: PTYUserInteraction,
        original_termios: Optional[list] = None,
    ):
        self.pty_manager = pty_manager
        self.llm_session = llm_session
        self.prompt_manager = prompt_manager
        self.context_manager = context_manager
        self.skill_manager = skill_manager
        self.user_interaction = user_interaction
        self._original_termios = original_termios
        self.shell = None  # Set by _setup_components

    def _restore_terminal_for_output(self) -> None:
        """Temporarily restore terminal settings for AI output."""
        if self._original_termios:
            try:
                import termios

                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._original_termios
                )
            except Exception:
                pass
        sys.stdout.flush()

    def _set_raw_mode(self) -> None:
        """Re-enter raw mode after AI output."""
        if self._original_termios:
            try:
                import tty

                tty.setraw(sys.stdin.fileno())
            except Exception:
                pass

    @staticmethod
    def _try_parse_json_output(response: str) -> Optional[dict]:
        """Try to parse response as JSON command."""
        import json

        # Try to extract JSON from markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to parse entire response as JSON
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _run_async_in_thread(coro):
        """Run an async function in a separate thread with its own event loop.

        Avoids nested anyio.run() errors when called from within
        an existing event loop (e.g. shell_pty main loop).
        """
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(anyio.run, coro).result()

    # Skill reference extraction regex (from the legacy shell core)
    _SKILL_REF_EXTRACT_RE = re.compile(r'@(\w+)')

    def _extract_skill_refs(self, text: str) -> list[str]:
        """Extract skill references from text."""
        available = {skill.metadata.name for skill in self.skill_manager.list_skills()}
        if not available:
            return []
        refs: list[str] = []
        seen: set[str] = set()
        for match in self._SKILL_REF_EXTRACT_RE.findall(text):
            name = match.lower()
            if name in available and name not in seen:
                refs.append(name)
                seen.add(name)
        return refs

    def _inject_skill_prefix(self, text: str) -> str:
        """Inject skill prefix into text."""
        refs = self._extract_skill_refs(text)
        if not refs:
            return text
        prefix = " ".join([f"use {name} skill to do this." for name in refs])
        return f"{prefix}\n\n{text}"

    def handle_error_correction(self) -> None:
        """Handle error correction."""
        import anyio

        tracker = self.pty_manager.exit_tracker

        if tracker.last_exit_code == 0:
            print("\r\033[KNo previous error to fix.")
            self._trigger_prompt_redraw()
            self._set_raw_mode()
            return

        cmd = tracker.last_command
        if not cmd:
            print("\r\033[KNo previous command to fix.")
            self._trigger_prompt_redraw()
            self._set_raw_mode()
            return

        try:
            self._restore_terminal_for_output()

            async def _fix():
                with self.llm_session.cancellation_token.open_cancel_scope():
                    system_message = self.prompt_manager.substitute_template(
                        "cmd_error",
                        user_nickname=os.getenv("USER", "user"),
                        uname_info=getattr(self, 'uname_info', ''),
                        os_info=getattr(self, 'os_info', ''),
                        basic_env_info=getattr(self, 'basic_env_info', ''),
                        output_language=getattr(self, 'output_language', 'en'),
                    )

                    prompt = f"""<command_result>
Command: {cmd}
Exit code: {tracker.last_exit_code}
</command_result>"""

                    response = await self.llm_session.completion(
                        prompt,
                        system_message=system_message,
                        emit_events=True,
                        stream=True
                    )
                    return response

            self.llm_session.reset_cancellation_token()
            self.shell._user_requested_exit = False

            from .interruption import ShellState
            self.shell.interruption_manager.set_state(ShellState.AI_THINKING)
            self.shell.operation_in_progress = True

            try:
                response = self._run_async_in_thread(_fix)
            except (
                anyio.get_cancelled_exc_class(),
                asyncio.CancelledError,
                KeyboardInterrupt,
            ):
                self.shell.handle_processing_cancelled()
                return
            finally:
                self.shell.interruption_manager.set_state(ShellState.NORMAL)
                self.shell.operation_in_progress = False

            executed_cmd = False
            if response:
                corrected_cmd = self._display_ai_response(response)

                if corrected_cmd:
                    executed_cmd = self._ask_execute_command(corrected_cmd)

            if not executed_cmd:
                self._trigger_prompt_redraw()
                self.pty_manager.send(b"\n")
                # Wait for PTY output and filter empty lines
                import time
                max_wait = 0.2
                start_wait = time.time()
                while (time.time() - start_wait) < max_wait:
                    ready, _, _ = select.select([self.pty_manager._master_fd], [], [], 0.05)
                    if ready:
                        try:
                            data = os.read(self.pty_manager._master_fd, 4096)
                            if data:
                                cleaned = self.pty_manager.exit_tracker.parse_and_update(data)
                                cleaned = cleaned.lstrip(b"\r\n")
                                if cleaned:
                                    sys.stdout.buffer.write(cleaned)
                                    sys.stdout.buffer.flush()
                        except OSError:
                            break

            self._set_raw_mode()

        except Exception as e:
            print(f"\r\033[KError: {e}")
            self._set_raw_mode()

    def handle_question(self, question: str) -> None:
        """Handle AI question."""
        import anyio

        try:
            self._restore_terminal_for_output()

            async def _ask():
                with self.llm_session.cancellation_token.open_cancel_scope():
                    system_message = self.prompt_manager.substitute_template(
                        "oracle",
                        user_nickname=os.getenv("USER", "user"),
                        uname_info=getattr(self, 'uname_info', ''),
                        os_info=getattr(self, 'os_info', ''),
                        basic_env_info=getattr(self, 'basic_env_info', ''),
                        output_language=getattr(self, 'output_language', 'en'),
                    )

                    # Inject skill prefix
                    question_processed = self._inject_skill_prefix(question)

                    response = await self.llm_session.process_input(
                        question_processed,
                        context_manager=self.context_manager,
                        system_message=system_message,
                        stream=True,
                    )
                    return response

            # Reset cancellation token for new AI interaction
            self.llm_session.reset_cancellation_token()
            self.shell._user_requested_exit = False

            from .interruption import ShellState
            self.shell.interruption_manager.set_state(ShellState.AI_THINKING)
            self.shell.operation_in_progress = True

            try:
                response = self._run_async_in_thread(_ask)
            except (
                anyio.get_cancelled_exc_class(),
                asyncio.CancelledError,
                KeyboardInterrupt,
            ):
                self.shell.handle_processing_cancelled()
                return
            finally:
                self.shell.interruption_manager.set_state(ShellState.NORMAL)
                self.shell.operation_in_progress = False

            if response:
                self._display_ai_response(response)

            self._trigger_prompt_redraw()
            self.pty_manager.send(b"\n")

            # Wait for PTY output
            max_wait = 0.2
            start_wait = time.time()
            while (time.time() - start_wait) < max_wait:
                ready, _, _ = select.select([self.pty_manager._master_fd], [], [], 0.05)
                if ready:
                    try:
                        data = os.read(self.pty_manager._master_fd, 4096)
                        if data:
                            cleaned = self.pty_manager.exit_tracker.parse_and_update(data)
                            cleaned = cleaned.lstrip(b"\r\n")
                            if cleaned:
                                sys.stdout.buffer.write(cleaned)
                                sys.stdout.buffer.flush()
                    except OSError:
                        break

            self._set_raw_mode()

        except Exception as e:
            print(f"\r\033[KError: {e}")
            self._set_raw_mode()

    def _trigger_prompt_redraw(self) -> None:
        """Trigger bash to redraw its prompt by sending SIGWINCH."""
        if self.pty_manager._child_pid:
            try:
                os.kill(self.pty_manager._child_pid, signal.SIGWINCH)
            except Exception:
                pass

    def _display_ai_response(self, response: str) -> Optional[str]:
        """Display AI response, handling JSON command format.

        Returns the corrected command if available, None otherwise.
        """
        from rich.box import HORIZONTALS
        from rich.panel import Panel
        from rich.markdown import Markdown
        from rich.console import Console

        console = Console()

        # Try to parse as JSON command
        json_cmd = self._try_parse_json_output(response)
        if json_cmd:
            if json_cmd.get("type") == "corrected_command":
                command = json_cmd.get("command", "").strip()
                description = json_cmd.get("description", "")
                # If command is empty, show error message
                if not command:
                    print(f"\033[33m⚠ {t('shell.error_correction.no_valid_command')}\033[0m")
                    if description:
                        # Extract just the error message from description
                        # Remove common prefixes like "Command 'a' not found..."
                        clean_desc = description.split("Insufficient context")[0].strip()
                        if clean_desc:
                            print(f"   {clean_desc}")
                    print(f"   \033[36m{t('shell.error_correction.retry_hint')}\033[0m")
                    sys.stdout.flush()
                    sys.stderr.flush()
                    console.show_cursor()
                    return None
                print(f"{t('shell.error_correction.corrected_command_title')} \033[1;36m{command}\033[0m")
                if description:
                    print(f"   {description}")
                return command
            else:
                console.print(
                    Panel(Markdown(response), border_style="green", box=HORIZONTALS)
                )
                sys.stdout.flush()
                sys.stderr.flush()
                console.show_cursor()
                return None
        else:
            # Not JSON - display as Markdown panel
            console.print(
                Panel(Markdown(response), border_style="green", box=HORIZONTALS)
            )
            sys.stdout.flush()
            sys.stderr.flush()
            console.show_cursor()
            return None

    def _ask_execute_command(self, command: str) -> bool:
        """Ask user if they want to execute the corrected command.

        Returns True if command was executed, False otherwise.
        """
        confirmed = self.user_interaction.get_confirmation(
            f"{t('shell.error_correction.confirm_execute_prefix')}\033[1;36m{command}\033[0m{t('shell.error_correction.confirm_execute_suffix')}"
        )
        if confirmed:
            self.pty_manager.exit_tracker.set_last_command(command)
            self.pty_manager.send((command + "\r").encode())
            # Consume command echo from PTY to avoid displaying it
            import select
            try:
                ready, _, _ = select.select([self.pty_manager._master_fd], [], [], 0.1)
                if ready:
                    data = os.read(self.pty_manager._master_fd, 4096)
                    # Discard echoed command + CR/LF
            except Exception:
                pass
            return True
        return False


class OutputProcessor:
    """Process PTY output, detect errors, show hints."""

    def __init__(
        self, pty_manager: PTYManager, placeholder_manager: Optional["PlaceholderManager"] = None
    ):
        self.pty_manager = pty_manager
        self._waiting_for_result = False
        self._filter_exit_echo = False
        self.placeholder_manager = placeholder_manager

    def set_waiting_for_result(self, waiting: bool) -> None:
        """Set whether we're waiting for a command result."""
        self._waiting_for_result = waiting
        # Clear stale exit code state to prevent previous command's exit code
        # from prematurely resetting waiting flag on echo data
        if waiting:
            self.pty_manager.exit_tracker.clear_exit_available()

    def set_filter_exit_echo(self, filter_exit: bool) -> None:
        """Set whether to filter exit command echo."""
        self._filter_exit_echo = filter_exit

    def process(self, data: bytes) -> bytes:
        """Process PTY output, return cleaned output."""
        # Filter exit command echo (bash behavior in interactive PTY mode)
        # Bash echoes "exit" when exiting in interactive PTY mode
        if self._filter_exit_echo:
            # Check if this chunk is just the exit echo
            # Exit echo is typically just "\rexit\r\n" or "\nexit\r\n"
            stripped = data.strip(b"\r\n")
            if stripped == b"exit":
                # This is the exit echo, filter it out completely
                return b""
            # Also check for exit echo at the end of a larger chunk
            for pattern in (b"\rexit\r\n", b"\nexit\r\n", b"\rexit\n"):
                if data.endswith(pattern):
                    data = data[: -len(pattern)]
                    self._filter_exit_echo = False
                    break

        # Check for prompt and show placeholder
        # This is a heuristic: prompt typically ends with $, #, or similar
        # followed by space, and comes after a newline
        if self.placeholder_manager and not self._waiting_for_result:
            # Detect potential prompt: ends with a known prompt marker followed by space
            # Common bash prompt patterns + aish custom arrow prompt
            # aish uses: ➜ (success) or ➜➜ (failure) with ANSI reset before space
            prompt_patterns = (
                b"$ ", b"# ", b"% ", b"> ",  # Standard prompts
                b"\x1b[0m ",  # ANSI reset + space (aish prompt ends with this)
                b"m ",  # Color code end + space (partial match for robustness)
            )
            for pattern in prompt_patterns:
                if data.endswith(pattern):
                    # Show placeholder after this output
                    placeholder_seq = self.placeholder_manager.show_placeholder()
                    if placeholder_seq:
                        # Append placeholder to output
                        data = data + placeholder_seq
                    break

        # Only check for command completion when we're waiting for a result
        if not self._waiting_for_result:
            return data

        tracker = self.pty_manager.exit_tracker

        # Check if a command completed (success or error)
        if tracker.has_exit_code():
            # Use consume_error to check for new errors (auto-resets has_error flag)
            # This correctly handles the case where PTY output is split into multiple reads
            error_info = tracker.consume_error()
            if error_info is not None:
                # Show error hint on new line
                hint = t("shell.error_correction.press_semicolon_hint")
                sys.stdout.write(f"\033[33m<{hint}>\033[0m\r\n")
                sys.stdout.flush()
            # Reset waiting flag after command completes (success or error)
            self._waiting_for_result = False
            # Reset exit code available flag to prevent stale state from leaking
            # into the next command cycle
            tracker.clear_exit_available()

        return data


class PTYAIShell:
    """AI shell with direct PTY connection.

    Features:
    - InputRouter intercepts ';' at line start for AI commands
    - Pure PTY passthrough for bash commands
    - Perfect interactive support (vim, top, less, etc.)
    """

    def __init__(
        self,
        config: ConfigModel,
        skill_manager: "SkillManager",
        config_manager: Optional[Config] = None,
        console: Optional[Console] = None,
    ):
        self.config = config
        self.skill_manager = skill_manager
        self.config_manager = config_manager
        self.console = console or Console()

        # PTY manager
        self._pty_manager: Optional[PTYManager] = None

        # Components
        self._input_router: Optional[InputRouter] = None
        self._output_processor: Optional[OutputProcessor] = None
        self._ai_handler: Optional[AIHandler] = None

        # State
        self._running = False
        self._original_termios: Optional[list] = None

        # === New: LLM and Context ===
        self.prompt_manager: PromptManager = PromptManager()
        self.context_manager: ContextManager = ContextManager(
            max_llm_messages=getattr(config, "max_llm_messages", 50),
            max_shell_messages=getattr(config, "max_shell_messages", 20),
            token_budget=getattr(config, "context_token_budget", None),
            model=config.model,
            enable_token_estimation=getattr(config, "enable_token_estimation", True),
        )
        self.llm_session: "LLMSession" = self._create_llm_session()

        # === New: System Information ===
        self.uname_info, self.os_info, self.basic_env_info = get_or_fetch_static_env_info()
        self.output_language = get_output_language(config)
        self.current_env_info = get_current_env_info()

        # Cache system info to context
        env_entry = f"[system information] {self.current_env_info}"
        self.context_manager.add_memory(
            MemoryType.LLM, {"role": "user", "content": env_entry}
        )
        self.context_manager.add_memory(
            MemoryType.KNOWLEDGE, {"key": "system_info", "value": self.uname_info}
        )
        self.context_manager.add_memory(
            MemoryType.KNOWLEDGE, {"key": "os_info", "value": self.os_info}
        )
        self.context_manager.add_memory(
            MemoryType.KNOWLEDGE, {"key": "output_language", "value": self.output_language}
        )

        # === New: Skill Hot Reload ===
        self.skill_hotreload_service: SkillHotReloadService = SkillHotReloadService(
            skill_manager=skill_manager, debounce_ms=200
        )

        # === New: LLM Event Handling ===
        self.llm_event_router: LLMEventRouter = LLMEventRouter(
            handlers={
                LLMEventType.OP_START: self.handle_operation_start,
                LLMEventType.GENERATION_START: self.handle_generation_start,
                LLMEventType.GENERATION_END: self.handle_generation_end,
                LLMEventType.REASONING_START: self.handle_reasoning_start,
                LLMEventType.REASONING_DELTA: self.handle_reasoning_delta,
                LLMEventType.REASONING_END: self.handle_reasoning_end,
                LLMEventType.CONTENT_DELTA: self.handle_content_delta,
                LLMEventType.TOOL_EXECUTION_START: self.handle_tool_execution_start,
                LLMEventType.TOOL_EXECUTION_END: self.handle_tool_execution_end,
                LLMEventType.ERROR: self.handle_error_event,
                LLMEventType.CANCELLED: self.handle_processing_cancelled,
                LLMEventType.INTERACTION_REQUIRED: self.handle_ask_user_required,
                LLMEventType.TOOL_CONFIRMATION_REQUIRED: self.handle_tool_confirmation_required,
            }
        )
        self.animation_active: bool = False
        self.animation_thread: Optional[threading.Thread] = None
        self.animation_lock: threading.Lock = threading.Lock()
        self.animation_counter: int = 0
        self._animation_base_text: str = "思考中"
        self._animation_pattern: str = "braille"
        self._animation_update_text: Optional[str] = None
        self._reasoning_display_enabled: bool = False
        self._reasoning_active: bool = False
        self._reasoning_partial: str = ""
        self._reasoning_lines: list[str] = []
        self._reasoning_max_lines: int = 2
        self._last_reasoning_render_lines: list[str] = []
        self._last_streaming_accumulated: str = ""
        self.current_live: Optional[Any] = None  # Rich Live object
        self._content_preview_active: bool = False
        self._at_line_start: bool = True

        # === Ctrl+C cancellation infrastructure ===
        self._current_op_scope: Optional[Any] = None  # anyio.CancelScope
        self._user_requested_exit: bool = False
        self.operation_in_progress: bool = False

        # === New: User Interaction ===
        self.user_interaction: PTYUserInteraction = PTYUserInteraction()
        self._approved_ai_commands: set[str] = set()

        # === New: Placeholder Manager ===
        self._placeholder_manager: Optional[PlaceholderManager] = None

    def _create_llm_session(self) -> "LLMSession":
        """Create LLMSession with all necessary dependencies."""
        from .llm import LLMSession
        from .interruption import InterruptionManager
        import threading
        import logging

        logger = logging.getLogger(__name__)

        interruption_manager = InterruptionManager()
        cancellation_token = CancellationToken()

        # Store interruption_manager and wire up interrupt callback
        self.interruption_manager = interruption_manager
        interruption_manager.set_interrupt_callback(self._on_interrupt_requested)

        session = LLMSession(
            config=self.config,
            skill_manager=self.skill_manager,
            event_callback=self.handle_llm_event,
            env_manager=None,  # PTY mode doesn't use EnvironmentManager
            interruption_manager=interruption_manager,
            is_command_approved=None,  # PTY mode doesn't use command approval
            history_manager=None,  # PTY mode doesn't use HistoryManager
        )

        # Start background initialization of litellm to avoid delay on first AI use
        # This runs in a separate thread so it doesn't block shell startup
        def init_litellm_in_background():
            try:
                session._get_litellm()
                session._get_acompletion()
                with session._sync_init_lock:
                    session._initialized = True
                logger.info("LLM client initialized successfully in background")
            except Exception as e:
                logger.warning(f"LLM background initialization failed: {e}, will retry on first use")

        init_thread = threading.Thread(target=init_litellm_in_background, daemon=True)
        init_thread.start()

        return session

    # === LLM Event Handlers ===

    def handle_operation_start(self, event) -> None:
        """Handle operation start event."""
        return None

    def handle_generation_start(self, event) -> None:
        """Handle generation start event."""
        self._finalize_content_preview()
        self._reset_reasoning_state()
        self._last_streaming_accumulated = ""
        self._reasoning_display_enabled = True
        return self.handle_thinking_start(event)

    def handle_thinking_start(self, event) -> None:
        """Handle thinking start event - show enhanced rotating animation"""
        from rich.text import Text

        # Stop any existing animation
        self._stop_animation()
        self._last_streaming_accumulated = ""
        self._last_reasoning_render_lines = []

        if self.current_live:
            self.current_live.stop()

        # Move to a new line so Live doesn't overwrite user input.
        # transient=True ensures the animation line is fully cleared on stop,
        # so the cursor returns here and content replaces it without a blank line.
        self.console.print()
        self._at_line_start = True

        self.current_live = Live(console=self.console, auto_refresh=False, transient=True)
        self.current_live.start()

        # Start enhanced rotating animation
        pattern = "braille"
        base_text = "思考中"
        self._start_animation(base_text=base_text, pattern=pattern)
        return None

    def _update_reasoning_live(self, lines: list[str]) -> None:
        """Update reasoning display with live content."""
        from rich.text import Text

        self._check_terminal_resize()
        if not self.current_live:
            self.current_live = Live(console=self.console, auto_refresh=False, transient=True)
            self.current_live.start()

        self._last_reasoning_render_lines = list(lines)
        with self.animation_lock:
            spinner_char = self._get_current_spinner_char("dots")
            self.animation_counter += 1

        max_width = max(10, self.console.size.width - 4)
        trimmed_lines = []
        for line in lines:
            if len(line) > max_width:
                trimmed_lines.append(line[-max_width:])
            else:
                trimmed_lines.append(line)

        thinking_label = "思考中"
        if trimmed_lines:
            display_text = "\n".join(
                [f"{spinner_char} {thinking_label}", *trimmed_lines]
            )
        else:
            display_text = f"{spinner_char} {thinking_label}..."

        self.current_live.update(Text(display_text, style="grey50"), refresh=True)

    def _render_streaming_chunk(self, accumulated_content: str) -> None:
        """Render streaming content chunk."""
        from rich.text import Text

        display_text = str(accumulated_content).replace("\n", " ")
        terminal_width = self.console.size.width
        display_width = min(60, int(terminal_width * 0.8))

        if len(display_text) > display_width:
            display_text = "🤖 思考中: " + display_text[-(display_width - 3):]
        else:
            display_text = "🤖 思考中: " + display_text

        if self.current_live:
            self.current_live.update(Text(display_text, style="green"), refresh=True)

    def _start_animation(
        self,
        base_text: Optional[str] = None,
        pattern: str = "braille",
        update_text: Optional[str] = None,
    ) -> None:
        """Start the background animation thread."""
        with self.animation_lock:
            self._animation_base_text = base_text or "思考中"
            self._animation_pattern = pattern
            self._animation_update_text = update_text

            if self.animation_thread and self.animation_thread.is_alive():
                self.animation_active = True
                return

            self.animation_active = True
            self.animation_counter = 0

        self.animation_thread = threading.Thread(
            target=self._animate_thinking,
            daemon=True,
        )
        self.animation_thread.start()

    def _animate_thinking(self) -> None:
        """Background animation loop for thinking indicators."""
        from rich.text import Text

        while self.animation_active:
            try:
                self._check_terminal_resize()
                with self.animation_lock:
                    if not self.animation_active or not self.current_live:
                        break

                    current_live = self.current_live
                    pattern = self._animation_pattern
                    base_text = self._animation_base_text
                    update_text = self._animation_update_text

                    spinner_char = self._get_current_spinner_char(pattern)
                    self.animation_counter += 1

                if update_text:
                    display_text = f"{spinner_char} {update_text}"
                else:
                    display_text = f"{spinner_char} {base_text}..."

                current_live.update(Text(display_text, style="blue"), refresh=True)

                time.sleep(0.15)  # 150ms for smooth animation
            except Exception:
                # Silently handle animation errors to avoid disrupting main flow
                break

    def _stop_animation(self) -> None:
        """Stop the background animation thread with robust cleanup."""
        try:
            with self.animation_lock:
                self.animation_active = False

            if self.animation_thread and self.animation_thread.is_alive():
                # Give thread time to stop gracefully
                self.animation_thread.join(timeout=0.5)

                # If thread is still alive, it will be cleaned up by daemon=True
                if self.animation_thread.is_alive():
                    # Thread will be terminated when main thread exits
                    pass
        except Exception:
            # Silently handle any cleanup errors to prevent cascading issues
            pass
        finally:
            self.animation_thread = None

    def handle_generation_end(self, event) -> None:
        """Handle generation end event."""
        self._stop_animation()
        self._reset_reasoning_state()
        self._last_streaming_accumulated = ""
        self._finalize_content_preview()
        if self.current_live:
            self.current_live.update("", refresh=True)
            self.current_live.stop()
            self.current_live = None
        return None

    def handle_completion_done(self, event) -> None:
        """Handle completion done event - clean up display."""
        self._last_streaming_accumulated = ""
        self._last_reasoning_render_lines = []
        if self.current_live:
            self.current_live.update("", refresh=True)
            self.current_live.stop()
            self.current_live = None
        return None

    def handle_reasoning_start(self, event) -> None:
        """Handle reasoning start event."""
        if not self._reasoning_display_enabled:
            return None

        self._finalize_content_preview()
        self._stop_animation()
        self._reasoning_active = True
        self._last_streaming_accumulated = ""
        self._reasoning_partial = ""
        self._reasoning_lines = []
        self._update_reasoning_live([])
        return None

    def handle_reasoning_delta(self, event) -> None:
        """Handle reasoning content delta."""
        if not self._reasoning_display_enabled:
            return None

        delta = str(event.data.get("delta") or "")
        if not delta:
            return None

        if not self._reasoning_active:
            self.handle_reasoning_start(event)

        lines = self._append_reasoning_delta(delta)
        self._update_reasoning_live(lines)
        return None

    def _append_reasoning_delta(self, delta: str) -> list[str]:
        """Append reasoning delta and return new lines."""
        normalized = delta.replace("\r\n", "\n").replace("\r", "\n")
        parts = normalized.split("\n")
        if not parts:
            return []

        self._reasoning_partial += parts[0]
        for segment in parts[1:]:
            self._reasoning_lines.append(self._reasoning_partial)
            self._reasoning_partial = segment
            if len(self._reasoning_lines) > self._reasoning_max_lines:
                self._reasoning_lines = self._reasoning_lines[
                    -self._reasoning_max_lines:
                ]

        all_lines = list(self._reasoning_lines)
        if self._reasoning_partial:
            all_lines.append(self._reasoning_partial)

        if not all_lines:
            return []

        return all_lines[-self._reasoning_max_lines:]

    def handle_reasoning_end(self, event) -> None:
        """Handle reasoning end event."""
        self._reasoning_active = False
        return None

    def handle_content_delta(self, event) -> None:
        """Handle streamed/synthesized assistant text chunks."""
        from rich.text import Text

        # Stop animation on the first emitted content to avoid competing Live updates
        self._stop_animation()
        self._reset_reasoning_state()
        self._last_streaming_accumulated = ""

        if self.current_live:
            self.current_live.update("", refresh=True)
            self.current_live.stop()
            self.current_live = None

        content = event.data.get("delta") or event.data.get("accumulated") or ""
        content = str(content)
        if not content:
            return None

        if not self._content_preview_active:
            self._content_preview_active = True
            content = f"🤖 {content}"

        self.console.print(Text(content, style="bold grey50"), end="")
        self._at_line_start = False
        return None

    def handle_tool_execution_start(self, event) -> None:
        """Handle tool execution start."""
        self._finalize_content_preview()
        tool_name = event.data.get("tool_name", "unknown")
        tool_args = self._format_tool_args_for_display(
            tool_name, event.data.get("tool_args", {})
        )

        prefix = t("shell.tool.prefix")
        if event.data and event.data.get("source") == "system_diagnose_agent":
            prefix = t("shell.tool.prefix_diagnose")

        self.console.print(f"{prefix}: {tool_name} ({tool_args})", style="cyan")
        return None

    def handle_tool_execution_end(self, event) -> None:
        """Handle tool execution end."""
        tool_name = event.data.get("tool_name", "unknown")

        if event.data and event.data.get("source") == "system_diagnose_agent":
            prefix = t("shell.tool.done_diagnose")
            self.console.print(f"{prefix}: {tool_name}", style="green")
        return None

    def handle_error_event(self, event) -> None:
        """Handle error event."""
        self._finalize_content_preview()
        error_msg = event.data.get("error_message", "Unknown error")
        self.console.print(f"\033[31m错误: {error_msg}\033[0m")

        # Clean up live display on error
        self._reset_reasoning_state()
        self._last_streaming_accumulated = ""
        if self.current_live:
            self.current_live.update("", refresh=True)
            self.current_live.stop()
            self.current_live = None
        return None

    def _on_interrupt_requested(self) -> None:
        """Interrupt callback - called by InterruptionManager."""
        from .cancellation import CancellationReason

        self.llm_session.cancellation_token.cancel(
            CancellationReason.USER_INTERRUPT, "User pressed Ctrl+C"
        )

    def handle_processing_cancelled(self, event=None) -> None:
        """Handle processing cancelled with context-aware messages."""
        from .interruption import ShellState

        self._stop_animation()
        self._reset_reasoning_state()
        self._last_streaming_accumulated = ""
        self._finalize_content_preview()

        # Clear any live display
        if self.current_live:
            self.current_live.update("", refresh=True)
            self.current_live.stop()
            self.current_live = None

        # Check if tool confirmation was denied (user pressed N)
        is_tool_denied = (
            event and event.data and event.data.get("reason") == "tool_cancelled"
        )

        # Show cancellation message with appropriate formatting
        if not is_tool_denied:
            last_ai_state = self.interruption_manager.get_last_ai_state()

            if last_ai_state == ShellState.AI_THINKING:
                self.console.print("<Interrupted received.>", style="dim")
            elif last_ai_state == ShellState.SANDBOX_EVAL:
                self.console.print(
                    "<Stopping... finalizing current task.>", style="dim"
                )
            elif last_ai_state == ShellState.COMMAND_EXEC:
                self.console.print(
                    "<Stopping... finishing current task (this may take a moment)>",
                    style="dim",
                )

        # Clear last AI state
        self.interruption_manager.clear_last_ai_state()

    def handle_ask_user_required(self, event):
        """Handle ask_user tool call."""
        from .llm import LLMCallbackResult

        data = event.data or {}
        prompt = data.get("prompt", "")
        options = data.get("options", [])
        allow_custom_input = data.get("allow_custom_input", False)

        try:
            choice_value, custom_input = self.user_interaction.request_choice(
                prompt, options, allow_custom_input
            )

            if choice_value:
                data["selected_value"] = choice_value
            elif custom_input:
                data["selected_value"] = custom_input

            if choice_value:
                return LLMCallbackResult(
                    action="return",
                    data={"choice": choice_value}
                )
            elif custom_input:
                return LLMCallbackResult(
                    action="return",
                    data={"custom_input": custom_input}
                )
            else:
                return LLMCallbackResult(
                    action="return",
                    data={"cancelled": True}
                )
        except Exception as e:
            return LLMCallbackResult(
                action="return",
                data={"error": str(e)}
            )

    def handle_tool_confirmation_required(self, event):
        """Handle tool confirmation request."""
        from .llm import LLMCallbackResult

        # Stop animation and Live display before prompting
        self._stop_animation()
        if self.current_live:
            try:
                self.current_live.update("", refresh=True)
                self.current_live.stop()
            finally:
                self.current_live = None

        self._finalize_content_preview()
        data = event.data or {}

        panel = data.get("panel") if isinstance(data.get("panel"), dict) else {}
        panel_mode = str(panel.get("mode") or data.get("panel_mode", "confirm")).lower()

        # Display security panel
        display_security_panel(self, data, panel_mode=panel_mode)

        # Only "confirm" requires interactive user input
        if panel_mode == "confirm":
            remember_command = panel.get("remember_key", data.get("remember_key"))
            allow_remember = bool(panel.get("allow_remember", data.get("allow_remember")))
            return get_user_confirmation(
                self,
                remember_command=remember_command,
                allow_remember=allow_remember,
            )
        return LLMCallbackResult.CONTINUE

    def handle_llm_event(self, event) -> Any:
        """Main event handler that routes events to specific handlers."""
        return self.llm_event_router.handle(event)

    def run(self) -> None:
        """Main shell loop."""
        self._setup_signals()
        self._save_terminal()
        self._show_welcome()
        self._setup_pty()
        self._setup_components()

        self._running = True

        from .cancellation import CancellationReason

        async def _main_loop():
            with anyio.open_signal_receiver(signal.SIGINT) as sigs:
                signal_scope = anyio.CancelScope()

                async def signal_handler():
                    try:
                        with signal_scope:
                            async for _ in sigs:
                                if self._current_op_scope is not None:
                                    self._current_op_scope.cancel()
                                self.llm_session.cancellation_token.cancel(
                                    CancellationReason.USER_INTERRUPT,
                                    "SIGINT received",
                                )
                    except anyio.get_cancelled_exc_class():
                        pass

                async def _loop_body():
                    self._set_raw_mode()

                    while self._running:
                        try:
                            read_fds = [sys.stdin.fileno()]
                            if (
                                self._pty_manager
                                and self._pty_manager._master_fd is not None
                            ):
                                read_fds.append(self._pty_manager._master_fd)

                            ready, _, _ = select.select(read_fds, [], [], 0.05)
                        except (ValueError, OSError):
                            break

                        for fd in ready:
                            if fd == sys.stdin.fileno():
                                self._handle_stdin()
                            elif self._pty_manager._master_fd:
                                self._handle_pty_output()

                        # anyio checkpoint to allow signal handler to run
                        await anyio.sleep(0)

                async with anyio.create_task_group() as tg:
                    tg.start_soon(signal_handler)
                    await _loop_body()
                    # When _loop_body exits, cancel signal handler
                    signal_scope.cancel()

        try:
            anyio.run(_main_loop)
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()

    def _setup_signals(self) -> None:
        """Set up signal handlers."""
        # SIGINT is handled by anyio.open_signal_receiver in run()
        signal.signal(signal.SIGTERM, self._sigterm_handler)
        signal.signal(signal.SIGWINCH, self._sigwinch_handler)

    def _sigterm_handler(self, signum, frame) -> None:
        """Handle termination."""
        self._running = False

    def _sigwinch_handler(self, signum, frame) -> None:
        """Handle terminal resize."""
        if self._pty_manager:
            try:
                size = shutil.get_terminal_size()
                self._pty_manager.resize(size.lines, size.columns)
            except Exception:
                pass

    def _save_terminal(self) -> None:
        """Save terminal settings."""
        try:
            self._original_termios = termios.tcgetattr(sys.stdin.fileno())
        except Exception:
            pass

    def _set_raw_mode(self) -> None:
        """Set raw terminal mode."""
        if self._original_termios:
            tty.setraw(sys.stdin.fileno())

    def _restore_terminal(self) -> None:
        """Restore terminal settings."""
        if self._original_termios:
            try:
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._original_termios
                )
            except Exception:
                pass

    # === Animation System ===

    def _get_spinner_patterns(self) -> dict[str, list[str]]:
        """Get spinner patterns for animation."""
        return {
            "braille": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"],
            "dots": ["⠁", "⠂", "⠄", "⡀", "⢀", "⠠", "⠐", "⠈"],
            "thinking": ["🤔", "💭", "🧠", "⚡", "✨", "🔍", "💡", "🎯"],
            "progress": ["●○○○", "○●○○", "○○●○", "○○○●", "○○●○", "○●○○"],
            "arrows": ["←", "↖", "↑", "↗", "→", "↘", "↓", "↙"],
            "clock": [
                "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗",
                "🕘", "🕙", "🕚", "🕛",
            ],
        }

    def _get_current_spinner_char(self, pattern_name: str = "braille") -> str:
        """Get current spinner character based on pattern and counter."""
        patterns = self._get_spinner_patterns()
        pattern = patterns.get(pattern_name, patterns["braille"])
        return pattern[self.animation_counter % len(pattern)]

    def _reset_reasoning_state(self) -> None:
        """Reset reasoning display state."""
        self._reasoning_display_enabled = False
        self._reasoning_active = False
        self._reasoning_partial = ""
        self._reasoning_lines = []
        self._last_reasoning_render_lines = []

    def _finalize_content_preview(self) -> None:
        """Finalize any pending content preview."""
        if not self._content_preview_active:
            return
        self.console.print()
        self._content_preview_active = False
        self._at_line_start = True

    @staticmethod
    def _safe_cancel_scope():
        """Provide a CancelScope when running under anyio, fallback otherwise."""
        import anyio

        scope = anyio.CancelScope()
        try:
            entered = scope.__enter__()
        except (AssertionError, Exception):
            # No anyio task state available (e.g. sync context).
            yield None
            return
        try:
            yield entered
        finally:
            scope.__exit__(None, None, None)

    # === Tool display helpers (ported from the legacy shell core) ===

    def _get_tool_arg_preview_settings(self, tool_name: str) -> ToolArgPreviewSettings:
        raw_config = getattr(self.config, "tool_arg_preview", None)
        if isinstance(raw_config, dict):
            settings = raw_config.get(tool_name) or raw_config.get("default")
            if isinstance(settings, ToolArgPreviewSettings):
                return settings
        return ToolArgPreviewSettings()

    def _truncate_tool_text(self, text: str, settings: ToolArgPreviewSettings) -> str:
        max_lines = settings.max_lines
        max_chars = settings.max_chars

        preview = text
        suffix_parts = []
        lines = text.splitlines()
        if max_lines and len(lines) > max_lines:
            preview = "\n".join(lines[:max_lines])
            suffix_parts.append(f"+{len(lines) - max_lines} lines")

        if max_chars and len(preview) > max_chars:
            extra_chars = len(preview) - max_chars
            preview = preview[:max_chars]
            suffix_parts.append(f"+{extra_chars} chars")

        if suffix_parts:
            return f"{preview} ... ({', '.join(suffix_parts)})"
        return preview

    def _format_tool_arg_value(
        self, value: object, settings: ToolArgPreviewSettings
    ) -> str:
        max_items = settings.max_items

        if isinstance(value, str):
            return self._truncate_tool_text(value, settings)
        if isinstance(value, dict):
            items = list(value.items())
            rendered_items = []
            for key, item in items[:max_items]:
                rendered_items.append(
                    f"{key}={self._format_tool_arg_value(item, settings)}"
                )
            if len(items) > max_items:
                rendered_items.append(f"... (+{len(items) - max_items} more)")
            return ", ".join(rendered_items)
        if isinstance(value, (list, tuple)):
            items = list(value)
            rendered_items = [
                self._format_tool_arg_value(item, settings)
                for item in items[:max_items]
            ]
            if len(items) > max_items:
                rendered_items.append(f"... (+{len(items) - max_items} more)")
            return "[" + ", ".join(rendered_items) + "]"
        return str(value)

    def _format_tool_args_for_display(self, tool_name: str, tool_args: object) -> str:
        if tool_name == "write_file" and isinstance(tool_args, dict):
            display_args = dict(tool_args)
            display_args.pop("content", None)
            return self._format_tool_arg_value(display_args, ToolArgPreviewSettings())
        settings = self._get_tool_arg_preview_settings(tool_name)
        if not settings.enabled:
            if isinstance(tool_args, dict) and len(tool_args) == 1:
                return str(next(iter(tool_args.values())))
            return str(tool_args)

        if isinstance(tool_args, dict) and len(tool_args) == 1:
            tool_args = next(iter(tool_args.values()))
        return self._format_tool_arg_value(tool_args, settings)

    def _remember_approved_command(self, command: str) -> None:
        command = str(command)
        if not command:
            return
        self._approved_ai_commands.add(command)

    def _is_command_approved(self, command: str) -> bool:
        command = str(command)
        return bool(command) and command in self._approved_ai_commands

    def _check_terminal_resize(self) -> None:
        """Check if terminal has been resized and refresh Live display."""
        try:
            current_size = shutil.get_terminal_size()
            if not hasattr(self, '_last_terminal_size'):
                self._last_terminal_size = current_size
                return
            if current_size == self._last_terminal_size:
                return
            self._last_terminal_size = current_size
            self._refresh_live_for_resize()
        except Exception:
            pass

    def _refresh_live_for_resize(self) -> None:
        """Refresh Live display after terminal resize."""
        if not self.current_live:
            return

        if self._reasoning_active or self._last_reasoning_render_lines:
            self._update_reasoning_live(self._last_reasoning_render_lines)
            return

        if self._last_streaming_accumulated:
            self._render_streaming_chunk(self._last_streaming_accumulated)

    def _show_welcome(self) -> None:
        """Display welcome screen."""
        try:
            welcome = build_welcome_renderable(self.config)
            self.console.print(welcome)
        except Exception:
            pass

    def _setup_pty(self) -> None:
        """Set up PTY with bash."""
        try:
            size = shutil.get_terminal_size()
            rows, cols = size.lines, size.columns
        except Exception:
            rows, cols = 24, 80

        self._pty_manager = PTYManager(
            rows=rows, cols=cols, cwd=os.getcwd(), use_output_thread=False
        )
        self._pty_manager.start()

        # Wait for bash to initialize
        time.sleep(0.2)

    def _setup_components(self) -> None:
        """Set up input router and output processor."""
        # Create AIHandler with new dependencies
        self._ai_handler = AIHandler(
            self._pty_manager,
            self.llm_session,
            self.prompt_manager,
            self.context_manager,
            self.skill_manager,
            self.user_interaction,
            self._original_termios,
        )
        self._ai_handler.shell = self
        # Initialize PlaceholderManager first (needed by OutputProcessor)
        self._placeholder_manager = PlaceholderManager(
            interruption_manager=self.interruption_manager
        )
        # Create output processor with placeholder manager
        self._output_processor = OutputProcessor(
            pty_manager=self._pty_manager,
            placeholder_manager=self._placeholder_manager,
        )
        # Then create input router with output processor reference and placeholder manager
        self._input_router = InputRouter(
            self._pty_manager,
            self._ai_handler,
            self._output_processor,
            placeholder_manager=self._placeholder_manager,
            interruption_manager=self.interruption_manager,
        )

    def _handle_stdin(self) -> None:
        """Handle stdin with input routing."""
        try:
            data = os.read(sys.stdin.fileno(), 4096)
            if data:
                # Route input through InputRouter
                self._input_router.handle_input(data)
            else:
                self._running = False
        except OSError:
            self._running = False

    def _handle_pty_output(self) -> None:
        """Handle PTY output."""
        try:
            data = os.read(self._pty_manager._master_fd, 1024 * 20)
            if data:
                # Parse exit code markers
                cleaned = self._pty_manager.exit_tracker.parse_and_update(data)
                # Process output (show error hints, filter exit echo)
                processed = self._output_processor.process(cleaned)
                # Forward to stdout
                if processed:
                    sys.stdout.buffer.write(processed)
                    sys.stdout.buffer.flush()
            else:
                self._running = False
        except OSError:
            self._running = False

    def _cleanup(self) -> None:
        """Cleanup resources."""
        if not self._running and hasattr(self, "_cleanup_done"):
            return
        self._running = False
        self._cleanup_done = True

        # Stop animation
        self._stop_animation()
        self._finalize_content_preview()

        if self._pty_manager:
            self._pty_manager.stop()

        self._restore_terminal()

        # Print goodbye message
        self.console.print(t("cli.startup.goodbye"))


def run_shell(
    config: ConfigModel,
    skill_manager: "SkillManager",
    config_manager: Optional[Config] = None,
) -> None:
    """Run the AI shell (entry point for CLI)."""
    shell = PTYAIShell(config, skill_manager, config_manager)
    try:
        shell.run()
    except KeyboardInterrupt:
        pass
    finally:
        shell._cleanup()
