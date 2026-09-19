"""transport 单元测试：只锁标准输入输出的一行读写，不启动运行时。"""

import asyncio
import io
import sys

from proxy_layer.transport import log, read_line, write_line


class _Buf:
    def __init__(self):
        self.parts = []
        self.flushed = False

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        self.flushed = True

    def getvalue(self):
        return "".join(self.parts)


def test_read_line_strips_newline_and_spaces(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("  hello \n"))

    assert asyncio.run(read_line()) == "hello"


def test_read_line_keeps_blank_line_distinct_from_eof(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

    assert asyncio.run(read_line()) == ""


def test_read_line_returns_none_on_eof(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert asyncio.run(read_line()) is None


def test_write_line_appends_newline_and_flushes(monkeypatch):
    buf = _Buf()
    monkeypatch.setattr(sys, "stdout", buf)

    asyncio.run(write_line("你好"))

    assert buf.getvalue() == "你好\n"
    assert buf.flushed is True


def test_log_writes_to_stderr_and_flushes(monkeypatch):
    buf = _Buf()
    monkeypatch.setattr(sys, "stderr", buf)

    log("boom")

    assert buf.getvalue() == "boom\n"
    assert buf.flushed is True
