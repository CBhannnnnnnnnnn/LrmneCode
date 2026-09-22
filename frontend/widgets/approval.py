"""审批面板：工具摘要 + diff + 三个扁平按钮。

由 ``frontend/widgets.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any
from rich.text import Text
from textual.containers import (Horizontal, Vertical)
from textual.message import Message
from textual.widgets import (Button, Static)
from .. import (tools)
from ..theme import (GLYPH_PANEL, GLYPH_TOOL, S_ERR, S_OK)
from .tool_view import _call_parts, diff_fold


class ApprovalPanel(Vertical):
    """审批请求面板：聚焦后按 y / n / a，或点击三个按钮。

    面板里给的是「将要做什么」而不是参数原文：编辑显示彩色 diff，命令显示命令行，
    审批才有依据。``a`` / 「允许并记住」走的是 ``approval.respond`` 的 ``always``：
    后端把引擎建议的那一类放行规则回灌进权限引擎（同命令前缀、同目录），
    从此不再询问——不改全局权限模式，也不新增协议能力。
    """

    class Decision(Message):
        """用户对某个审批面板做出决定；``always`` 表示同时记住这一类放行。"""

        def __init__(
            self, panel: "ApprovalPanel", approved: bool, always: bool = False
        ) -> None:
            super().__init__()
            self.panel = panel
            self.approved = approved
            self.always = always

    can_focus = True
    BINDINGS = [
        ("y", "approve", "允许"),
        ("n", "deny", "拒绝"),
        ("a", "approve_and_remember", "允许并记住"),
    ]

    def __init__(
        self, approval_request_id: str, tool_calls: list[dict], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.approval_request_id = approval_request_id
        self._tool_calls = tool_calls
        self.resolved = False
        # 快捷键不写进边框：三个按钮就是全部选项，底部那一行说明是噪音
        self.border_title = f"{GLYPH_PANEL} 工具执行审批"

    def compose(self):
        for tc in self._tool_calls:
            name, args = _call_parts(tc)
            yield Static(tools.summary(name, args), classes="approval-item")
            fold = diff_fold(name, args)
            if fold is not None:
                yield fold
        yield Horizontal(
            Button("允许", compact=True, id="btn-approve"),
            Button("拒绝", compact=True, id="btn-deny"),
            # 「记住」放行的是这一类：引擎随审批给出的建议规则（同命令前缀 / 同目录）
            Button("允许并记住", compact=True, id="btn-remember"),
            classes="approval-buttons",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        self.post_message(
            self.Decision(
                self, button != "btn-deny", always=button == "btn-remember"
            )
        )

    def action_approve(self) -> None:
        self.post_message(self.Decision(self, True))

    def action_deny(self) -> None:
        self.post_message(self.Decision(self, False))

    def action_approve_and_remember(self) -> None:
        self.post_message(self.Decision(self, True, always=True))

    def mark_resolved(self, approved: bool) -> None:
        self.resolved = True
        self.add_class("resolved")
        self.border_title = f"{GLYPH_PANEL} 已处理"
        for button in self.query(Button):
            button.disabled = True
        note = (
            Text(f"{GLYPH_TOOL} 已允许，等待工具执行…", style=S_OK)
            if approved
            else Text(f"{GLYPH_TOOL} 已拒绝。", style=S_ERR)
        )
        self.mount(Static(note, classes="approval-item"))
