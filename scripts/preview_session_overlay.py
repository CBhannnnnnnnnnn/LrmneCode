"""把会话切换浮窗渲染成纯文本，人工核对视觉与信息密度（非断言型脚本）。"""

import asyncio
import sys

sys.path.insert(0, "test/test_frontend")

from conftest import FakeClient, show_sessions  # noqa: E402

from frontend import app as app_module  # noqa: E402


async def main() -> None:
    app_module.BackendClient = FakeClient
    app = app_module.LrmneAgentApp()

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        # 造两个会话：第一个看起来正在干活，第二个（当前）比较空闲
        first = app.store.current
        first.tokens_in = 12400
        first.tokens_out = 1830
        first.inflight.add("req-preview")
        app.new_conversation()
        await pilot.pause()
        app.store.current.tokens_in = 900
        await pilot.pause()

        # 回执按信封上的 cid 路由，这里必须用当前会话发
        current = app.store.current
        app.open_sessions()
        await pilot.pause()
        show_sessions(app, current, [
            {"cid": first.cid, "summary": "修复登录并发", "modified": 1758000000.0},
            {"cid": current.cid, "summary": "把 provider schema 接进配置浮窗", "modified": 1757900000.0},
            {"cid": "c-ancient", "summary": "", "modified": 1757000000.0},
        ])
        await pilot.pause()

        strips = app.screen._compositor.render_strips()
        for strip in strips:
            print("".join(segment.text for segment in strip).rstrip())


asyncio.run(main())
