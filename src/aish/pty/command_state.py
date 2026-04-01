"""Event-based command lifecycle tracking for the persistent bash PTY."""

from __future__ import annotations

from dataclasses import dataclass

from .control_protocol import BackendControlEvent


@dataclass(slots=True)
class CommandSubmission:
    """Metadata for a command submitted to the backend shell."""

    command: str
    source: str
    command_seq: int | None = None
    error_correction_dismissed: bool = False


@dataclass(slots=True)
class CommandResult:
    """Resolved completion state for a backend command."""

    command: str
    exit_code: int
    source: str
    command_seq: int | None = None
    interrupted: bool = False


class CommandState:
    """Track submitted commands and completed results using control events."""

    def __init__(self) -> None:
        self._active_submission: CommandSubmission | None = None
        self._submitted_by_seq: dict[int, CommandSubmission] = {}
        self._last_command: str = ""
        self._last_exit_code: int = 0
        self._last_result: CommandResult | None = None
        self._pending_error: CommandResult | None = None

    @property
    def last_command(self) -> str:
        return self._last_command

    @property
    def last_exit_code(self) -> int:
        return self._last_exit_code

    @property
    def last_result(self) -> CommandResult | None:
        return self._last_result

    def register_user_command(self, command: str) -> None:
        self.register_command(command, source="user", command_seq=None)

    def register_backend_command(
        self, command: str, command_seq: int | None = None
    ) -> None:
        self.register_command(command, source="backend", command_seq=command_seq)

    def register_command(
        self,
        command: str,
        source: str,
        command_seq: int | None = None,
    ) -> None:
        command = command.strip()
        if not command:
            return
        self._store_submission(
            command=command,
            source=source,
            command_seq=command_seq,
        )

    def clear_error_correction(self) -> None:
        self._pending_error = None

    def consume_error(self) -> tuple[str, int] | None:
        if self._pending_error is None:
            return None
        result = self._pending_error
        self._pending_error = None
        return result.command, result.exit_code

    def handle_backend_event(
        self, event: BackendControlEvent
    ) -> CommandResult | None:
        if event.type == "command_started":
            command = str(event.payload.get("command") or "").strip()
            command_seq = self._coerce_command_seq(event.payload.get("command_seq"))
            submission = self._resolve_submission(
                command_seq=command_seq,
                command=command,
                create_if_missing=True,
            )
            if submission is not None and command:
                submission.command = command
            return None

        if event.type != "prompt_ready":
            return None

        command_seq = self._coerce_command_seq(event.payload.get("command_seq"))
        submission = self._resolve_submission(
            command_seq=command_seq,
            command=None,
            create_if_missing=False,
        )
        if submission is None or not submission.command:
            return None

        exit_code = self._coerce_exit_code(event.payload.get("exit_code"))
        interrupted = bool(event.payload.get("interrupted")) or exit_code == 130
        result = CommandResult(
            command=submission.command,
            exit_code=exit_code,
            source=submission.source,
            command_seq=command_seq,
            interrupted=interrupted,
        )

        self._last_command = result.command
        self._last_exit_code = result.exit_code
        self._last_result = result

        if result.source == "user" and result.exit_code != 0 and not result.interrupted:
            self._pending_error = result
        else:
            self._pending_error = None

        if command_seq is not None:
            self._submitted_by_seq.pop(command_seq, None)
        if self._active_submission is submission:
            self._active_submission = None
        return result

    def reset(self) -> None:
        self._active_submission = None
        self._submitted_by_seq.clear()
        self._last_command = ""
        self._last_exit_code = 0
        self._last_result = None
        self._pending_error = None

    def _store_submission(
        self,
        *,
        command: str,
        source: str,
        command_seq: int | None,
    ) -> CommandSubmission:
        submission = CommandSubmission(
            command=command,
            source=source,
            command_seq=command_seq,
        )
        self._active_submission = submission
        if command_seq is not None:
            self._submitted_by_seq[command_seq] = submission
        return submission

    def _resolve_submission(
        self,
        *,
        command_seq: int | None,
        command: str | None,
        create_if_missing: bool,
    ) -> CommandSubmission | None:
        if command_seq is not None:
            submission = self._submitted_by_seq.get(command_seq)
            if submission is not None:
                self._active_submission = submission
                return submission

        if self._active_submission is not None:
            active_seq = self._active_submission.command_seq
            if command_seq is None or active_seq is None or active_seq == command_seq:
                return self._active_submission

        if not create_if_missing:
            return None

        source = "backend" if command_seq is not None else "user"
        return self._store_submission(
            command=(command or self._last_command).strip(),
            source=source,
            command_seq=command_seq,
        )

    @staticmethod
    def _coerce_command_seq(value: object) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_exit_code(value: object) -> int:
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0