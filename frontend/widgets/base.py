"""块的基元与共用格式化：流式 Markdown、Block 外壳、状态写入、耗时与路径裁剪。

由 ``frontend/widgets.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

import json
from typing import Any
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Markdown)




def _tail_path(path: str, depth: int = 2) -> str:
    """只留路径末尾几段：状态栏一行里放不下完整路径，尾段才是用户认得的部分。"""
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    return "/".join(parts[-depth:])


class StreamMarkdown(Markdown):
    """流式 Markdown：挂载完成前的内容先缓冲，挂载后统一补上。

    Markdown 的 on_mount 会清掉挂载前写入的内容，所以不能直接 update；
    把缓冲放在部件内部，调用方无需关心挂载时序。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ready = False
        self._pending: str | None = None

    def on_mount(self) -> None:
        self.ready = True
        if self._pending is not None:
            self.update(self._pending)
            self._pending = None

    def push(self, text: str) -> None:
        """已就绪则直接渲染，否则先记下来等挂载后补渲染。"""
        if self.ready:
            self.update(text)
        else:
            self._pending = text


def _one_line(text: str, limit: int) -> str:
    """压成单行并截断。"""
    line = " ".join(text.split())
    if len(line) > limit:
        return line[:limit] + "…"
    return line


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


# 边框状态的语义档位：CSS 里各有一条 .block.state-* 决定状态色
_STATE_CLASSES = ("running", "ok", "err", "warn")


def set_block_state(widget: Any, text: str, state: str = "") -> None:
    """换掉「一块」右下角的状态：``state`` 给颜色，``text`` 给内容；空即清空。

    边框标题/状态只能整体吃一个样式（Textual 的 ``_BorderTitle`` 只存一条
    样式信息），所以分色只能走 CSS 类；而 Textual 的 CSS 没有 ``:not()``，
    三档类名只能逐个摘掉。
    """
    for name in _STATE_CLASSES:
        widget.remove_class(f"state-{name}")
    if state:
        widget.add_class(f"state-{state}")
    widget.border_subtitle = Text(text)


class Block(Vertical):
    """转录区的展示单位：一个圆角框，左上写「这是什么」，右下写「进行到哪」。

    标题与状态都画在边框上，不占正文行。标题一律传 ``Text``：边框标题走
    ``render_str``，字符串会被当 console markup 解析，工具参数里一个方括号
    就能把标题吃掉。
    """

    def __init__(self, title: str | Text, *, classes: str = "", **kwargs: Any) -> None:
        super().__init__(classes=f"block {classes}".strip(), **kwargs)
        self.border_title = title if isinstance(title, Text) else Text(title)

    def set_state(self, text: str, state: str = "") -> None:
        set_block_state(self, text, state)


def _as_mapping(text: str) -> Any:
    """source 可能是 dict，也可能是被后端 dump 成字符串的 dict。"""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    return parsed if isinstance(parsed, dict) else text
