from __future__ import annotations

import os
import select
import time

from aish.pty.manager import PTYManager
from aish.pty.control_protocol import decode_control_chunk


def test_decode_control_chunk_handles_partial_ndjson_frames():
    first = b'{"version":1,"type":"session_ready","ts":1}\n{"version":1'
    second = b',"type":"prompt_ready","ts":2}\n'

    events, remainder, errors = decode_control_chunk(b"", first)

    assert [event.type for event in events] == ["session_ready"]
    assert remainder == b'{"version":1'
    assert errors == []

    events, remainder, errors = decode_control_chunk(remainder, second)

    assert [event.type for event in events] == ["prompt_ready"]
    assert remainder == b""
    assert errors == []


def test_decode_control_chunk_reports_invalid_lines_without_dropping_valid_events():
    chunk = b'{"version":1,"type":"session_ready","ts":1}\nnot-json\n'

    events, remainder, errors = decode_control_chunk(b"", chunk)

    assert [event.type for event in events] == ["session_ready"]
    assert remainder == b""
    assert len(errors) == 1


def test_pty_manager_emits_control_events_for_user_command():
    manager = PTYManager(use_output_thread=False)

    try:
        manager.start()
        manager.register_user_command("echo hello")
        manager.send(b"echo hello\n")

        saw_started = False
        saw_ready = False
        deadline = time.monotonic() + 5.0

        while time.monotonic() < deadline and not (saw_started and saw_ready):
            ready, _, _ = select.select([manager.control_fd, manager._master_fd], [], [], 0.1)
            for fd in ready:
                data = os.read(fd, 4096)
                if not data:
                    continue
                if fd == manager.control_fd:
                    events, errors = manager.decode_control_events(data)
                    assert errors == []
                    for event in events:
                        result = manager.handle_backend_event(event)
                        if event.type == "command_started":
                            saw_started = event.payload.get("command") == "echo hello"
                        if event.type == "prompt_ready":
                            saw_ready = event.payload.get("exit_code") == 0
                            assert result is not None
                            assert result.command == "echo hello"
                else:
                    continue

        assert saw_started is True
        assert saw_ready is True
        assert manager.last_command == "echo hello"
        assert manager.last_exit_code == 0
    finally:
        manager.stop()


def test_pty_manager_execute_command_returns_output_without_marker():
    manager = PTYManager(use_output_thread=False)

    try:
        manager.start()
        output, exit_code = manager.execute_command("printf 'hello\\n'")

        assert exit_code == 0
        assert output == "hello"
        assert "[AISH_EXIT:" not in output
    finally:
        manager.stop()