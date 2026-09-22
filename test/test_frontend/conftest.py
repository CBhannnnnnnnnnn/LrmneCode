"""前端测试共用的替身与夹具。

主应用壳需要 ``BackendClient``，测试里换成不拉起子进程的替身：
命令留在 ``client.sent`` 里，事件/回执由测试手工投喂。
"""

from __future__ import annotations

import pytest
from rich.text import Text

from frontend import app as app_module
from frontend.app import main as app_main
from proxy_layer.schema import make_event, make_receipt_success


class FakeClient:
    """BackendClient 替身：只记录发出的命令，不做任何 IO。"""

    def __init__(self, on_event, on_receipt, on_exit=None):
        self.on_event = on_event
        self.on_receipt = on_receipt
        self.on_exit = on_exit
        self.sent = []
        self.stopped = False

    async def start(self):
        return

    def send(self, command):
        self.sent.append(command)

    async def stop(self):
        self.stopped = True

    # -- 测试辅助 --

    @property
    def operations(self) -> list[str]:
        return [command.operation for command in self.sent]

    def last(self, operation: str):
        for command in reversed(self.sent):
            if command.operation == operation:
                return command
        return None


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def make_app(monkeypatch):
    # patch 的必须是**调用处所在模块**的全局：`from x import Name` 绑的是各模块自己的命名空间，
    # 拆包后只打包名会静默失效（FakeClient 不生效、测试真去拉后端子进程）。
    monkeypatch.setattr(app_main, "BackendClient", FakeClient)

    def factory():
        return app_module.LrmneAgentApp()

    return factory


def plain(widget) -> str:
    """部件或 rich 可渲染对象当前呈现的纯文本。"""
    if isinstance(widget, Text):
        return widget.plain
    renderable = widget.render()
    return getattr(renderable, "plain", str(renderable))


def show_providers(app, conv, providers) -> None:
    """模拟后端对 config.providers 的成功回执。"""
    command = app.client.last("config.providers")
    app._handle_receipt(
        make_receipt_success(command.request_id, conv.cid, providers),
    )


def show_config(app, conv, config) -> None:
    """模拟后端对 config.get 的成功回执。"""
    command = app.client.last("config.get")
    app._handle_receipt(make_receipt_success(command.request_id, conv.cid, config))


def show_sessions(app, conv, sessions) -> None:
    """模拟后端对 session.list 的成功回执。"""
    command = app.client.last("session.list")
    app._handle_receipt(make_receipt_success(command.request_id, conv.cid, sessions))


def emit(app, cid, name, data=None) -> None:
    app._dispatch_event(make_event(cid, name, data or {}))
