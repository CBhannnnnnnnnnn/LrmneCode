"""runtime 单元测试：只锁入站分拣和出站包装，调度器用假实现。"""

import asyncio

import pytest

from proxy_layer.runtime import Runtime
from proxy_layer.schema import (
    ErrorCode,
    MessageType,
    from_line,
    make_command,
    make_event,
    make_receipt_success,
    to_line,
)
import proxy_layer.runtime as runtime_mod


def _patch_io(monkeypatch, lines, written, wait_for=0):
    pending = list(lines)

    async def read_line():
        if pending:
            return pending.pop(0)
        while len(written) < wait_for:
            await asyncio.sleep(0)
        return None

    async def write_line(line):
        written.append(line)

    monkeypatch.setattr(runtime_mod, "read_line", read_line)
    monkeypatch.setattr(runtime_mod, "write_line", write_line)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=2))


def test_as_frame_passes_protocol_through():
    receipt = make_receipt_success("req-1", "c-1", result=1)

    assert Runtime()._as_frame(receipt, "c-other") is receipt


def test_as_frame_wraps_dict_as_event():
    frame = Runtime()._as_frame({"event": "token", "data": {"text": "hi"}}, "c-1")

    assert frame.message_type == MessageType.EVENT
    assert frame.event == "token"
    assert frame.data == {"text": "hi"}
    assert frame.conversation_id == "c-1"


def test_as_frame_defaults_missing_event_data():
    frame = Runtime()._as_frame({"event": "token"}, "c-1")

    assert frame.data == {}


def test_as_frame_rejects_unknown_chunk():
    with pytest.raises(TypeError, match="int"):
        Runtime()._as_frame(1, "c-1")


def test_run_skips_blank_line_and_non_command(monkeypatch):
    written = []
    _patch_io(
        monkeypatch,
        ["", to_line(make_event("c-1", "token")), None],
        written,
    )

    _run(Runtime().run())

    assert written == []


def test_run_replies_invalid_message_for_bad_line(monkeypatch):
    written = []
    _patch_io(monkeypatch, ["{not json", None], written)

    _run(Runtime().run())

    receipt = from_line(written[0])
    assert receipt.accepted is False
    assert receipt.error_code == ErrorCode.INVALID_MESSAGE
    assert receipt.request_id is None
    assert receipt.conversation_id is None


def test_run_writes_scheduler_chunks_as_protocol_lines(monkeypatch):
    written = []
    _patch_io(
        monkeypatch,
        [to_line(make_command("req-1", "c-1", "unit.ping"))],
        written,
        wait_for=2,
    )

    async def submit(msg):
        yield make_receipt_success(msg.request_id, msg.conversation_id, result="pong")
        yield {"event": "token", "data": {"text": "hi"}}

    monkeypatch.setattr(runtime_mod.scheduler, "submit", submit)

    _run(Runtime().run())

    first = from_line(written[0])
    second = from_line(written[1])
    assert first.accepted is True
    assert first.result == "pong"
    assert second.event == "token"
    assert second.data == {"text": "hi"}
    assert second.conversation_id == "c-1"


def test_execute_turns_scheduler_crash_into_internal_error(monkeypatch):
    written = []

    async def write_line(line):
        written.append(line)

    async def submit(msg):
        raise RuntimeError("boom")
        yield None

    monkeypatch.setattr(runtime_mod, "write_line", write_line)
    monkeypatch.setattr(runtime_mod.scheduler, "submit", submit)

    _run(Runtime()._execute(make_command("req-1", "c-1", "unit.ping")))

    receipt = from_line(written[0])
    assert receipt.accepted is False
    assert receipt.error_code == ErrorCode.INTERNAL_ERROR
    assert receipt.request_id == "req-1"
    assert "boom" in receipt.error_message


def test_execute_turns_bad_chunk_into_internal_error(monkeypatch):
    written = []

    async def write_line(line):
        written.append(line)

    async def submit(msg):
        yield 1

    monkeypatch.setattr(runtime_mod, "write_line", write_line)
    monkeypatch.setattr(runtime_mod.scheduler, "submit", submit)

    _run(Runtime()._execute(make_command("req-1", "c-1", "unit.ping")))

    receipt = from_line(written[0])
    assert receipt.error_code == ErrorCode.INTERNAL_ERROR
    assert "int" in receipt.error_message
