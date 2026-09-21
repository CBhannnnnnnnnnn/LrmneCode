"""把工具调用 / 审批 / 思考 / 系统提示渲染成纯文本，人工核对观感（非断言型脚本）。"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "test/test_frontend")

from conftest import FakeClient  # noqa: E402

from frontend import app as app_module  # noqa: E402
from proxy_layer.schema import make_event  # noqa: E402

EDIT = {
    "file_path": "D:/code/demo/auth.py",
    "old_string": "token = read_token()\nreturn token",
    "new_string": "token = read_token(timeout=5)\naudit(token)\nreturn token",
}

LONG_COMMAND = (
    "$ProgressPreference='SilentlyContinue'; try { $r = Invoke-WebRequest "
    "-Uri 'https://example.com/a' -UseBasicParsing -UserAgent 'Mozilla/5.0' "
    "-TimeoutSec 25; $html = $r.Content; $plain = $html -replace '<script.*?>.*?</script>','' "
    "-replace '<style.*?>.*?</style>',''; Write-Output $plain.Substring(0,6000) } "
    "catch { Write-Output $_.Exception.Message }"
)


async def main() -> None:
    app_module.BackendClient = FakeClient
    app = app_module.LrmneAgentApp()

    async with app.run_test(size=(100, 46)) as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        app._dispatch_event(make_event(cid, "stream.hint", {
            "source": '{"label": "System", "sublabel": "Runtime State"}',
            "hint": "<system-reminder>Treat the following as the ground truth ...",
        }))
        app.send_chat("帮我看下 auth.py 的 token 相关代码")
        # 一次模型调用：文本 → 思考 → 工具调用；用量在调用收尾时才回来
        app._dispatch_event(make_event(cid, "model.start", {"reply_id": "r", "model_name": "claude-sonnet-4-5"}))
        app._dispatch_event(make_event(cid, "stream.text.start", {"reply_id": "r", "block_id": "t"}))
        app._dispatch_event(make_event(cid, "stream.text", {
            "reply_id": "r", "block_id": "t",
            "text_delta": "我先把 `read_token()` 的调用点找出来。\n\n",
        }))
        app._flush_streams()
        app._dispatch_event(make_event(cid, "stream.thinking.start", {"reply_id": "r", "block_id": "b"}))
        app._dispatch_event(make_event(cid, "stream.thinking", {
            "reply_id": "r", "block_id": "b",
            "text_delta": "The user is asking who I am. I should answer briefly and mention "
                          "that I work inside their project workspace. " * 4,
        }))
        # 思考耗时按 start→end 的间隔算，中间停一下才有数字可看
        await asyncio.sleep(0.9)
        app._dispatch_event(make_event(cid, "stream.thinking.end", {"reply_id": "r", "block_id": "b"}))

        # 编辑：一行摘要 + 默认折叠的 diff
        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t1", "name": "Edit"}))
        app._dispatch_event(make_event(cid, "tool.call.delta", {"tool_call_id": "t1", "delta": json.dumps(EDIT, ensure_ascii=False)}))
        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t1"}))
        app._dispatch_event(make_event(cid, "tool.result.delta", {"tool_call_id": "t1", "text_delta": "The file D:/code/demo/auth.py has been updated successfully."}))
        app._dispatch_event(make_event(cid, "tool.result.end", {"tool_call_id": "t1", "state": "success"}))

        # 终端命令：只给一行命令，无正文
        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t2", "name": "PowerShell"}))
        app._dispatch_event(make_event(cid, "tool.call.delta", {"tool_call_id": "t2", "delta": json.dumps({"command": LONG_COMMAND, "description": "抓取文章正文", "timeout": 25})}))
        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t2"}))
        app._dispatch_event(make_event(cid, "tool.result.delta", {"tool_call_id": "t2", "text_delta": "STATUS: 403 远程服务器返回错误: (403) 已禁止。"}))
        app._dispatch_event(make_event(cid, "tool.result.end", {"tool_call_id": "t2", "state": "error"}))

        # 长输出：结果折成一行标题
        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t3", "name": "Grep"}))
        app._dispatch_event(make_event(cid, "tool.call.delta", {"tool_call_id": "t3", "delta": json.dumps({"pattern": "[a-z]+", "glob": "*.py", "-C": 2}, ensure_ascii=False)}))
        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t3"}))
        app._dispatch_event(make_event(cid, "tool.result.delta", {
            "tool_call_id": "t3", "text_delta": "\n".join(f"auth.py:{i}:    token = read_token()" for i in range(1, 25)),
        }))
        app._dispatch_event(make_event(cid, "tool.result.end", {"tool_call_id": "t3", "state": "success"}))

        # 中断：状态行报「已中断」，agentscope 写进结果的提醒文本不进转录
        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t4", "name": "Glob"}))
        app._dispatch_event(make_event(cid, "tool.call.delta", {"tool_call_id": "t4", "delta": json.dumps({"pattern": "**/*.py"})}))
        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t4"}))
        app._dispatch_event(make_event(cid, "tool.result.delta", {
            "tool_call_id": "t4",
            "text_delta": "<system-reminder>The tool call has been interrupted by the user.</system-reminder>",
        }))
        app._dispatch_event(make_event(cid, "tool.result.end", {"tool_call_id": "t4", "state": "interrupted"}))

        # 调用收尾：助手块补上 token 数、思考块写耗时、状态栏算吞吐与命中率
        app._dispatch_event(make_event(cid, "stream.text.end", {"reply_id": "r", "block_id": "t"}))
        await asyncio.sleep(1.2)
        app._dispatch_event(make_event(cid, "model.end", {
            "reply_id": "r",
            "input_tokens": 12_400,
            "output_tokens": 96,
            "cache_input_tokens": 8_700,
            "context": {
                "total": 12_400,
                "segments": [
                    {"key": "prompt", "tokens": 2_600},
                    {"key": "skills", "tokens": 900},
                    {"key": "tools", "tokens": 1_500},
                    {"key": "messages", "tokens": 2_400},
                    {"key": "tool_calls", "tokens": 5_000},
                ],
            },
        }))

        # 等审批的那次调用：表要停住，状态写「等待中」而不是「执行中 · N 秒」
        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t5", "name": "PowerShell"}))
        app._dispatch_event(make_event(cid, "tool.call.delta", {"tool_call_id": "t5", "delta": json.dumps({"command": "curl -s https://example.com/feed", "timeout": 25})}))
        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t5"}))
        await pilot.pause()
        app._dispatch_event(make_event(cid, "approval.request", {
            "approval_request_id": "a2",
            "tool_calls": [{"id": "t5", "name": "PowerShell", "input": json.dumps({"command": "curl -s https://example.com/feed", "timeout": 25})}],
        }))
        await pilot.pause()

        # 右侧栏与底部状态栏都在下面的整屏渲染里，不再单独打印

        # 审批面板：摘要 + 折叠 diff + 扁平按钮
        app._dispatch_event(make_event(cid, "approval.request", {
            "approval_request_id": "a1",
            "tool_calls": [{"id": "c1", "name": "Write", "input": json.dumps({"file_path": "notes.md", "content": "# 观赛指南\n Worlds 2026 赛程\n"}, ensure_ascii=False)}],
        }))
        await pilot.pause()

        for strip in app.screen._compositor.render_strips():
            print("".join(segment.text for segment in strip).rstrip())


asyncio.run(main())
