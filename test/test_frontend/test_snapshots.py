"""六个关键屏的渲染快照基线。

这些是前端重构期间的真值：任何一片搬运让它们变红，就说明搬坏了东西。
屏态构造复用 `conftest` 里已经跑通的驱动接缝（`make_app` 换掉 `BackendClient`，
事件与回执由测试手工投喂），不新造一套。
"""

from __future__ import annotations

import sys

import pytest

from conftest import emit, show_config, show_providers, show_sessions
from frontend import widgets as _widgets  # noqa: F401  导入即装载各子模块
from frontend.overlay import Choice
from snapshot import SIZE, snap


@pytest.fixture(autouse=True)
def _fixed_elapsed_width(monkeypatch):
    """把耗时文本钉成定宽。

    耗时的**字符数**会随时钟变（`0ms` ↔ `1.0s`），多出来那一格会把内容高度推过滚动条阈值，
    整块宽度再抖一格，基线就复现不出来。不能冻 `time.monotonic`——Textual 的定时器会空转挂死。
    改钉这个纯格式化函数：长度恒定 ⟹ 不换行 ⟹ 布局确定。

    逐个模块打补丁而不是只打 ``frontend.widgets``：`from X import name` 绑的是各模块自己的
    全局，拆包之后只打包名会**静默失效**。
    """
    def fixed(seconds: float) -> str:
        return "T"

    targets = [
        module
        for name, module in list(sys.modules.items())
        if name.startswith("frontend") and hasattr(module, "_fmt_elapsed")
    ]
    for module in targets:
        monkeypatch.setattr(module, "_fmt_elapsed", fixed)
    assert targets, "没找到 _fmt_elapsed 的宿主模块，夹具已失效"

PROVIDERS = [
    {
        "title": "OpenAI API",
        "type": "object",
        "required": ["api_key"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "openai_credential"},
            "name": {"type": "string", "default": ""},
            "api_key": {"type": "string", "format": "password", "title": "Api Key"},
            "base_url": {
                "type": "string",
                "title": "Base Url",
                "default": "https://api.openai.com/v1",
            },
        },
    },
    {
        "title": "Ollama API",
        "type": "object",
        "required": [],
        "properties": {
            "type": {"type": "string", "const": "ollama_credential"},
            "name": {"type": "string", "default": ""},
            "host": {"type": "string", "title": "Host", "default": "http://localhost:11434"},
        },
    },
]

CONFIGURED = {
    "model": {
        "configured": True,
        "provider_type": "openai_credential",
        "credential": {"api_key": {"configured": True, "suffix": "abcd"}},
        "model": "gpt-4o",
        "thinking_level": "medium",
        "context_size": 32768,
    },
    "permission": {"mode": "default"},
    "workspace": {"root": "/tmp/project"},
}

DISK_SESSIONS = [
    {"cid": "c-old", "summary": "修复登录并发", "modified": 1758000000.0},
    {"cid": "c-ancient", "summary": "", "modified": 1757000000.0},
]


async def _feed_transcript(app, pilot) -> None:
    """一段思考 + 一段正文 + 一次带 diff 的工具调用及其结果。"""
    cid = app.store.current.cid
    emit(app, cid, "stream.thinking.start", {"reply_id": "r1", "block_id": "th1"})
    emit(app, cid, "stream.thinking", {
        "reply_id": "r1", "block_id": "th1",
        "text_delta": "先看现有结构，再决定按职责还是按屏幕区域切。",
    })
    emit(app, cid, "stream.thinking.end", {"reply_id": "r1", "block_id": "th1"})

    emit(app, cid, "stream.text.start", {"reply_id": "r1", "block_id": "b1"})
    emit(app, cid, "stream.text", {
        "reply_id": "r1", "block_id": "b1",
        "text_delta": "三个大文件各自变成一个包，顺序自底向上。",
    })
    emit(app, cid, "stream.text.end", {"reply_id": "r1", "block_id": "b1"})

    emit(app, cid, "tool.call.start", {"tool_call_id": "t1", "name": "Edit"})
    emit(app, cid, "tool.call.delta", {
        "tool_call_id": "t1",
        "delta": '{"file_path": "/p/a.py", "old_string": "old line", "new_string": "new line"}',
    })
    emit(app, cid, "tool.call.end", {"tool_call_id": "t1"})
    emit(app, cid, "tool.result.delta", {"tool_call_id": "t1", "delta": "已写入 1 处改动"})
    emit(app, cid, "tool.result.end", {"tool_call_id": "t1", "state": "success"})
    await pilot.pause()


@pytest.mark.anyio
async def test_snapshot_shell_idle(make_app):
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await snap(app, pilot, "shell_idle")


@pytest.mark.anyio
async def test_snapshot_transcript(make_app):
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await _feed_transcript(app, pilot)
        await snap(app, pilot, "transcript")


@pytest.mark.anyio
async def test_snapshot_approval_panel(make_app):
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        emit(app, app.store.current.cid, "approval.request", {
            "approval_request_id": "a1",
            "tool_calls": [{
                "name": "Edit",
                "input": '{"file_path": "/p/a.py", "old_string": "old line", "new_string": "new line"}',
            }],
        })
        await pilot.pause()
        await snap(app, pilot, "approval_panel")


@pytest.mark.anyio
async def test_snapshot_picker(make_app):
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        app.open_picker(
            "appearance",
            "选择外观",
            [Choice("石墨", "默认，中性灰阶", "graphite"), Choice("浅色", "", "light")],
            current="graphite",
        )
        await pilot.pause()
        await snap(app, pilot, "picker")


@pytest.mark.anyio
async def test_snapshot_model_config(make_app):
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        conv = app.store.current
        app.open_model_config()
        await pilot.pause()
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()
        await snap(app, pilot, "model_config")


@pytest.mark.anyio
async def test_snapshot_session_switcher(make_app):
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        conv = app.store.current
        app.open_sessions()
        await pilot.pause()
        show_sessions(app, conv, DISK_SESSIONS)
        await pilot.pause()
        await snap(app, pilot, "session_switcher")
