"""单行输入浮窗。

由 ``frontend/overlay.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any, Callable
from rich.text import Text
from textual import on
from textual.widgets import Button, Input, OptionList, Select, Static
from ..theme import (S_DIM)
from .base import Overlay




class Prompt(Overlay):
    """单行输入浮窗：路径这类自由文本用它，替代「/命令 参数」的写法。"""

    HINT_TEXT = "回车确认 · Esc 取消"
    PANEL_CLASS = "prompt-overlay"

    def __init__(
        self,
        kind: str,
        title: str,
        caption: str = "",
        value: str = "",
        placeholder: str = "",
        on_submit: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.TITLE_TEXT = title
        self.kind = kind
        self._caption = caption
        self._value = value
        self._placeholder = placeholder
        self._on_submit = on_submit

    def build_body(self) -> list[Any]:
        return [
            Static(Text(self._caption, style=S_DIM), classes="prompt-caption")
            if self._caption
            else Static("", classes="prompt-caption"),
            Input(placeholder=self._placeholder, id="prompt-input"),
        ]

    def on_body_ready(self) -> None:
        field = self.query_body("#prompt-input", Input)
        if field is None:
            return
        field.value = self._value
        field.focus()

    @on(Input.Submitted, "#prompt-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit(event.value.strip())

    def _submit(self, value: str) -> None:
        if not value:
            self.set_hint("不能留空", "warn")
            return
        self.dismiss(value)
        if self._on_submit is not None:
            self._on_submit(value)
