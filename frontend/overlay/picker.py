"""选择浮窗：在几个取值里挑一个，↑↓ + Enter。

由 ``frontend/overlay.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any, Callable
from rich.text import Text
from textual import on
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from ..theme import (GLYPH_ACTIVE, S_FAINT, S_TEXT, S_USER)
from .base import Choice, Overlay




class Picker(Overlay):
    """选择浮窗：↑↓ 挑一项，回车确认。

    凡是「在几个取值里选一个」的命令都走这里，命令本身一律不带参数。
    候选项可以异步填充（列表往往来自后端回执）。
    """

    HINT_TEXT = "↑↓ 选择 · Enter 确认 · Esc 取消"
    PANEL_CLASS = "picker-overlay"

    def __init__(
        self,
        kind: str,
        title: str,
        on_select: Callable[[Any], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.TITLE_TEXT = title
        self.kind = kind
        self._on_select = on_select
        self._choices: list[Choice] = []
        self._current: Any = None
        self._mounted = False

    def build_body(self) -> list[Any]:
        return [OptionList(id="picker-list")]

    def on_body_ready(self) -> None:
        self._mounted = True
        self._render_choices()

    def set_choices(
        self, choices: list[Choice], current: Any = None
    ) -> None:
        """填充或更新候选项；主体还没就绪就先存着，就绪那一遍统一渲染。"""
        self._choices = list(choices)
        self._current = current
        if self._mounted:
            self._render_choices()

    def _render_choices(self) -> None:
        listing = self.query_body("#picker-list", OptionList)
        if listing is None:
            return
        listing.clear_options()
        # id 用下标而不是值：值可能带空格或符号，不是合法的查询标识
        for index, choice in enumerate(self._choices):
            listing.add_option(Option(self._label(choice), id=str(index)))
        if not listing.options:
            listing.add_option(Option(Text("暂无可选项", style=S_FAINT), disabled=True))
            self.set_hint("没有可选项", "info")
            return
        listing.highlighted = self._index_of_current()
        listing.focus()

    def _label(self, choice: Choice) -> Text:
        active = choice.value == self._current
        row = Text()
        row.append(f"{GLYPH_ACTIVE} " if active else "  ")
        row.append(choice.label, style=S_USER if active else S_TEXT)
        if choice.note:
            row.append(f"  {choice.note}", style=S_FAINT)
        return row

    def _index_of_current(self) -> int:
        for index, choice in enumerate(self._choices):
            if choice.value == self._current:
                return index
        return 0

    @on(OptionList.OptionSelected, "#picker-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        try:
            index = int(str(event.option_id))
        except (TypeError, ValueError):
            return
        if not 0 <= index < len(self._choices):
            return
        value = self._choices[index].value
        self.dismiss(value)
        if self._on_select is not None:
            self._on_select(value)
