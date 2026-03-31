from __future__ import annotations

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