"""PTY manager using direct pty.fork() - pyxtermjs style."""

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import threading
import time
from typing import Callable, Optional

from .exit_tracker import ExitCodeTracker


def set_winsize(fd: int, row: int, col: int, xpix: int = 0, ypix: int = 0) -> None:
    """Set terminal window size."""
    winsize = struct.pack("HHHH", row, col, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


class PTYManager:
    """Manage PTY connection to bash using direct pty.fork().

    This is the pyxtermjs approach: simple, reliable, perfect bash compatibility.

    Usage:
        manager = PTYManager()
        manager.start()

        # With callback
        manager.set_output_callback(lambda data: print(data, end=''))
        manager.send("ls -la\n")

        # Later
        manager.stop()
    """

    # Bash initialization to inject exit code marker and custom prompt
    BASH_INIT = r'''
# aish exit code tracking and prompt generation
__aish_last_exit_code=0

__aish_set_prompt() {
    local exit_code=$?
    __aish_last_exit_code=$exit_code
    printf "[AISH_EXIT:%s]" "$exit_code"

    # Build prompt dynamically
    local prompt_parts=()

    # Add model if available
    if [ -n "$AISH_MODEL" ]; then
        prompt_parts+=("\033[2m$AISH_MODEL\033[0m")
    fi

    # Add current directory (abbreviated)
    local cwd="$PWD"
    if [[ "$cwd" == "$HOME"* ]]; then
        cwd="~${cwd#$HOME}"
    fi
    # Abbreviate: ~/nfs/xzx/github/aish -> ~/n/x/g/aish
    local IFS='/' parts=($cwd)
    local abbrev=""
    for i in "${!parts[@]}"; do
        local part="${parts[$i]}"
        if [[ -z "$part" ]]; then
            continue
        fi
        if [[ "$part" == "~" || $i -eq $((${#parts[@]}-1)) ]]; then
            abbrev+="$part/"
        else
            abbrev+="${part:0:1}/"
        fi
    done
    cwd="${abbrev%/}"
    prompt_parts+=("\033[34m$cwd\033[0m")

    # Add git branch if in repo
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        local branch="$(git branch --show-current 2>/dev/null || echo HEAD)"
        if [[ "$branch" == "HEAD" ]]; then
            prompt_parts+=("\033[2m$branch\033[0m")
        else
            prompt_parts+=("\033[35m$branch\033[0m")
        fi
    fi

    # Join parts
    local prompt=""
    local separator=" | "
    local first=true
    for part in "${prompt_parts[@]}"; do
        if $first; then
            prompt="$part"
            first=false
        else
            prompt="$prompt$separator$part"
        fi
    done

    # Add prompt symbol
    if [ "$exit_code" -eq 0 ]; then
        prompt="$prompt \033[32m➜\033[0m "
    else
        prompt="$prompt \033[31m➜➜\033[0m "
    fi

    # Set PS1 for this prompt cycle
    PS1="$prompt"
}

# Run before each prompt
PROMPT_COMMAND="__aish_set_prompt"
PS1=""  # Will be set by PROMPT_COMMAND
'''

    def __init__(
        self,
        rows: int = 24,
        cols: int = 80,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        use_output_thread: bool = True,
    ):
        self._rows = rows
        self._cols = cols
        self._cwd = cwd or os.getcwd()
        self._env = env or {}
        self._use_output_thread = use_output_thread

        self._master_fd: Optional[int] = None
        self._child_pid: Optional[int] = None
        self._running = False
        self._output_thread: Optional[threading.Thread] = None

        # Callbacks
        self._output_callback: Optional[Callable[[bytes], None]] = None
        self._exit_code_callback: Optional[Callable[[int], None]] = None

        # Exit code tracking
        self._exit_tracker = ExitCodeTracker()

        # Lock for thread-safe operations
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Check if PTY is active."""
        return self._running and self._child_pid is not None

    @property
    def exit_tracker(self) -> ExitCodeTracker:
        """Get exit code tracker."""
        return self._exit_tracker

    def set_output_callback(self, callback: Callable[[bytes], None]) -> None:
        """Set callback for PTY output."""
        self._output_callback = callback

    def set_exit_code_callback(self, callback: Callable[[int], None]) -> None:
        """Set callback for exit code changes."""
        self._exit_code_callback = callback

    def set_exit_code_callback(self, callback: Callable[[int], None]) -> None:
        """Set callback for exit code changes."""
        self._exit_code_callback = callback

    def start(self) -> None:
        """Start bash process with PTY."""
        if self._running:
            return

        self._child_pid, self._master_fd = pty.fork()

        if self._child_pid == 0:
            # Child process: exec bash
            os.chdir(self._cwd)

            # Build environment
            env = dict(os.environ)
            env.update(self._env)
            env["TERM"] = "xterm-256color"

            # Use our rcfile wrapper to set up exit code tracking while preserving user's config
            rcfile_path = os.path.join(os.path.dirname(__file__), "bash_rc_wrapper.sh")
            if os.path.exists(rcfile_path):
                os.execvpe(
                    "/bin/bash",
                    ["/bin/bash", "--rcfile", rcfile_path, "-i"],
                    env,
                )
            else:
                # Fallback without rcfile
                os.execvpe(
                    "/bin/bash",
                    ["/bin/bash"],
                    env,
                )
            os._exit(1)

        # Parent process
        # Set non-blocking
        flags = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Set window size
        set_winsize(self._master_fd, self._rows, self._cols)

        self._running = True

        # Start output reader thread (optional - disabled when main loop reads directly)
        if self._use_output_thread:
            self._output_thread = threading.Thread(target=self._output_loop, daemon=True)
            self._output_thread.start()

            # Wait for bash to be ready (discard initial output)
            self._wait_ready()
        else:
            # When not using thread, just wait a bit for bash to start
            time.sleep(0.1)

    def _wait_ready(self, timeout: float = 0.3) -> None:
        """Wait for bash to initialize."""
        start = time.time()
        while time.time() - start < timeout:
            ready, _, _ = select.select([self._master_fd], [], [], 0.05)
            if ready:
                try:
                    data = os.read(self._master_fd, 4096)
                    # Process exit code markers
                    cleaned = self._exit_tracker.parse_and_update(data)
                    # Forward to callback if set (but typically discard during init)
                    if cleaned and self._output_callback:
                        try:
                            self._output_callback(cleaned)
                        except Exception:
                            pass
                except OSError:
                    break

    def _output_loop(self) -> None:
        """Background thread to read and forward PTY output."""
        max_read_bytes = 1024 * 20

        while self._running:
            try:
                # Poll for data
                ready, _, _ = select.select([self._master_fd], [], [], 0.01)
                if not ready:
                    continue

                data = os.read(self._master_fd, max_read_bytes)
                if not data:
                    # EOF - bash exited
                    self._running = False
                    break

                # Parse exit code markers and clean output
                cleaned = self._exit_tracker.parse_and_update(data)

                # Forward cleaned output to callback
                if cleaned and self._output_callback:
                    try:
                        self._output_callback(cleaned)
                    except Exception:
                        pass

            except OSError:
                self._running = False
                break

    def send(self, data: bytes) -> int:
        """Send input to bash."""
        if not self._running or self._master_fd is None:
            return 0

        with self._lock:
            try:
                return os.write(self._master_fd, data)
            except OSError:
                return 0

    def send_command(self, command: str) -> None:
        """Send a command (with newline) to bash."""
        self._exit_tracker.set_last_command(command.strip())
        self.send((command + "\n").encode())

    def resize(self, rows: int, cols: int) -> None:
        """Resize terminal."""
        if self._master_fd is None:
            return

        with self._lock:
            self._rows = rows
            self._cols = cols
            set_winsize(self._master_fd, rows, cols)

    def stop(self) -> None:
        """Stop bash and close PTY."""
        self._running = False

        if self._child_pid is not None:
            try:
                os.kill(self._child_pid, signal.SIGTERM)
                time.sleep(0.1)
                os.waitpid(self._child_pid, os.WNOHANG)
            except (ProcessLookupError, ChildProcessError, OSError):
                pass

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass

        self._master_fd = None
        self._child_pid = None

    def __enter__(self) -> "PTYManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
