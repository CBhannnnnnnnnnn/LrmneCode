"""系统测试：真实 Runtime 回路。每种命令模式一条，外加失败路径。不调用模型。"""

import asyncio

from pydantic import BaseModel

from proxy_layer.register import register_command
from proxy_layer.runtime import Runtime
from proxy_layer.schema import (
    CommandMode,
    ErrorCode,
    MessageType,
    from_line,
    make_command,
    make_event,
    to_line,
)
import proxy_layer.runtime as runtime_mod


class _Params(BaseModel):
    pass


def _run(monkeypatch, lines, wait_for=0):
    written = []
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
    asyncio.run(asyncio.wait_for(Runtime().run(), timeout=5))
    return [from_line(line) for line in written]


def test_system_rejects_invalid_line(monkeypatch):
    messages = _run(monkeypatch, ["{not json"], wait_for=1)

    assert messages[0].error_code == ErrorCode.INVALID_MESSAGE
    assert messages[0].request_id is None


def test_system_ignores_non_command(monkeypatch):
    messages = _run(monkeypatch, [to_line(make_event("c-1", "token"))])

    assert messages == []


def test_system_plain_config_get(monkeypatch):
    line = to_line(make_command("req-1", "c-sys", "config.get", {"key": "mode"}))

    messages = _run(monkeypatch, [line], wait_for=1)

    assert messages[0].message_type == MessageType.RECEIPT
    assert messages[0].accepted is True
    assert messages[0].request_id == "req-1"
    assert "mode" in messages[0].result


def test_system_control_interrupt(monkeypatch):
    line = to_line(make_command("req-1", "c-sys", "chat.interrupt", {}))

    messages = _run(monkeypatch, [line], wait_for=1)

    assert messages[0].accepted is True
    assert messages[0].result["interrupted"] is True


def test_system_wait_command_returns_result(monkeypatch):
    async def ping(params: _Params):
        return "pong"

    register_command("unit", "ping", CommandMode.WAIT)(ping)
    line = to_line(make_command("req-1", "c-sys", "unit.ping", {}))
    try:
        messages = _run(monkeypatch, [line], wait_for=1)
    finally:
        import proxy_layer.register as register

        register._registry.pop("unit", None)

    assert messages[0].accepted is True
    assert messages[0].result == "pong"


def test_system_unknown_operation(monkeypatch):
    line = to_line(make_command("req-1", "c-sys", "unit.missing", {}))

    messages = _run(monkeypatch, [line], wait_for=1)

    assert messages[0].accepted is False
    assert messages[0].error_code == ErrorCode.UNKNOWN_OPERATION
