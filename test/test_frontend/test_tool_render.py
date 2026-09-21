"""工具调用与审批的富渲染：参数 JSON → 摘要 / 彩色 diff。

分两层：``frontend.tools`` 的纯转换（不依赖 Textual），以及部件把它挂进聊天区
与审批面板的集成。协议里 ``ToolCallBlock.input`` 是 JSON **字符串**，两处都要能吃下。
"""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.widgets import Button, Collapsible

from conftest import emit, plain
from frontend import tools
from frontend.theme import S_DIFF_ADD, S_DIFF_DEL

EDIT_ARGS = {
    "file_path": "/p/a.py",
    "old_string": "old line",
    "new_string": "new line",
}


def styled_lines(out: Text) -> list[tuple[str, str]]:
    """(文本, 样式) 列表，按出现顺序；无样式的片段不产生 span。"""
    return [(out.plain[s.start : s.end], str(s.style)) for s in out.spans]


# ---------- 参数解析 ----------


def test_parse_args_returns_complete_json():
    assert tools.parse_args('{"file_path": "a.py"}') == {"file_path": "a.py"}


def test_parse_args_scavenges_only_closed_fields_while_streaming():
    raw = '{"file_path": "/p/a.py", "old_string": "还没闭合'
    assert tools.parse_args(raw) == {"file_path": "/p/a.py"}


def test_parse_args_unescapes_string_values():
    assert tools.parse_args('{"command": "echo a\\nb"}') == {"command": "echo a\nb"}


def test_parse_args_keeps_unknown_keys_of_complete_json_and_drops_non_objects():
    # 未知工具（如 MCP）的参数要原样保留，落到摘要的 compact JSON 兜底
    assert tools.parse_args('{"whatever": "x"}') == {"whatever": "x"}
    assert tools.parse_args('[1, 2]') == {}
    assert tools.parse_args("") == {}


def test_coerce_args_accepts_dict_json_string_or_junk():
    assert tools.coerce_args({"a": 1}) == {"a": 1}
    assert tools.coerce_args('{"file_path": "a.py"}') == {"file_path": "a.py"}
    assert tools.coerce_args(None) == {}
    assert tools.coerce_args(7) == {}


# ---------- 一行摘要 ----------


def test_summary_shows_only_the_command_line_for_shell():
    """终端工具只给一行命令：多行命令压平，超时等参数不再缀在行尾。"""
    args = {"command": "a\n  b", "timeout": 120, "description": "跑测试"}
    assert tools.summary("PowerShell", args).plain == "PowerShell  $ a b"


def test_summary_truncates_a_runaway_command():
    text = tools.summary("Bash", {"command": "echo " + "x" * 400})
    assert len(text.plain) < 200
    assert text.plain.endswith("…")


def test_summary_shows_edit_shape():
    text = tools.summary("Edit", EDIT_ARGS)
    assert text.plain == "Edit  /p/a.py · −1 +1 行"


def test_summary_marks_replace_all():
    args = dict(EDIT_ARGS, replace_all=True)
    assert "全部替换" in tools.summary("Edit", args).plain


def test_summary_gives_path_before_the_rest_arrives():
    raw = '{"file_path": "/p/a.py", "old_string": "未闭合'
    assert tools.summary("Edit", tools.parse_args(raw)).plain == "Edit  /p/a.py"


def test_summary_reports_grep_flags():
    text = tools.summary("Grep", {"pattern": "TODO", "glob": "*.py", "i": True, "-C": 2})
    assert text.plain == "Grep  /TODO/ · *.py · -i · -C2"


def test_summary_falls_back_to_compact_json():
    assert tools.summary("mcp__x__y", {"a": 1}).plain == 'mcp__x__y  {"a": 1}'


def test_summary_keeps_tool_name_when_args_are_missing():
    assert tools.summary("Read", {}).plain == "Read"


def test_icon_of_marks_file_and_shell_tools_apart():
    """标题前的图标按类别分：文件类是文件，终端类是指针，MCP 之类走中性点。"""
    assert tools.icon_of("Read") == "▤"
    assert tools.icon_of("Glob") == "▤"
    assert tools.icon_of("PowerShell") == "▸"
    assert tools.icon_of("mcp__x__y") == "●"


# ---------- 正文 ----------


def test_edit_body_renders_colored_diff():
    body = tools.body("Edit", EDIT_ARGS)
    assert body is not None
    assert body.plain.splitlines() == ["@@ /p/a.py", "- old line", "+ new line"]
    assert ("- old line", str(S_DIFF_DEL)) in styled_lines(body)
    assert ("+ new line", str(S_DIFF_ADD)) in styled_lines(body)


def test_edit_body_clips_long_side_and_counts_omitted_lines():
    args = {
        "file_path": "a.py",
        "old_string": "\n".join(f"o{i}" for i in range(30)),
        "new_string": "n",
    }
    body = tools.body("Edit", args)
    assert body is not None
    lines = body.plain.splitlines()
    assert "省略 18 行" in body.plain
    # 头部 + 首尾各 6 行 + 省略标记 + 新增行，总行数不随原行数增长
    assert len(lines) == tools.MAX_DIFF_LINES + 3


def test_write_body_marks_every_line_as_addition():
    body = tools.body("Write", {"file_path": "a.py", "content": "x\ny\n"})
    assert body is not None
    assert body.plain.startswith("@@ a.py  ·  2 行 / 4 字节\n+ x\n+ y")


def test_shell_never_has_a_body_block():
    """多行命令也只在摘要里给一行，不铺正文。"""
    assert tools.body("Bash", {"command": "ls"}) is None
    assert tools.body("PowerShell", {"command": "set -e\nls\nrm -rf x"}) is None


def test_body_label_sizes_the_fold_while_it_is_collapsed():
    assert tools.body_label("Edit", EDIT_ARGS) == "改动 1 → 1 行"
    assert tools.body_label("Write", {"content": "x\ny\n"}) == "写入 2 行"


def test_body_is_none_when_there_is_nothing_to_show():
    assert tools.body("Read", {"file_path": "a.py"}) is None
    assert tools.body("Edit", {"file_path": "a.py"}) is None
    assert tools.body("Write", {"file_path": "a.py", "content": ""}) is None


def test_result_label_sizes_the_fold_and_previews_it():
    """结果折叠后标题是唯一可见的一行：既报行数，也给一段开头。"""
    label = tools.result_label("第一行\n第二行\n第三行")
    assert label.startswith("输出 3 行 · 第一行")
    # 单行长输出没有「行数」可言，也不该把整段铺进标题
    single = tools.result_label("x" * 400)
    assert single.startswith("输出 · ")
    assert len(single) <= tools.RESULT_PREVIEW + len("输出 · ")
    assert tools.result_label("") == "输出"


# ---------- 聊天区集成 ----------


@pytest.mark.anyio
async def test_tool_call_view_shows_summary_then_body(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        emit(app, cid, "tool.call.start", {"tool_call_id": "t1", "name": "Edit"})
        emit(
            app,
            cid,
            "tool.call.delta",
            {"tool_call_id": "t1", "delta": '{"file_path": "/p/a.py", '},
        )
        await pilot.pause()

        view = conv.tools["t1"]
        # 标题只写工具名（文件类带文件图标）：边框标题挤不下一条命令，
        # 「在干什么」走框内第一行
        assert str(view.border_title) == "▤ Edit"
        assert plain(view.query_one(".tool-detail")) == "/p/a.py"
        assert "准备中" in str(view.border_subtitle)
        assert not view.query(".diff-fold")

        emit(
            app,
            cid,
            "tool.call.delta",
            {
                "tool_call_id": "t1",
                "delta": '"old_string": "old line", "new_string": "new line"}',
            },
        )
        emit(app, cid, "tool.call.end", {"tool_call_id": "t1"})
        await pilot.pause()

        assert view._state == "executing"
        assert plain(view.query_one(".tool-detail")) == "/p/a.py · −1 +1 行"
        fold = view.query_one(".diff-fold", Collapsible)
        assert fold.collapsed is True  # 默认收起，聊天区一眼能扫过
        assert plain(fold.query_one(".diff-body")).splitlines() == [
            "@@ /p/a.py",
            "- old line",
            "+ new line",
        ]

        emit(app, cid, "tool.result.end", {"tool_call_id": "t1", "state": "success"})
        await pilot.pause()

        assert view._state == "done"
        assert "完成" in str(view.border_subtitle)


@pytest.mark.anyio
async def test_short_result_lands_in_the_result_row_even_before_mount(make_app):
    """结果可能赶在部件挂载前就到齐（挂载是延迟落地的），那一行不能被丢掉。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        # 整段一次灌完，中间不给 pilot.pause()：复现「结果先于挂载到达」
        for name, data in (
            ("tool.call.start", {"tool_call_id": "t3", "name": "PowerShell"}),
            ("tool.call.delta", {"tool_call_id": "t3", "delta": '{"command": "ls"}'}),
            ("tool.call.end", {"tool_call_id": "t3"}),
            ("tool.result.delta", {"tool_call_id": "t3", "text_delta": "STATUS: 403"}),
            ("tool.result.end", {"tool_call_id": "t3", "state": "error"}),
        ):
            emit(app, cid, name, data)
        await pilot.pause()

        view = conv.tools["t3"]
        assert plain(view.query_one(".tool-result")).startswith("└ STATUS: 403")
        assert not view.query(".result-fold")  # 短结果不值得折


@pytest.mark.anyio
async def test_interrupted_call_reports_its_own_state_without_the_runtime_reminder(make_app):
    """被中断的调用报「已中断」，而 agentscope 写进结果的提醒文本不进转录。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        emit(app, cid, "tool.call.start", {"tool_call_id": "t4", "name": "Glob"})
        emit(app, cid, "tool.call.delta", {"tool_call_id": "t4", "delta": '{"pattern": "**/*.py"}'})
        emit(app, cid, "tool.call.end", {"tool_call_id": "t4"})
        emit(
            app,
            cid,
            "tool.result.delta",
            {
                "tool_call_id": "t4",
                "text_delta": (
                    "<system-reminder>The tool call has been interrupted by the user."
                    "</system-reminder>"
                ),
            },
        )
        emit(app, cid, "tool.result.end", {"tool_call_id": "t4", "state": "interrupted"})
        await pilot.pause()

        view = conv.tools["t4"]
        assert view._state == "interrupted"
        assert "已中断" in str(view.border_subtitle)
        assert plain(view.query_one(".tool-result")) == ""


@pytest.mark.anyio
async def test_tool_call_view_keeps_unknown_tool_readable(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        emit(app, cid, "tool.call.start", {"tool_call_id": "t2", "name": "mcp__x__y"})
        emit(
            app,
            cid,
            "tool.call.delta",
            {"tool_call_id": "t2", "delta": '{"q": "hi"}'},
        )
        await pilot.pause()

        assert "mcp__x__y" in str(conv.tools["t2"].border_title)
        assert not conv.tools["t2"].query(".diff-fold")


# ---------- 审批面板 ----------


def _approval(app, cid, tool_calls):
    emit(
        app,
        cid,
        "approval.request",
        {"approval_request_id": "a1", "tool_calls": tool_calls},
    )


@pytest.mark.anyio
async def test_pending_approval_pauses_the_tool_call_timer(make_app):
    """审批没表态前工具其实没在跑：块上写「等待中」，等待那段不算工具耗时。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        emit(app, cid, "tool.call.start", {"tool_call_id": "t5", "name": "PowerShell"})
        emit(
            app,
            cid,
            "tool.call.delta",
            {"tool_call_id": "t5", "delta": '{"command": "ls"}'},
        )
        emit(app, cid, "tool.call.end", {"tool_call_id": "t5"})
        await pilot.pause()

        view = conv.tools["t5"]
        assert "执行中" in str(view.border_subtitle)

        _approval(
            app, cid, [{"id": "t5", "name": "PowerShell", "input": {"command": "ls"}}]
        )
        await pilot.pause()

        assert view._state == "awaiting"
        assert "等待中" in str(view.border_subtitle)
        assert view._timer is None  # 表停了，等的这段时间不该往下走

        conv.approvals["a1"].action_approve()
        # Decision 走 post_message，要两拍才落到 app 的 handler 上
        await pilot.pause()
        await pilot.pause()

        assert view._state == "executing"

        emit(app, cid, "tool.result.end", {"tool_call_id": "t5", "state": "success"})
        await pilot.pause()

        assert view._state == "done"


@pytest.mark.anyio
async def test_approval_panel_shows_the_diff_it_is_asking_about(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        # 协议里 input 是 JSON 字符串，面板要自己解析
        _approval(app, conv.cid, [{"name": "Edit", "input": '{"file_path": "/p/a.py", "old_string": "old line", "new_string": "new line"}'}])
        await pilot.pause()

        panel = conv.approvals["a1"]
        first = panel.query("Static").first()
        assert plain(first) == "Edit  /p/a.py · −1 +1 行"
        fold = panel.query_one(".diff-fold", Collapsible)
        assert fold.collapsed is True
        assert "- old line" in plain(fold.query_one(".diff-body"))


@pytest.mark.anyio
async def test_approval_buttons_are_flat_text_not_colored_boxes(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        _approval(app, conv.cid, [{"name": "Bash", "input": {"command": "ls"}}])
        await pilot.pause()

        panel = conv.approvals["a1"]
        for button_id, label in (
            ("btn-approve", "允许"),
            ("btn-deny", "拒绝"),
            ("btn-remember", "允许并记住"),
        ):
            button = panel.query_one(f"#{button_id}", Button)
            # variant 会带出绿/红实心方块，与整体石墨观感冲突
            assert "-success" not in button.classes
            assert "-error" not in button.classes
            assert str(button.label) == label
        # 三个按钮就是全部选项，边框上不再写一遍快捷键
        assert panel.border_subtitle is None


@pytest.mark.anyio
async def test_approval_panel_does_not_duplicate_short_commands(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        _approval(app, conv.cid, [{"name": "Bash", "input": {"command": "ls -la"}}])
        await pilot.pause()

        panel = conv.approvals["a1"]
        assert plain(panel.query("Static").first()) == "Bash  $ ls -la"
        assert not panel.query(".diff-fold")


@pytest.mark.anyio
async def test_approval_always_remembers_the_class_without_touching_mode(make_app):
    """「允许并记住」是一条命令：放行这一类由后端回灌规则，不再改全局权限模式。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        _approval(app, conv.cid, [{"name": "Write", "input": {"file_path": "a.py", "content": "x"}}])
        await pilot.pause()

        panel = conv.approvals["a1"]
        panel.action_approve_and_remember()
        await pilot.pause()
        await pilot.pause()

        assert panel.resolved
        assert conv.approvals == {}
        assert app.client.last("config.set") is None
        assert app.client.last("approval.respond").parameters == {
            "approval_request_id": "a1",
            "approved": True,
            "always": True,
        }
        # 表态由面板自己标「已处理」，不再往转录里补一行回执
        assert not any("已允许该工具调用" in plain(child) for child in conv.view.children)


@pytest.mark.anyio
async def test_plain_approve_does_not_touch_permission_mode(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        _approval(app, conv.cid, [{"name": "Bash", "input": {"command": "ls"}}])
        await pilot.pause()

        conv.approvals["a1"].action_approve()
        await pilot.pause()

        assert app.client.last("config.set") is None
