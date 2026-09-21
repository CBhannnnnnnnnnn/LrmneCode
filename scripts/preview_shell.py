"""把浮窗层与状态栏渲染成纯文本，人工核对观感（非断言型脚本）。

用法：.venv/Scripts/python.exe scripts/preview_shell.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "test/test_frontend")

from conftest import FakeClient  # noqa: E402

from frontend import app as app_module  # noqa: E402
from proxy_layer.schema import make_event, make_receipt_success  # noqa: E402

OPENAI = {
    "title": "OpenAI API",
    "type": "object",
    "required": ["api_key"],
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "const": "openai_credential"},
        "api_key": {"type": "string", "format": "password", "title": "Api Key"},
        "base_url": {
            "type": "string",
            "title": "Base Url",
            "default": "https://api.openai.com/v1",
        },
    },
}
DASHSCOPE = {
    "title": "DashScope",
    "type": "object",
    "required": ["api_key"],
    "properties": {
        "type": {"type": "string", "const": "dashscope_credential"},
        "api_key": {"type": "string", "format": "password", "title": "Api Key"},
    },
}
OLLAMA = {
    "title": "Ollama",
    "type": "object",
    "required": [],
    "properties": {
        "type": {"type": "string", "const": "ollama_credential"},
        "host": {"type": "string", "title": "Host", "default": "http://localhost:11434"},
    },
}

CONFIG = {
    "model": {
        "configured": True,
        "provider_type": "openai_credential",
        "credential": {"api_key": {"configured": True, "suffix": "9f3c"}},
        "model": "claude-sonnet-4-5",
        "thinking_level": "medium",
        "context_size": 200000,
    },
    "permission": {"mode": "accept_edits"},
    "workspace": {"root": "D:/code/LrmneAgent", "agent_home": "D:/code/LrmneAgent/.lrmne"},
}


def dump(app, title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    for strip in app.screen._compositor.render_strips():
        print("".join(segment.text for segment in strip).rstrip())


async def main() -> None:
    app_module.BackendClient = FakeClient
    app = app_module.LrmneAgentApp()

    # 120 列：状态栏的 ctx / 计数 / 吞吐 / 缓存都放得下，再多就轮到配置段被裁
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        # 主屏：状态栏（模型 / 思考 / 权限 / 目录 / 上下文压力 / token）
        app._dispatch_receipt(
            make_receipt_success(app.client.last("config.get").request_id, cid, CONFIG)
        )
        # 状态栏的 tok/s 按 model.start→end 的间隔现算，中间得真等一会儿
        # （1.6s 出 128 个 token ≈ 80 tok/s，跟真跑一轮的量级对得上）
        app._dispatch_event(make_event(cid, "model.start", {"model_name": "claude-sonnet-4-5"}))
        await asyncio.sleep(1.6)
        app._dispatch_event(
            make_event(
                cid,
                "model.end",
                {
                    "input_tokens": 132_000,
                    "output_tokens": 128,
                    "cache_input_tokens": 118_000,
                },
            )
        )
        await pilot.pause()
        dump(app, "主屏：状态栏（128k/200k 上下文）")

        # 模型配置浮窗
        app.open_model_config()
        await pilot.pause()
        overlay = app.model_config_overlay
        app._dispatch_receipt(
            make_receipt_success(app.client.last("config.providers").request_id, cid, [OPENAI, DASHSCOPE, OLLAMA])
        )
        app._dispatch_receipt(
            make_receipt_success(app.client.last("config.get").request_id, cid, CONFIG)
        )
        await pilot.pause()
        panel = overlay.query_one(".overlay-panel")
        print(
            f"\n[面板] 屏幕 {app.size.width}x{app.size.height} "
            f"面板 region={panel.region}（上留 {panel.region.y} 行，下留 "
            f"{app.size.height - panel.region.bottom} 行）"
        )
        dump(app, "模型配置浮窗")

        # 选择卡片
        app.close_overlays()
        await pilot.pause()
        app.input_area.set_text("/permission")
        await pilot.press("enter")
        await pilot.pause()
        dump(app, "选择卡片：权限模式")


asyncio.run(main())
