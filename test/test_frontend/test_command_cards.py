"""命令卡片的端到端冒烟：输入命令 → 弹卡片 → 选中 → 发出对应的协议命令。

锁的是「命令不带参数」这条约束的另一半：取值只能从卡片里来。
"""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.widgets import Input, OptionList

from proxy_layer.schema import make_receipt_success


def _plain(widget) -> str:
    if isinstance(widget, Text):
        return widget.plain
    renderable = widget.render()
    return getattr(renderable, "plain", str(renderable))


async def _run_command(pilot, app, text: str) -> None:
    """走真实输入路径：写进输入框再回车。"""
    app.input_area.set_text(text)
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.anyio
async def test_thinking_card_picks_a_level_and_confirms_in_one_line(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        await _run_command(pilot, app, "/thinking")

        picker = app.picker_overlay
        assert picker is not None and picker.kind == "thinking"
        listing = picker.query_one("#picker-list", OptionList)
        assert listing.option_count == 4

        listing.highlighted = 3  # high
        await pilot.press("enter")
        await pilot.pause()

        assert app.picker_overlay is None
        assert app.client.last("config.set").parameters == {
            "key": "thinking_level",
            "value": "high",
        }

        # 回执带的是整个模型配置块，转录里只该出现那一句确认
        command = app.client.last("config.set")
        app._dispatch_receipt(
            make_receipt_success(
                command.request_id,
                conv.cid,
                {"model": "gpt-4", "thinking_level": "high", "mode": "default"},
            )
        )
        await pilot.pause()

        body = _plain(conv.view.children[-1])
        assert "思考级别已设为 high" in body
        assert "provider_type" not in body


@pytest.mark.anyio
async def test_diff_card_is_filled_by_the_receipt_then_asks_for_the_round(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        await _run_command(pilot, app, "/diff")

        picker = app.picker_overlay
        assert picker is not None and picker.kind == "diff"
        # 候选项还没到：卡片先立起来，收据随后填
        assert picker.query_one("#picker-list", OptionList).option_count == 1

        app._dispatch_receipt(
            make_receipt_success(
                app.client.last("diff.list").request_id,
                conv.cid,
                {
                    "rounds": [
                        {"round": 1, "files": ["a.py"]},
                        {"round": 2, "files": ["a.py", "pkg/b.py", "c.py", "d.py"]},
                    ]
                },
            )
        )
        await pilot.pause()

        listing = picker.query_one("#picker-list", OptionList)
        assert listing.option_count == 2
        second = _plain(listing.options[1].prompt)
        assert "第 2 轮" in second
        assert "4 个文件" in second

        listing.highlighted = 1
        await pilot.press("enter")
        await pilot.pause()

        assert app.client.last("diff.show").parameters == {"round": 2}


@pytest.mark.anyio
async def test_skills_card_runs_the_picked_skill(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.view.remove_children()

        await _run_command(pilot, app, "/skills")

        picker = app.picker_overlay
        assert picker is not None and picker.kind == "skills"

        app._dispatch_receipt(
            make_receipt_success(
                app.client.last("skill.list").request_id,
                conv.cid,
                [{"name": "review", "description": "代码审查"}],
            )
        )
        await pilot.pause()

        listing = picker.query_one("#picker-list", OptionList)
        assert listing.option_count == 1
        assert "review" in _plain(listing.options[0].prompt)

        await pilot.press("enter")
        await pilot.pause()

        command = app.client.last("chat.send")
        assert command is not None
        assert command.parameters["text"] == "请阅读并执行 skill: review"


@pytest.mark.anyio
async def test_cwd_prompt_prefills_the_root_from_the_config_cache(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current

        app.send("config.get", {})
        app._dispatch_receipt(
            make_receipt_success(
                app.client.last("config.get").request_id,
                conv.cid,
                {"workspace": {"root": "D:/code/demo", "agent_home": "D:/code/demo/.lrmne"}},
            )
        )
        await pilot.pause()

        await _run_command(pilot, app, "/cwd")

        field = app.screen.query_one("#prompt-input", Input)
        assert field.value == "D:/code/demo"

        field.value = "D:/code/other"
        await pilot.press("enter")
        await pilot.pause()

        assert app.client.last("config.set").parameters == {
            "key": "root",
            "value": "D:/code/other",
        }
