"""会话切换浮窗：本进程会话切换 / 磁盘存档载入 / 删除二次确认。

由 ``frontend/overlay.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from ..theme import (GLYPH_ACTIVE, S_DIM, S_FAINT, S_LABEL, S_TEXT, S_USER, S_WARN)
from .base import Overlay, _OPEN_PREFIX, _entry_index, _shorten


_DISK_PREFIX = "disk:"

_HINT = "↑↓ 选择 · Enter 切换 / 载入 · x 关闭本进程会话 · d 删除存档 · Esc 关闭"


def _format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


class SessionSwitcherOverlay(Overlay):
    """会话切换浮窗：上半是本进程已打开的会话，下半是磁盘存档。

    两者的「进入」语义不同，所以分成两组：
    - 本进程会话：直接切前台，草稿与滚动位置都还在。
    - 磁盘存档：后端 ``session.resume`` 固定写进**当前** cid，因此先开一个空会话
      再把存档读进去，不会覆盖用户正开着的会话。
    """

    TITLE_TEXT = "会话"
    HINT_TEXT = _HINT
    PANEL_CLASS = "session-overlay"

    BINDINGS = [
        # 基类靠 Overlay.CSS + 继承拿到弹窗样式，但 BINDINGS 是整体覆盖，Esc 要重申
        Binding("escape", "close_overlay", "关闭", priority=True),
        Binding("x", "close_session", "关闭会话"),
        Binding("d", "delete_disk", "删除存档"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._open: list[Any] = []
        self._disk: list[dict] = []
        self._confirm = ""
        self._mounted = False

    # ---------- 骨架 ----------

    def build_body(self) -> list[Any]:
        return [OptionList(id="session-list")]

    def on_body_ready(self) -> None:
        self._mounted = True
        self.app.send("session.list", {})
        self._render_sessions()

    # ---------- 数据入口（由 results.py 的回执渲染器调用） ----------

    def load_sessions(self, items: list) -> None:
        self._disk = [item for item in items if isinstance(item, dict)]
        if self._mounted:
            self._render_sessions()

    def forget_disk(self, cid: str) -> None:
        """删除成功后立刻摘掉该行，不再往返一次 session.list（两者都是 PLAIN 模式）。"""
        before = len(self._disk)
        self._disk = [item for item in self._disk if item.get("cid") != cid]
        if self._mounted and len(self._disk) != before:
            self._render_sessions()

    # ---------- 渲染 ----------

    def _render_sessions(self) -> None:
        if not self._mounted:
            return
        listing = self.query_body("#session-list", OptionList)
        if listing is None:
            return
        listing.clear_options()
        self._open = list(self.app.store.all())
        open_cids = {conv.cid for conv in self._open}
        current = self.app.store.current_cid

        if self._open:
            listing.add_option(Option(Text("本进程", style=S_LABEL), disabled=True))
            for conv in self._open:
                listing.add_option(
                    Option(self._open_label(conv, current), id=_OPEN_PREFIX + conv.cid),
                )
        if self._disk:
            listing.add_option(Option(Text("磁盘存档", style=S_LABEL), disabled=True))
            for item in self._disk:
                cid = str(item.get("cid") or "")
                if cid:
                    listing.add_option(
                        Option(self._disk_label(item, open_cids), id=_DISK_PREFIX + cid),
                    )

        if not listing.options:
            listing.add_option(Option(Text("暂无会话", style=S_FAINT), disabled=True))
            self.set_hint("暂无可切换的会话", "info")
            return

        listing.highlighted = _entry_index(listing, current)
        listing.focus()

    def _open_label(self, conv: Any, current: str) -> Text:
        active = conv.cid == current
        row = Text(f"{GLYPH_ACTIVE} " if active else "  ")
        row.append(conv.cid, style=S_USER if active else S_TEXT)
        if active:
            row.append("  当前", style=S_FAINT)
        state = conv.run_state
        if state != "idle":
            row.append(f"  {state}", style=S_WARN if state == "working" else S_FAINT)
        if conv.tokens_in or conv.tokens_out:
            row.append(
                f"  ↑{_format_tokens(conv.tokens_in)} ↓{_format_tokens(conv.tokens_out)}",
                style=S_FAINT,
            )
        return row

    def _disk_label(self, item: dict, open_cids: set[str]) -> Text:
        cid = str(item.get("cid") or "?")
        row = Text("  ")
        row.append(cid, style=S_TEXT)
        if cid in open_cids:
            row.append("  已打开", style=S_FAINT)
        summary = str(item.get("summary") or "")
        if summary:
            row.append(f"  {_shorten(summary, 36)}", style=S_DIM)
        modified = item.get("modified")
        if isinstance(modified, (int, float)):
            stamp = datetime.fromtimestamp(modified).strftime("%m-%d %H:%M")
            row.append(f"  {stamp}", style=S_FAINT)
        return row

    # ---------- 动作 ----------

    def _highlighted_id(self) -> str:
        listing = self.query_body("#session-list", OptionList)
        option = listing.highlighted_option if listing is not None else None
        return str(option.id or "") if option is not None else ""

    @on(OptionList.OptionHighlighted, "#session-list")
    def _on_highlight(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if self._confirm:
            self._confirm = ""
            self.set_hint(self.HINT_TEXT)

    @on(OptionList.OptionSelected, "#session-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        target = str(event.option_id or "")
        if target.startswith(_OPEN_PREFIX):
            cid = target[len(_OPEN_PREFIX) :]
            if self.app.store.get(cid) is not None:
                self.app.activate(cid)
            self.dismiss(None)
        elif target.startswith(_DISK_PREFIX):
            self.app.resume_conversation(target[len(_DISK_PREFIX) :])
            self.dismiss(None)

    def action_close_session(self) -> None:
        target = self._highlighted_id()
        if not target.startswith(_OPEN_PREFIX):
            self.set_hint("只有本进程已打开的会话可以关闭", "warn")
            return
        cid = target[len(_OPEN_PREFIX) :]
        self._confirm = ""
        self.app.close_conversation(cid)
        self._render_sessions()
        self.set_hint(f"已关闭 {cid}（磁盘存档未删除）", "success")

    def action_delete_disk(self) -> None:
        target = self._highlighted_id()
        if not target.startswith(_DISK_PREFIX):
            self.set_hint("只有磁盘存档可以删除", "warn")
            return
        cid = target[len(_DISK_PREFIX) :]
        if self._confirm != cid:
            # 删存档不可恢复，所以要求再按一次同一个键确认
            self._confirm = cid
            self.set_hint(f"再按一次 d 确认删除 {cid} 的磁盘存档（不可恢复）", "warn")
            return
        self._confirm = ""
        self.app.delete_session(cid)
        self.set_hint(f"正在删除 {cid}…", "warn")
