"""浮窗基类与共用件：边框外壳、提示行、Choice、文本裁剪与定位辅助。

由 ``frontend/overlay.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from ..theme import (GLYPH_PANEL, S_ERR, S_FAINT, S_OK, S_TEXT, S_WARN)
from .styles import OVERLAY_CSS


# 提示语配色：与 NoticeLine 的语义一致
_HINT_STYLE = {"info": S_FAINT, "success": S_OK, "error": S_ERR, "warn": S_WARN}


class Overlay(ModalScreen):
    """模态浮窗基类：居中面板 + 标题 + 主体 + 提示行。"""

    CSS = OVERLAY_CSS
    # 关掉作用域：Textual 默认把部件类 CSS 的每条规则前面再缀上**子类自己**的类型名
    # （``ModelConfigOverlay.CSS`` 里的 ``Overlay {…}`` 会被改写成
    # ``ModelConfigOverlay Overlay {…}``，于是永远匹配不到浮窗自己）。
    # 本模块的规则本来就都带着 overlay 专属选择器，全局注册即可。
    SCOPED_CSS = False
    # priority：浮窗作为模态必须独占 Esc，否则会被 App 的 Esc 抢走
    BINDINGS = [Binding("escape", "close_overlay", "关闭", priority=True)]

    TITLE_TEXT = ""
    HINT_TEXT = "Esc 关闭"
    PANEL_CLASS = ""

    def compose(self) -> ComposeResult:
        classes = "overlay-panel" + (f" {self.PANEL_CLASS}" if self.PANEL_CLASS else "")
        with Vertical(classes=classes):
            yield Static(
                Text(f"{GLYPH_PANEL} {self.TITLE_TEXT}", style=S_TEXT),
                classes="overlay-title",
            )
            with VerticalScroll(classes="overlay-body"):
                for widget in self.build_body():
                    yield widget
            with Horizontal(classes="overlay-footer"):
                yield Static(Text(self.HINT_TEXT, style=S_FAINT), classes="overlay-hint")
                for widget in self.build_footer():
                    yield widget

    def build_body(self) -> list[Any]:
        """子类返回主体部件列表（可滚动区域）。"""
        return []

    def build_footer(self) -> list[Any]:
        """子类返回底部动作部件（常驻可见，不随主体滚动）。"""
        return []

    def on_mount(self) -> None:
        # compose 与 mount 是两条独立消息：on_mount 触发时子部件可能尚未生成，
        # 需要查询 DOM 的初始化因此放在 mount_composed_widgets 之后。
        pass

    async def mount_composed_widgets(self, widgets: list[Any]) -> None:
        await super().mount_composed_widgets(widgets)
        self.on_body_ready()

    def on_body_ready(self) -> None:
        """主体部件挂载完成后的初始化钩子（子类覆写）。"""

    def query_body(self, selector: str, expect: type | None = None) -> Any:
        """查询主体部件；组合尚未完成时返回 None，而不是抛 NoMatches。"""
        try:
            return self.query_one(selector, expect)
        except NoMatches:
            return None

    def set_hint(self, text: str, level: str = "info") -> None:
        hint = self.query_body(".overlay-hint", Static)
        if hint is None:
            return
        hint.update(Text(text, style=_HINT_STYLE.get(level, S_FAINT)))

    def action_close_overlay(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True)
class Choice:
    """Picker 里的一行：展示文本 + 弱化说明 + 选中后回传的值。"""

    label: str
    note: str = ""
    value: Any = None


# ---------- 会话切换 ----------

# 选项 id 前缀：区分「本进程已打开」与「磁盘存档」，两类动作不同
_OPEN_PREFIX = "open:"


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _entry_index(listing: OptionList, current: str) -> int:
    """光标优先落在当前会话那一行；没有则落在第一个可选行。"""
    first_enabled: int | None = None
    for index, option in enumerate(listing.options):
        if option.disabled:
            continue
        if option.id == _OPEN_PREFIX + current:
            return index
        if first_enabled is None:
            first_enabled = index
    return first_enabled if first_enabled is not None else 0
