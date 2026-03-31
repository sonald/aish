"""Focused unit tests for the PTY shell core."""

from __future__ import annotations

from unittest.mock import Mock

from aish.i18n import t
from aish.pty.control_protocol import BackendControlEvent
from aish.pty.manager import PTYManager
from aish.shell.runtime.app import PTYAIShell
from aish.shell.runtime.output import OutputProcessor
from aish.shell.runtime.router import InputRouter
from aish.shell.ui.placeholder import PlaceholderManager


class _FakeTracker:
    def __init__(self, *, has_exit_code: bool = False, error_info=None):
        self._has_exit_code = has_exit_code
        self._error_info = error_info
        self.last_exit_code = 0
        self.last_command = ""
        self.clear_exit_available = Mock()
        self.set_last_command = Mock(side_effect=self._remember_command)

    def _remember_command(self, command: str) -> None:
        self.last_command = command

    def has_exit_code(self) -> bool:
        return self._has_exit_code

    def consume_error(self):
        return self._error_info


class _FakePTYManager:
    def __init__(self, tracker: _FakeTracker | None = None):
        self.sent: list[bytes] = []
        self.exit_tracker = tracker or _FakeTracker()
        self._master_fd = 1

    def send(self, data: bytes) -> int:
        self.sent.append(data)
        return len(data)


def test_input_router_routes_semicolon_question_to_ai_handler(capsys):
    pty_manager = _FakePTYManager()
    ai_handler = Mock()
    router = InputRouter(pty_manager, ai_handler)

    router.handle_input(b";hello\r")

    ai_handler.handle_question.assert_called_once_with("hello")
    ai_handler.handle_error_correction.assert_not_called()
    assert pty_manager.sent == []
    assert ";hello" in capsys.readouterr().out


def test_input_router_routes_bare_semicolon_to_error_correction(capsys):
    pty_manager = _FakePTYManager()
    ai_handler = Mock()
    router = InputRouter(pty_manager, ai_handler)

    router.handle_input(b";\r")

    ai_handler.handle_error_correction.assert_called_once_with()
    ai_handler.handle_question.assert_not_called()
    assert pty_manager.sent == []
    assert ";" in capsys.readouterr().out


def test_input_router_marks_normal_command_as_waiting_for_result():
    tracker = _FakeTracker()
    pty_manager = _FakePTYManager(tracker=tracker)
    ai_handler = Mock()
    output_processor = Mock()
    submit_callback = Mock(return_value=1)
    router = InputRouter(
        pty_manager,
        ai_handler,
        output_processor=output_processor,
        command_submit_callback=submit_callback,
    )

    router.handle_input(b"ls\r")

    assert pty_manager.sent == [b"l", b"s", b"\r"]
    submit_callback.assert_called_once_with("ls")
    tracker.set_last_command.assert_called_once_with("ls")
    output_processor.set_waiting_for_result.assert_called_once_with(True, "ls")
    output_processor.set_filter_exit_echo.assert_not_called()


def test_input_router_marks_exit_command_for_echo_filtering():
    tracker = _FakeTracker()
    pty_manager = _FakePTYManager(tracker=tracker)
    ai_handler = Mock()
    output_processor = Mock()
    router = InputRouter(pty_manager, ai_handler, output_processor=output_processor)

    router.handle_input(b"exit\r")

    tracker.set_last_command.assert_called_once_with("exit")
    output_processor.set_filter_exit_echo.assert_called_once_with(True)
    output_processor.set_waiting_for_result.assert_not_called()


def test_output_processor_filters_exit_echo():
    processor = OutputProcessor(_FakePTYManager())
    processor.set_filter_exit_echo(True)

    assert processor.process(b"\rexit\r\n") == b""


def test_output_processor_appends_placeholder_after_prompt():
    placeholder_manager = Mock()
    placeholder_manager.show_placeholder.return_value = b"<hint>"
    processor = OutputProcessor(_FakePTYManager(), placeholder_manager=placeholder_manager)

    rendered = processor.process(b"prompt\x1b[0m ")

    assert rendered.endswith(b"<hint>")
    placeholder_manager.show_placeholder.assert_called_once_with()


def test_output_processor_does_not_append_placeholder_on_plain_m_space_tail():
    placeholder_manager = Mock()
    placeholder_manager.show_placeholder.return_value = b"<hint>"
    processor = OutputProcessor(_FakePTYManager(), placeholder_manager=placeholder_manager)

    rendered = processor.process(b"normal output m ")

    assert rendered == b"normal output m "
    placeholder_manager.show_placeholder.assert_not_called()


def test_output_processor_prints_error_hint_when_command_fails(capsys):
    tracker = _FakeTracker(has_exit_code=True, error_info=("bad command", 1))
    processor = OutputProcessor(_FakePTYManager(tracker=tracker))
    processor._waiting_for_result = True

    rendered = processor.process(b"stderr output")

    assert rendered == b"stderr output"
    assert processor._waiting_for_result is False
    tracker.clear_exit_available.assert_called_once_with()
    assert t("shell.error_correction.press_semicolon_hint") in capsys.readouterr().out


def test_input_router_semicolon_after_ctrl_a_keeps_tracking_safe():
    pty_manager = _FakePTYManager()
    ai_handler = Mock()
    router = InputRouter(pty_manager, ai_handler)

    router.handle_input(b"abc")
    router.handle_input(b"\x01")
    router.handle_input(b";")

    assert pty_manager.sent == [b"a", b"b", b"c", b"\x01", b";"]
    assert router._current_cmd == "abc"
    assert router._cursor_tracking_dirty is True


def test_input_router_recovers_when_bracketed_paste_end_missing():
    pty_manager = _FakePTYManager()
    ai_handler = Mock()
    router = InputRouter(pty_manager, ai_handler)

    router.handle_input(b"\x1b[200~abc")
    assert router._in_bracketed_paste is True

    router.handle_input(b"\x03")

    assert router._in_bracketed_paste is False
    assert pty_manager.sent == [b"a", b"b", b"c", b"\x03"]


def test_input_router_handles_bracketed_paste_start_mid_chunk():
    pty_manager = _FakePTYManager()
    ai_handler = Mock()
    router = InputRouter(pty_manager, ai_handler)

    router.handle_input(b"ab\x1b[200~cd\x1b[201~ef")

    assert router._in_bracketed_paste is False
    assert pty_manager.sent == [b"a", b"b", b"c", b"d", b"e", b"f"]


def test_input_router_escape_key_clears_placeholder_before_forwarding():
    pty_manager = _FakePTYManager()
    ai_handler = Mock()
    placeholder_manager = Mock()
    placeholder_manager.is_visible.return_value = True
    placeholder_manager.clear_placeholder.return_value = b""

    router = InputRouter(
        pty_manager,
        ai_handler,
        placeholder_manager=placeholder_manager,
    )

    router.handle_input(b"\x1b[A")

    assert pty_manager.sent == [b"\x1b[A"]
    placeholder_manager.clear_placeholder.assert_called_once_with()
    placeholder_manager.mark_cleared.assert_called_once_with()


def test_placeholder_manager_show_placeholder_is_idempotent():
    interruption_manager = Mock()
    interruption_manager.state = None
    interruption_manager.get_prompt_message.return_value = None
    manager = PlaceholderManager(interruption_manager)

    first = manager.show_placeholder()
    second = manager.show_placeholder()

    assert first
    assert second == b""


def test_placeholder_manager_is_disabled_by_default_from_environment(monkeypatch):
    monkeypatch.delenv("AISH_ENABLE_PLACEHOLDER", raising=False)
    interruption_manager = Mock()
    interruption_manager.state = None
    interruption_manager.get_prompt_message.return_value = None

    manager = PlaceholderManager.from_environment(interruption_manager)

    assert manager.show_placeholder() == b""
    assert manager.is_visible() is False


def test_pty_manager_send_command_injects_command_seq():
    manager = object.__new__(PTYManager)
    manager._exit_tracker = _FakeTracker()
    sent: list[bytes] = []

    def _fake_send(data: bytes) -> int:
        sent.append(data)
        return len(data)

    manager.send = _fake_send  # type: ignore[method-assign]

    PTYManager.send_command(manager, "echo hi", command_seq=7)

    assert sent == [b"echo hi\n"]
    assert manager._exit_tracker.last_command == "echo hi"