"""主应用壳的无头冒烟测试：不拉起后端子进程，只喂协议消息。

覆盖的是「组装 + 路由 + 状态」这三类容易悄悄坏掉的地方：
事件按 cid 分派、流式文本合并写入、回执走 registry、审批与在途标记的收敛。
"""

from __future__ import annotations

import asyncio

import pytest

from proxy_layer.schema import (
    ErrorCode,
    make_event,
    make_receipt_error,
    make_receipt_success,
)

from frontend.conversation import Conversation


def _plain(widget) -> str:
    renderable = widget.render()
    return getattr(renderable, "plain", str(renderable))


def _side(app) -> str:
    """右侧栏三个小框的正文合在一起，方便按内容断言。"""
    return "\n".join(
        _plain(box)
        for box in (app.side._context_box, app.side._metric_box, app.side._config_box)
    )


def _styles(strip) -> dict[str, str]:
    """渲染出来的一行 → ``{文本: 样式}``（只关心有字的段，用来核对着色）。"""
    return {
        segment.text: str(segment.style) for segment in strip if segment.text.strip()
    }


@pytest.mark.anyio
async def test_mount_opens_one_conversation_and_pulls_config(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.store.current_cid
        assert [cmd.operation for cmd in app.client.sent] == ["config.get"]
        # 启动即有一条欢迎卡片，输入框已聚焦
        assert app.input_area.has_focus


@pytest.mark.anyio
async def test_stream_text_is_coalesced_into_a_single_block(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        before = len(conv.view.children)

        app._dispatch_event(make_event(cid, "stream.text.start", {"reply_id": "r1", "block_id": "b1"}))
        await pilot.pause()
        assert len(conv.view.children) == before + 1

        for piece in ("你", "好", "，世界"):
            app._dispatch_event(
                make_event(cid, "stream.text", {"reply_id": "r1", "block_id": "b1", "text_delta": piece})
            )
        await pilot.pause()

        # 三个 delta 仍只对应一个部件
        assert len(conv.view.children) == before + 1

        # 走与 0.08s ticker 相同的合并写入路径
        app._flush_streams()
        assert "你好，世界" in conv.text_blocks["r1:b1"].markdown.source

        app._dispatch_event(make_event(cid, "stream.text.end", {"reply_id": "r1", "block_id": "b1"}))
        await pilot.pause()
        assert "r1:b1" not in conv.text_blocks


@pytest.mark.anyio
async def test_thinking_finished_before_mount_keeps_its_finished_state(make_app):
    """思考可能整段在部件挂载前流完（挂载是延迟落地的），动画不该把它拽回「思考中」。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid

        for name, data in (
            ("stream.thinking.start", {"reply_id": "r", "block_id": "b"}),
            ("stream.thinking", {"reply_id": "r", "block_id": "b", "text_delta": "先看调用点"}),
            ("stream.thinking.end", {"reply_id": "r", "block_id": "b"}),
        ):
            app._dispatch_event(make_event(cid, name, data))
        # 多等一拍：若动画仍在跑，这一拍就会把状态覆盖回「思考中」
        await pilot.pause(0.3)

        block = conv.thinking["r:b"]
        assert block.collapsed is True
        assert "已思考" in str(block.border_subtitle)


@pytest.mark.anyio
async def test_model_usage_lands_on_the_block_and_the_status_bar(make_app):
    """用量只在 model.end 按整次调用回来：分摊给 thinking 块与正文块，状态栏现算比值。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid

        for name, data in (
            ("model.start", {"reply_id": "r", "model_name": "m"}),
            ("stream.thinking.start", {"reply_id": "r", "block_id": "t"}),
            (
                "stream.thinking",
                {"reply_id": "r", "block_id": "t", "text_delta": "先看调用点"},
            ),
            ("stream.thinking.end", {"reply_id": "r", "block_id": "t"}),
            ("stream.text.start", {"reply_id": "r", "block_id": "b"}),
            ("stream.text", {"reply_id": "r", "block_id": "b", "text_delta": "好了"}),
            ("stream.text.end", {"reply_id": "r", "block_id": "b"}),
        ):
            app._dispatch_event(make_event(cid, name, data))
        await pilot.pause()

        # 思考块的右下角写耗时，不再复述正文开头
        thinking = str(conv.thinking["r:t"].border_subtitle)
        assert thinking.startswith("已思考 · ")
        assert "先看调用点" not in thinking

        # 正文先收尾，用量还没回来，先只写「已生成」
        assistant = conv.call_outputs[-1]
        assert str(assistant.border_subtitle) == "已生成"

        app._dispatch_event(
            make_event(
                cid,
                "model.end",
                {
                    "reply_id": "r",
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_input_tokens": 500,
                },
            )
        )
        await pilot.pause()

        # 思考也是模型产出：这 200 tok 按字数分给 thinking（5 字）与正文（2 字），
        # 两份加起来仍等于上报值，谁也没被多算或漏算
        assert str(assistant.border_subtitle) == "已生成 · 57 tok"
        note = str(conv.thinking["r:t"].border_subtitle)
        assert note.startswith("已思考 · ")
        assert note.endswith("· 143 tok")
        # 底部那一行只留三样：运行态、模型、ctx 表；量化信息全在右侧栏
        assert [text for text, _ in app.status._segments()] == ["m"]
        side = _side(app)
        assert "缓存命中" in side and "50%" in side
        assert "tok/s" in side

        # 命中率取最近一次调用：累计值会把每轮重复发送的前缀摊进分母，越跑越低
        app._dispatch_event(
            make_event(
                cid,
                "model.end",
                {
                    "reply_id": "r",
                    "input_tokens": 2000,
                    "output_tokens": 100,
                    "cache_input_tokens": 1800,
                    "context": {
                        "total": 2000,
                        "segments": [
                            {"key": "prompt", "tokens": 600},
                            {"key": "tool_calls", "tokens": 1400},
                        ],
                    },
                },
            )
        )
        await pilot.pause()

        side = _side(app)
        assert "缓存命中 90%" in " ".join(side.split())
        # 上下文构成常驻右侧栏：压力表只给比值，看不出是谁把上下文撑起来的
        assert "提示词" in side and "工具调用" in side

        # 一轮收尾后归因目标作废，下一次调用的数字不会补到这一块上
        app._dispatch_event(
            make_event(cid, "run.finished", {"request_id": "q", "stop_reason": "completed"})
        )
        await pilot.pause()
        assert conv.call_outputs == []


@pytest.mark.anyio
async def test_side_panel_scrolls_config_and_keeps_brand_at_bottom(make_app):
    """配置框与上下文 / 用量同处滚动区，品牌区整块钉在面板底部。

    回归的是「一跑起来配置整段消失」：那时三类信息连成一串写在同一个部件里，
    超出面板高度的尾巴被直接裁掉。现在配置进了滚动容器（可滚），底部品牌区
    不随上面的框滚动。
    """
    app = make_app()

    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()
        conv = app.store.current

        request_id = app.send("config.get", {})
        app._dispatch_receipt(make_receipt_success(request_id, conv.cid, CONFIG_VIEW))
        app._dispatch_event(
            make_event(
                conv.cid,
                "model.end",
                {
                    "reply_id": "r",
                    "input_tokens": 120000,
                    "output_tokens": 800,
                    "cache_input_tokens": 60000,
                    "context": {
                        "total": 120000,
                        "segments": [
                            {"key": "prompt", "tokens": 3000},
                            {"key": "skills", "tokens": 2500},
                            {"key": "tools", "tokens": 9000},
                            {"key": "messages", "tokens": 40000},
                            {"key": "tool_calls", "tokens": 60000},
                            {"key": "summary", "tokens": 5500},
                        ],
                    },
                },
            )
        )
        await pilot.pause()
        await pilot.pause()

        scroll = app.side.query_one("#side-scroll")
        # 配置框已经移进滚动容器，与上下文 / 用量一起滚
        assert app.side._config_box in scroll.children
        # 装不下的那部分交给滚动容器，而不是被裁掉就当没有
        assert scroll.max_scroll_y > 0
        assert "会话" in _side(app)

        # 品牌区钉在面板底部，整块留在可视区
        panel = app.side.region
        brand = app.side.query_one("#side-brand")
        assert brand.region.height > 0
        assert brand.region.y + brand.region.height <= panel.y + panel.height
        assert "LrmneAgent" in _plain(brand).replace(" ", "")  # 产品名字标（字母留白）


@pytest.mark.anyio
async def test_context_compression_leaves_a_notice(make_app):
    """压缩由引擎在推理前自行触发、本身不产事件：后端补的那条要在转录里看得见。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        app._dispatch_event(
            make_event(conv.cid, "context.compressed", {"before": 48, "after": 6})
        )
        await pilot.pause()

        text = "\n".join(_plain(child) for child in conv.view.children)
        assert "已压缩" in text
        assert "48" in text and "6" in text


def test_deltas_are_batched_until_flush():
    """合并写入的契约：写入一次后不再重复写，直到有新 delta。"""
    conv = Conversation("c-unit")
    conv.begin_text("r:1")
    conv.push_text("r:1", "a")
    conv.push_text("r:1", "b")

    assert conv.flush_text("r:1") is True
    assert conv.flush_text("r:1") is False

    conv.push_text("r:1", "c")
    assert conv.flush_dirty() is True
    assert conv.flush_dirty() is False

    conv.end_text("r:1")
    assert "r:1" not in conv.text_blocks


@pytest.mark.anyio
async def test_events_route_by_conversation_id(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.store.current_cid
        second = "c-other"
        app.store.create(second)

        app._dispatch_event(make_event(first, "stream.text.start", {"reply_id": "r", "block_id": "1"}))
        app._dispatch_event(make_event(second, "stream.text.start", {"reply_id": "r", "block_id": "2"}))
        await pilot.pause()

        assert set(app.store.get(first).text_blocks) == {"r:1"}
        assert set(app.store.get(second).text_blocks) == {"r:2"}
        # 未切换前台：当前会话不变
        assert app.store.current_cid == first


@pytest.mark.anyio
async def test_tool_call_lifecycle_renders_status_and_result(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid

        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t1", "name": "read_file"}))
        app._dispatch_event(make_event(cid, "tool.call.delta", {"tool_call_id": "t1", "delta": '{"path": "a.py"}'}))
        await pilot.pause()

        view = conv.tools["t1"]
        assert view._state == "running"
        assert "read_file" in str(view.border_title)

        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t1"}))
        app._dispatch_event(make_event(cid, "tool.result.delta", {"tool_call_id": "t1", "text_delta": "done"}))
        app._dispatch_event(make_event(cid, "tool.result.end", {"tool_call_id": "t1", "state": "success"}))
        await pilot.pause()

        assert view._state == "done"
        assert any("done" in _plain(child) for child in view.children)


@pytest.mark.anyio
async def test_chat_view_follows_the_bottom_until_the_user_scrolls_away(make_app):
    """内容往下长时视口要跟着走；用户往上翻了就不该再打扰他。

    锁的是「滚动延到刷新之后」这一步：挂载是延迟落地的，当场量到的是旧高度，
    当场滚就永远停在上一段的末尾。
    """
    app = make_app()

    async with app.run_test(size=(80, 16)) as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        for index in range(20):
            app._dispatch_event(
                make_event(cid, "stream.text.start", {"reply_id": "r", "block_id": f"b{index}"})
            )
            app._dispatch_event(
                make_event(
                    cid,
                    "stream.text",
                    {"reply_id": "r", "block_id": f"b{index}", "text_delta": f"第 {index} 段"},
                )
            )
            app._dispatch_event(
                make_event(cid, "stream.text.end", {"reply_id": "r", "block_id": f"b{index}"})
            )
            await pilot.pause()

        view = conv.view

        async def settle() -> None:
            """贴底收敛有两级：刷新链 + 0.2s 的兜底 tick。

            ``pilot.pause()`` 不推进真实时间，兜底那一 tick 根本不会跑；这里带延时
            多等几轮，等的是"高度还在变"这件事结束，不是等一个固定秒数。
            """
            for _ in range(3):
                await pilot.pause(0.3)

        await settle()
        assert view.max_scroll_y > 0  # 内容确实高过一屏
        assert view.scroll_offset.y >= view.max_scroll_y - 2

        view.scroll_home(animate=False)
        await settle()
        app._dispatch_event(
            make_event(cid, "stream.text.start", {"reply_id": "r", "block_id": "b99"})
        )
        await settle()

        assert view.scroll_offset.y <= 2  # 停在用户看的地方


@pytest.mark.anyio
async def test_run_finished_clears_inflight_and_pending(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        app.send_chat("你好")
        await pilot.pause()

        request_id = app.client.sent[-1].request_id
        assert conv.inflight == {request_id}
        assert conv.run_state == "working"
        assert app.status._state == "working"

        app._dispatch_event(make_event(conv.cid, "run.finished", {"request_id": request_id, "stop_reason": "completed"}))
        await pilot.pause()

        assert conv.inflight == set()
        assert request_id not in conv.pending
        assert conv.run_state == "idle"
        assert app.status._state == "idle"


@pytest.mark.anyio
async def test_receipt_renders_result_and_updates_status(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        request_id = app.send("config.get", {})
        app._dispatch_receipt(
            make_receipt_success(
                request_id,
                conv.cid,
                {"model": {"model": "gpt-4"}, "thinking_level": "high", "mode": "default"},
            )
        )
        await pilot.pause()

        # 会话未调用模型时，状态栏回落到全局配置里的模型；档位归右侧栏
        assert app.status._config_model == "gpt-4"
        assert app.side._thinking == "high"
        assert app.side._permission == "default"
        assert request_id not in conv.pending


CONFIG_VIEW = {
    "model": {
        "configured": True,
        "provider_type": "openai_credential",
        "credential": {"api_key": {"configured": True, "suffix": "9f3c"}},
        "model": "gpt-4",
        "thinking_level": "high",
        "stream": True,
        "max_retries": 3,
        "context_size": 200000,
    },
    "permission": {"mode": "default"},
    "workspace": {"root": "D:/code/LrmneAgent", "agent_home": "D:/code/LrmneAgent/.lrmne"},
}


@pytest.mark.anyio
async def test_startup_config_read_stays_out_of_transcript(make_app):
    """启动时的 config.get 只喂状态栏，不能把 provider_type / agent_home 倒进转录。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        request_id = app.send("config.get", {})
        app._dispatch_receipt(make_receipt_success(request_id, conv.cid, CONFIG_VIEW))
        await pilot.pause()

        assert not list(conv.view.children)
        assert app.side._thinking == "high"
        assert app.status._context_size == 200000


@pytest.mark.anyio
async def test_failed_receipt_notices_and_drops_inflight(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current

        request_id = app.send("chat.send", {"text": "hi"})
        assert request_id in conv.inflight

        app._dispatch_receipt(
            make_receipt_error(
                request_id, conv.cid, ErrorCode.INVALID_PARAMETERS, "缺少 text"
            )
        )
        await pilot.pause()

        assert request_id not in conv.inflight
        assert request_id not in conv.pending
        assert conv.run_state == "idle"


@pytest.mark.anyio
async def test_approval_request_blocks_then_responds(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        app._dispatch_event(
            make_event(
                conv.cid,
                "approval.request",
                {
                    "approval_request_id": "a1",
                    "tool_calls": [{"name": "write_file", "input": {"path": "a.py"}}],
                },
            )
        )
        await pilot.pause()

        panel = conv.approvals["a1"]
        assert app.status._approvals == 1
        assert "write_file" in _plain(panel.query("Static").first())

        app._resolve_approval(panel, True)
        await pilot.pause()

        assert panel.resolved
        assert conv.approvals == {}
        assert app.status._approvals == 0
        assert app.client.sent[-1].operation == "approval.respond"
        assert app.client.sent[-1].parameters == {
            "approval_request_id": "a1",
            "approved": True,
            "always": False,
        }


@pytest.mark.anyio
async def test_switching_conversation_keeps_drafts_and_status_separate(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.store.current_cid
        app.input_area.set_text("草稿 A")
        await pilot.pause()

        app.new_conversation()
        await pilot.pause()
        second = app.store.current_cid
        assert second != first
        assert app.input_area.text == ""
        assert app.store.get(first).view.display is False

        app.input_area.set_text("草稿 B")
        app.activate(first)
        await pilot.pause()

        assert app.input_area.text == "草稿 A"
        assert app.store.get(first).view.display is True
        assert app.store.get(second).view.display is False


@pytest.mark.anyio
async def test_at_opens_the_mention_list_and_enter_inserts_the_pick(make_app, tmp_path):
    """``@`` 先列当前这一层；打了字就在这一层之下搜关键字，Enter 换成 ``@标签``。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "session.py").write_text("token = 1", encoding="utf-8")
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()  # 挂载时的配置回执会留张卡，这里只关心 @ 有没有写进转录
        app.remember_config({"root": str(tmp_path)})

        await pilot.press("@")
        await pilot.pause()
        assert app.palette.display and app.palette.mode == "mention"
        assert app.client.last("skill.list") is not None  # 顺手拉一次 skill 列表
        # 空 @ 看目录结构：目录在前，文件在后
        assert app.palette.current_mention.label == "src"

        await pilot.press("s", "e", "s", "s")
        await pilot.pause()
        assert app.palette.current_mention.label == "src/session.py"

        await pilot.press("enter")
        await pilot.pause()
        assert not app.palette.display
        assert app.input_area.text == "@src/session.py "
        # 只是插入正文，还没发出去
        assert app.client.last("chat.send") is None
        assert not list(conv.view.children)


@pytest.mark.anyio
async def test_at_a_directory_drills_down_instead_of_inserting(make_app, tmp_path):
    """目录上按 Enter 是往下钻一层：补成 ``@src/`` 不加空格，面板接着列下一层。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "session.py").write_text("token = 1", encoding="utf-8")
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.remember_config({"root": str(tmp_path)})

        await pilot.press("@")
        await pilot.pause()
        assert app.palette.current_mention.label == "src"

        await pilot.press("enter")
        await pilot.pause()
        assert app.input_area.text == "@src/"
        assert app.palette.display  # 没插完，还在往下钻
        assert [item.label for item in app.palette._shown] == ["src/session.py"]
        assert app.palette.current_mention.label == "src/session.py"
        # 光标在 @src/ 后面：接着打字就是在这一层里搜
        assert app.input_area.cursor_location == (0, 5)

        await pilot.press("enter")
        await pilot.pause()
        assert app.input_area.text == "@src/session.py "
        assert not app.palette.display


@pytest.mark.anyio
async def test_mention_file_stays_a_line_in_the_prompt(make_app, tmp_path):
    """``@路径`` 只是提示词里的一行字：不读文件、不带附件，模型自己去看。"""
    (tmp_path / "note.md").write_text("你好", encoding="utf-8")
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.remember_config({"root": str(tmp_path)})

        app.input_area.set_text("看下 @note.md 然后改掉")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        command = app.client.last("chat.send")
        assert command.parameters["text"] == "看下 @note.md 然后改掉"
        assert "attachments" not in command.parameters


@pytest.mark.anyio
async def test_reference_in_the_input_box_is_painted_as_a_chip(make_app):
    """正文里的 ``@引用`` 单独套一层底色：光标落在引用里时也要让出那一格。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.input_area
        line = "看下 @src/app.py 谢谢 a@b.com"
        area.set_text(line)
        await pilot.pause()

        styles = _styles(area.render_line(0))
        assert styles["@src/app.py"] != styles["看下 "]  # 引用被单独着色
        assert "bold" in styles["@src/app.py"]
        # 邮箱那种贴在字符上的 @ 不算引用：整段还是普通样式，切不出独立的段
        assert styles[" 谢谢 a@b.com"] == styles["看下 "]

        # 光标停在引用里面（第 7 列）：那一格让出来，否则光标被底色盖住
        area.move_cursor((0, 7))
        await pilot.pause()
        assert list(_styles(area.render_line(0))) == [
            "看下 ",
            "@src",
            "/",
            "app.py",
            " 谢谢 a@b.com ",
        ]


@pytest.mark.anyio
async def test_at_skills_fill_the_list_without_touching_the_transcript(make_app, tmp_path):
    """skill 列表只为填候选：回执不该在转录里多出一张卡片。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()
        app.remember_config({"root": str(tmp_path)})

        await pilot.press("@")
        await pilot.pause()
        app._handle_receipt(
            make_receipt_success(
                app.client.last("skill.list").request_id,
                conv.cid,
                [{"name": "review", "description": "审查改动"}],
            )
        )
        await pilot.pause()

        assert not list(conv.view.children)
        assert app.palette.current_mention.label == "review"
        assert "skill" in app.palette.current_mention.note


@pytest.mark.anyio
async def test_at_without_a_match_says_so_instead_of_hiding(make_app, tmp_path):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.remember_config({"root": str(tmp_path)})

        await pilot.press("@", "z", "z", "z")
        await pilot.pause()

        assert app.palette.display
        assert app.palette.current_mention is None
        assert "没有匹配" in str(app.palette.get_option_at_index(0).prompt.plain)


@pytest.mark.anyio
async def test_palette_enter_and_click_both_run_the_highlighted_command(make_app):
    """命令面板不需要先 Tab 补全：Enter 直接执行，鼠标点选走同一条路。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current

        await pilot.press("/")
        await pilot.pause()
        assert app.palette.display
        assert app.palette.current_command.name == "help"

        await pilot.press("enter")
        await pilot.pause()
        assert not app.palette.display
        assert list(conv.view.query("Card"))  # /help 的卡片出来了

        await pilot.press("/", "m", "o")
        await pilot.pause()
        assert app.palette.current_command.name == "model"

        # 点选与 Enter 都落到 OptionList.action_select，同一条派发路径
        app.palette.action_select()
        await pilot.pause()
        assert app.model_config_overlay is not None


@pytest.mark.anyio
async def test_escape_pauses_inflight_and_stays_quiet_when_idle(make_app):
    """Esc 就是暂停：在途时给一句「已暂停」，没有在途的事就什么都不说。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        await pilot.press("escape")
        await pilot.pause()
        assert app.client.last("chat.interrupt") is None
        assert not list(conv.view.children)

        app.send_chat("你好")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.client.last("chat.interrupt") is not None
        assert any("已暂停" in _plain(child) for child in conv.view.children)


@pytest.mark.anyio
async def test_escape_interrupts_once_per_run(make_app):
    """一次运行只下发一次中断：再取消一次会掐掉后端收尾，界面反而卡在「执行中」。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        app.send_chat("你好")
        await pilot.pause()
        await pilot.press("escape", "escape", "escape")
        await pilot.pause()

        assert app.client.operations.count("chat.interrupt") == 1
        assert sum("已暂停" in _plain(child) for child in conv.view.children) == 1

        # 这一轮结束后 Esc 重新可用
        request_id = next(iter(conv.inflight))
        app._dispatch_event(
            make_event(conv.cid, "run.finished", {"request_id": request_id, "stop_reason": "interrupted"})
        )
        await pilot.pause()
        assert conv.stopping is False

        app.send_chat("再来一次")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.client.operations.count("chat.interrupt") == 2


@pytest.mark.anyio
async def test_run_end_settles_blocks_left_running(make_app):
    """结果帧没能送达时，run 结束也得把块从「执行中」收下来。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        cid = conv.cid
        conv.view.remove_children()

        app._dispatch_event(make_event(cid, "tool.call.start", {"tool_call_id": "t9", "name": "Glob"}))
        app._dispatch_event(make_event(cid, "tool.call.end", {"tool_call_id": "t9"}))
        await pilot.pause()

        view = conv.tools["t9"]
        assert "执行中" in str(view.border_subtitle)

        app._dispatch_event(
            make_event(cid, "run.finished", {"request_id": "r1", "stop_reason": "interrupted"})
        )
        await pilot.pause()

        assert "已中断" in str(view.border_subtitle)
        assert conv.tools == {}


def _metric_rows(app) -> list[str]:
    """右侧栏「用量」框当前的行文本。"""
    return [row.plain for row in app.side._metric_section()[1]]


@pytest.mark.anyio
async def test_side_panel_keeps_usage_visible_while_a_call_is_inflight(make_app):
    """思考进行中用量框不能消失。

    provider 只在 model.end 报一次量，而发送时 reset_tokens 会把本次运行的计数归零——
    于是整个调用期间一行都凑不出来，框被 `_fill` 收起，用户看到的是"用量监控没了"。
    """
    app = make_app()

    def emit(name, data=None):
        app._dispatch_event(make_event(app.store.current.cid, name, data or {}))

    async def tick(pilot):
        """跨过 STREAM_FLUSH_INTERVAL，让那个合并节拍真的跑一次。"""
        await asyncio.sleep(0.12)
        await pilot.pause()

    async with app.run_test() as pilot:
        await pilot.pause()

        # 第一次调用：没有校准基准，只报字数，不假装知道 token
        app.send_chat("你好")
        await pilot.pause()
        emit("model.start", {"model_name": "m"})
        await pilot.pause()  # 让 model.start 真的落地，否则下面改的起始时刻会被覆盖
        emit("stream.thinking.start", {"reply_id": "r1", "block_id": "t1"})
        emit("stream.thinking", {"reply_id": "r1", "block_id": "t1", "text_delta": "先想一段够用的内容。"})
        await tick(pilot)

        assert app.side._metric_box.display is True
        assert any("输出" in r and "字" in r for r in _metric_rows(app)), _metric_rows(app)
        assert not any("≈" in r for r in _metric_rows(app))

        # 真值到了，估算退场
        emit("model.end", {"input_tokens": 3000, "output_tokens": 12})
        await pilot.pause()
        assert not any("≈" in r for r in _metric_rows(app)), _metric_rows(app)
        assert any("↑" in r for r in _metric_rows(app))

        # 第二次调用：用上一次实测的每字量折算，中途就给估算 token
        app.send_chat("再来")
        await pilot.pause()
        emit("model.start", {"model_name": "m"})
        await pilot.pause()  # 让 model.start 真的落地，否则下面改的起始时刻会被覆盖
        emit("stream.thinking.start", {"reply_id": "r2", "block_id": "t2"})
        emit("stream.thinking", {"reply_id": "r2", "block_id": "t2", "text_delta": "又想到一些内容。"})
        app.store.current.call_started -= 2.0  # 让吞吐这一行真的凑得出来
        await tick(pilot)

        assert any("≈" in r and "输出" in r for r in _metric_rows(app)), _metric_rows(app)
        # 真值行与在途行是同一个量的两个时刻，不能同时出现成两行"吞吐"
        assert sum(1 for r in _metric_rows(app) if "吞吐" in r) == 1, _metric_rows(app)
