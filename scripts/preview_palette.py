"""把 ``/`` 命令面板与 ``@`` 提及面板渲染成纯文本，人工核对观感（非断言型脚本）。

``@`` 走的是「逐层浏览 + 关键字搜」：空 ``@`` 看当前层、目录带斜杠、Enter 往下钻。

用法：``PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/preview_palette.py``
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "test/test_frontend")

from conftest import FakeClient  # noqa: E402

from frontend import app as app_module  # noqa: E402
from proxy_layer.schema import make_receipt_success  # noqa: E402

SKILLS = [
    {"name": "review", "description": "按清单审查当前改动"},
    {"name": "commit", "description": "整理并提交这一轮改动"},
]


def show(app) -> None:
    for strip in app.screen._compositor.render_strips():
        print("".join(segment.text for segment in strip).rstrip())


def heading(title: str) -> None:
    print("=" * 100)
    print(title)
    print("=" * 100)


async def main() -> None:
    app_module.BackendClient = FakeClient
    app = app_module.LrmneAgentApp()

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        # 假装配置已经到了：工作目录就是本仓库
        app.remember_config({"root": os.getcwd()})

        heading("/ 命令面板")
        await pilot.press("/", "m")
        await pilot.pause()
        show(app)

        heading("@ 提及面板：空 @ 列当前这一层（目录在前，skill 在最上面）")
        app.input_area.set_text("请帮我看下 @")
        await pilot.pause()
        app._handle_receipt(
            make_receipt_success(
                app.client.last("skill.list").request_id, app.store.current.cid, SKILLS
            )
        )
        await pilot.pause()
        show(app)

        heading("@ 提及面板：打了字就在这一层之下递归搜")
        app.input_area.set_text("请帮我看下 @work")
        await pilot.pause()
        show(app)

        heading("目录上回车：只补成 @backend/ 不加空格，面板接着列下一层")
        app.input_area.set_text("请帮我看下 @")
        await pilot.pause()
        index = next(
            offset
            for offset, item in enumerate(app.palette._shown)
            if item.kind == "dir"
        )
        app.palette.highlighted = index
        directory = app.palette.current_mention
        app._accept_mention(directory)
        await pilot.pause()
        print("选中 =", directory.label)
        print("input =", repr(app.input_area.text), "palette =", app.palette.display)
        print("这一层的候选 =", [item.label for item in app.palette._shown])
        show(app)

        heading("文件上回车：补成 @标签 + 空格，面板收起")
        app.input_area.set_text("请帮我看下 @backend/adapter/")
        await pilot.pause()
        print("候选 =", [item.label for item in app.palette._shown])
        index = next(
            offset
            for offset, item in enumerate(app.palette._shown)
            if item.kind == "file"
        )
        app.palette.highlighted = index
        app._accept_mention(app.palette.current_mention)
        await pilot.pause()
        print("input =", repr(app.input_area.text))
        print("cursor =", app.input_area.cursor_location, "palette =", app.palette.display)

        heading("正文里的 @引用 带底色（渲染成文本看不出色，这里另给段样式）")
        app.input_area.set_text("请帮我看下 @backend/workspace.py 再改 @pyproject.toml，谢谢")
        await pilot.pause()
        for segment in app.input_area.render_line(0):
            if segment.text.strip():
                print(f"  {segment.text!r:40} {segment.style}")

        heading("发送：@路径 只是提示词里的一行字，不带附件")
        text = app.input_area.text
        app.input_area.set_text("请帮我看下 @backend/workspace.py 再改 @pyproject.toml，谢谢")
        await pilot.pause()
        app.send_chat(app.input_area.text)
        await pilot.pause()
        command = app.client.last("chat.send")
        print("发出去之前输入框里是 =", repr(text))
        print("text =", repr(command.parameters["text"]))
        print("attachments =", command.parameters.get("attachments", "无"))
        show(app)


asyncio.run(main())
