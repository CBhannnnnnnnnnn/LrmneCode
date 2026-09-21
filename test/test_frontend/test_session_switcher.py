"""会话切换浮窗：本进程会话切换 / 磁盘存档载入 / 删除需二次确认。"""

from __future__ import annotations

import pytest
from textual.widgets import OptionList

from conftest import plain, show_sessions
from frontend.overlay import SessionSwitcherOverlay
from frontend.theme import GLYPH_ACTIVE
from proxy_layer.schema import make_receipt_success

DISK = [
    {"cid": "c-old", "summary": "修复登录并发", "modified": 1758000000.0},
    {"cid": "c-ancient", "summary": "", "modified": 1757000000.0},
]


async def _open(app, pilot, sessions=DISK):
    conv = app.store.current
    app.open_sessions()
    await pilot.pause()
    overlay = app.session_overlay
    assert isinstance(overlay, SessionSwitcherOverlay)
    show_sessions(app, conv, sessions)
    await pilot.pause()
    return overlay


async def _add_conversation(app, pilot) -> str:
    """开一个新会话并等它挂好，返回新 cid。"""
    app.new_conversation()
    await pilot.pause()
    return app.store.current_cid


def _rows(overlay) -> list[tuple[str, str, bool]]:
    """(option_id, 展示文本, 是否禁用)。"""
    listing = overlay.query_one("#session-list", OptionList)
    return [
        (str(option.id or ""), plain(option.prompt), option.disabled)
        for option in listing.options
    ]


def _index_of(overlay, option_id: str) -> int:
    for index, (value, _, _) in enumerate(_rows(overlay)):
        if value == option_id:
            return index
    raise AssertionError(f"未找到 {option_id}: {_rows(overlay)}")


async def _highlight(overlay, pilot, option_id: str) -> None:
    listing = overlay.query_one("#session-list", OptionList)
    listing.highlighted = _index_of(overlay, option_id)
    await pilot.pause()


@pytest.mark.anyio
async def test_opening_lists_open_and_disk_sessions(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        current = app.store.current_cid
        overlay = await _open(app, pilot)

        rows = _rows(overlay)
        assert ("", "本进程", True) in rows
        assert ("", "磁盘存档", True) in rows
        assert f"open:{current}" in [row[0] for row in rows]
        assert [row[0] for row in rows if row[0].startswith("disk:")] == [
            "disk:c-old",
            "disk:c-ancient",
        ]
        # 默认光标落在当前会话上
        assert overlay.query_one("#session-list", OptionList).highlighted_option.id == (
            f"open:{current}"
        )


@pytest.mark.anyio
async def test_opening_twice_pushes_only_one_window(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await _open(app, pilot)
        app.open_sessions()
        await pilot.pause()

        stacked = [s for s in app.screen_stack if isinstance(s, SessionSwitcherOverlay)]
        assert len(stacked) == 1


@pytest.mark.anyio
async def test_current_session_row_is_marked_and_shows_progress(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        conv.inflight.add("req-x")
        conv.tokens_in = 1500
        conv.tokens_out = 240
        overlay = await _open(app, pilot)

        label = plain(overlay.query_one("#session-list", OptionList).highlighted_option.prompt)
        assert label.startswith(GLYPH_ACTIVE)
        assert conv.cid in label
        assert "当前" in label and "working" in label and "↑1.5k ↓240" in label


@pytest.mark.anyio
async def test_enter_switches_to_an_open_session(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.store.current_cid
        await _add_conversation(app, pilot)

        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, f"open:{first}")
        await pilot.press("enter")
        await pilot.pause()

        assert app.store.current_cid == first
        assert app.session_overlay is None


@pytest.mark.anyio
async def test_enter_on_disk_row_loads_into_a_new_conversation(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        original = app.store.current_cid
        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, "disk:c-old")
        await pilot.press("enter")
        await pilot.pause()

        command = app.client.last("session.resume")
        assert command is not None
        assert command.parameters == {"source_cid": "c-old"}
        # 存档写到新会话里，原会话不受影响
        assert command.conversation_id != original
        assert command.conversation_id == app.store.current_cid
        assert app.session_overlay is None


@pytest.mark.anyio
async def test_resume_receipt_lands_in_the_new_conversation(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        original = app.store.current
        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, "disk:c-old")
        await pilot.press("enter")
        await pilot.pause()

        command = app.client.last("session.resume")
        target = app.store.get(command.conversation_id)
        assert target is not None and target is not original

        app._handle_receipt(
            make_receipt_success(
                command.request_id,
                target.cid,
                {"source_cid": "c-old", "target_cid": target.cid, "resumed": True},
            )
        )
        await pilot.pause()

        assert "已载入存档 c-old" in plain(target.view.children[-1])
        assert "已载入存档" not in plain(original.view.children[-1])


@pytest.mark.anyio
async def test_x_closes_only_the_in_process_session(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.store.current_cid
        second = await _add_conversation(app, pilot)

        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, f"open:{first}")
        await pilot.press("x")
        await pilot.pause()

        assert app.store.get(first) is None
        # 关掉的是别的会话：当前会话不变，磁盘存档也没被碰
        assert app.store.current_cid == second
        assert app.client.last("session.delete") is None
        assert "已关闭" in plain(overlay.query_one(".overlay-hint"))
        assert f"open:{first}" not in [row[0] for row in _rows(overlay)]


@pytest.mark.anyio
async def test_closing_a_session_keeps_focus_in_the_window(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.store.current_cid
        await _add_conversation(app, pilot)

        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, f"open:{first}")
        await pilot.press("x")
        await pilot.pause()

        assert overlay.query_one("#session-list", OptionList).has_focus


@pytest.mark.anyio
async def test_d_requires_a_second_press_to_delete(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, "disk:c-old")

        await pilot.press("d")
        await pilot.pause()
        # 第一次只警告，不发删除命令
        assert app.client.last("session.delete") is None
        assert "再按一次 d" in plain(overlay.query_one(".overlay-hint"))

        await pilot.press("d")
        await pilot.pause()
        command = app.client.last("session.delete")
        assert command is not None
        assert command.parameters == {"cid": "c-old"}


@pytest.mark.anyio
async def test_moving_the_cursor_cancels_a_pending_delete(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, "disk:c-old")
        await pilot.press("d")
        await pilot.pause()

        await _highlight(overlay, pilot, "disk:c-ancient")
        await pilot.press("d")
        await pilot.pause()

        assert app.client.last("session.delete") is None
        assert "c-ancient" in plain(overlay.query_one(".overlay-hint"))


@pytest.mark.anyio
async def test_delete_receipt_drops_the_row(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        overlay = await _open(app, pilot)
        await _highlight(overlay, pilot, "disk:c-old")
        app.delete_session("c-old")
        command = app.client.last("session.delete")

        app._handle_receipt(
            make_receipt_success(
                command.request_id, conv.cid, {"cid": "c-old", "deleted": True}
            )
        )
        await pilot.pause()

        ids = [row[0] for row in _rows(overlay)]
        assert "disk:c-old" not in ids
        assert "disk:c-ancient" in ids


@pytest.mark.anyio
async def test_d_on_an_open_row_is_refused(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        overlay = await _open(app, pilot)

        await pilot.press("d")
        await pilot.pause()

        assert app.client.last("session.delete") is None
        assert "只有磁盘存档" in plain(overlay.query_one(".overlay-hint"))


@pytest.mark.anyio
async def test_f3_opens_and_escape_closes(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        assert app.session_overlay is not None

        await pilot.press("escape")
        await pilot.pause()
        assert app.session_overlay is None
        assert app.input_area.has_focus


@pytest.mark.anyio
async def test_session_list_receipt_does_not_add_a_card_when_open(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        before = len(conv.view.children)
        await _open(app, pilot)

        assert len(conv.view.children) == before


@pytest.mark.anyio
async def test_session_list_receipt_adds_a_card_when_closed(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        app.send("session.list", {})
        show_sessions(app, conv, DISK)
        await pilot.pause()

        card = conv.view.children[-1]
        # 卡片标题走 border_title，不在 render() 里
        assert "历史会话" in str(card.border_title)
        body = plain(card)
        assert "c-old" in body and "修复登录并发" in body
