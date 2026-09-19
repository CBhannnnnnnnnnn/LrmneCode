"""后端子进程管理与行协议客户端。

前端不 import 后端业务代码，只通过 ``proxy_layer.schema`` 定义的协议与
子进程 ``proxy_layer.runtime`` 通信：stdin 写命令，stdout 读事件/回执。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from pathlib import Path
from typing import Callable

from proxy_layer.schema import (
    CommandProtocol,
    ErrorCode,
    EventProtocol,
    ReceiptProtocol,
    from_line,
    make_receipt_error,
    to_line,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 后端没有 __main__ 入口，这里用 -c 引导启动 Runtime 主循环。
#
# 其中对 create_subprocess_exec 注入 stdin=DEVNULL 是必须的 workaround：
# agentscope 工具链（LocalBackend.exec_shell）spawn 的 python shim 未显式
# 设置 stdin，会继承 backend 的管道 stdin 并卡死不退出，导致 communicate()
# 永久等待、ensure_agent 挂起（chat.send 无事件）。显式传入 stdin 的调用方
# （如 MCP stdio server 的 stdin=PIPE）不受影响。
_BOOTSTRAP = """import asyncio

_orig_spawn = asyncio.create_subprocess_exec


def _patched_spawn(*args, **kwargs):
    kwargs.setdefault("stdin", asyncio.subprocess.DEVNULL)
    return _orig_spawn(*args, **kwargs)


asyncio.create_subprocess_exec = _patched_spawn

from proxy_layer.runtime import Runtime  # noqa: E402

asyncio.run(Runtime().run())
"""


class BackendClient:
    """拉起后端子进程，按行读写协议消息并分发。

    回调均在同一事件循环内同步调用：
    - ``on_event``: 后端单向推送的事件
    - ``on_receipt``: 命令回执（成功/失败）
    - ``on_exit``: 后端进程退出（code, stderr 末尾若干行）
    """

    def __init__(
        self,
        on_event: Callable[[EventProtocol], None],
        on_receipt: Callable[[ReceiptProtocol], None],
        on_exit: Callable[[int, str], None] | None = None,
    ) -> None:
        self.on_event = on_event
        self.on_receipt = on_receipt
        self.on_exit = on_exit
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._pump_tasks: list[asyncio.Task] = []
        self._early: list[CommandProtocol] = []  # 启动完成前暂存的命令

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self._proc is not None:
            return

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(PROJECT_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)

        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _BOOTSTRAP,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._pump_tasks = [
            asyncio.create_task(self._pump_stdout()),
            asyncio.create_task(self._pump_stderr()),
        ]
        # 补发启动完成前暂存的命令
        early, self._early = self._early, []
        for command in early:
            self.send(command)

    def send(self, command: CommandProtocol) -> None:
        """写一行命令到后端 stdin；启动完成前暂存，启动后自动补发。"""
        proc = self._proc
        if proc is None or proc.stdin is None:
            self._early.append(command)
            return
        proc.stdin.write(to_line(command).encode("utf-8") + b"\n")
        asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            try:
                await self._proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def stop(self) -> None:
        """优雅退出：关 stdin 让 Runtime 读到 EOF；超时则逐级强杀。"""
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            # 进程已退出，显式关闭管道 transport，避免事件循环关闭后告警
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                transport.close()
        finally:
            for task in self._pump_tasks:
                task.cancel()
            self._pump_tasks.clear()

    # ---------- 内部泵 ----------

    async def _pump_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:  # EOF
                break
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = from_line(text)
            except Exception as exc:
                # 后端发来无法解释的行：包装成回执交给 UI 展示
                self.on_receipt(
                    make_receipt_error(
                        None,
                        None,
                        ErrorCode.INVALID_MESSAGE,
                        f"后端发来无法解析的消息：{exc}",
                    )
                )
                continue
            if isinstance(msg, EventProtocol):
                self.on_event(msg)
            elif isinstance(msg, ReceiptProtocol):
                self.on_receipt(msg)

        code = proc.returncode
        if code is None:
            code = await proc.wait()
        if self.on_exit is not None:
            self.on_exit(code, "\n".join(self._stderr_tail).strip())

    async def _pump_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                break
            self._stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
