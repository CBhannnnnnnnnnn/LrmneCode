"""client 单元测试：只锁发送缓冲和 stdout 分发，不拉起后端子进程。"""

import asyncio

from frontend.client import BackendClient
from proxy_layer.schema import (
    ErrorCode,
    make_command,
    make_event,
    make_receipt_success,
    to_line,
)


class _Reader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _Stdin:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        return None


class _Proc:
    def __init__(self, stdout=(), stderr=(), returncode=0):
        self.stdout = _Reader(stdout)
        self.stderr = _Reader(stderr)
        self.stdin = _Stdin()
        self.returncode = returncode


def _client():
    events = []
    receipts = []
    exits = []
    client = BackendClient(events.append, receipts.append, lambda code, text: exits.append((code, text)))
    return client, events, receipts, exits


def test_send_queues_until_process_exists():
    client, _, _, _ = _client()
    command = make_command("req-1", "c-1", "config.get", {})

    client.send(command)

    assert client.running is False
    assert client._early == [command]


def test_running_requires_a_live_process():
    client, _, _, _ = _client()
    client._proc = _Proc(returncode=None)

    assert client.running is True

    client._proc.returncode = 0
    assert client.running is False


def test_send_writes_one_protocol_line():
    client, _, _, _ = _client()
    client._proc = _Proc(returncode=None)
    command = make_command("req-1", "c-1", "config.get", {"key": "mode"})

    async def body():
        client.send(command)
        await asyncio.sleep(0)

    asyncio.run(body())

    assert client._proc.stdin.data == (to_line(command) + "\n").encode("utf-8")


def test_stdout_dispatches_events_and_receipts_then_reports_exit():
    client, events, receipts, exits = _client()
    event = make_event("c-1", "token", {"text": "hi"})
    receipt = make_receipt_success("req-1", "c-1", result={"ok": True})
    client._proc = _Proc(
        stdout=[
            b"\n",
            (to_line(event) + "\n").encode("utf-8"),
            (to_line(receipt) + "\n").encode("utf-8"),
            b"",
        ],
        returncode=0,
    )

    asyncio.run(client._pump_stdout())

    assert [item.event for item in events] == ["token"]
    assert receipts[0].result == {"ok": True}
    assert exits == [(0, "")]


def test_stdout_wraps_unreadable_line_as_invalid_message():
    client, _, receipts, _ = _client()
    client._proc = _Proc(stdout=[b"not-json\n", b""], returncode=0)

    asyncio.run(client._pump_stdout())

    assert receipts[0].error_code == ErrorCode.INVALID_MESSAGE
    assert receipts[0].request_id is None


def test_stderr_keeps_a_tail():
    client, _, _, _ = _client()
    client._proc = _Proc(stderr=[b"warn\n", b""])

    asyncio.run(client._pump_stderr())

    assert list(client._stderr_tail) == ["warn"]
