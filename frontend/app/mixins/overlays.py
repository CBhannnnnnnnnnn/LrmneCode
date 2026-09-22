"""浮窗编排：模型配置 / 会话切换 / 选择 / 单行输入的开启与关闭。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any, Callable
from ...overlay import (Choice, ModelConfigOverlay, Overlay, Picker, Prompt, SessionSwitcherOverlay)


class OverlaysMixin:
    """浮窗编排：模型配置 / 会话切换 / 选择 / 单行输入的开启与关闭。"""

    def open_model_config(self) -> None:
        """打开模型配置浮窗（/model 或 F2）；已在窗口内则不重复入栈。"""
        if self.model_config_overlay is not None:
            return
        self.push_screen(ModelConfigOverlay())

    def open_sessions(self) -> None:
        """打开会话切换浮窗（/sessions 或 F3）。"""
        if self.session_overlay is not None:
            return
        self.push_screen(SessionSwitcherOverlay())

    def open_picker(
        self,
        kind: str,
        title: str,
        choices: list[Choice] | None = None,
        current: Any = None,
        on_select: Callable[[Any], None] | None = None,
    ) -> Picker:
        """打开一个选择浮窗。

        命令本身永远不带参数：需要选值时弹这张卡片，用 ↑↓/Enter 挑。
        ``choices`` 省略时由后续回执经 :meth:`Picker.set_choices` 填入。
        """
        self.close_overlays()
        picker = Picker(kind, title, on_select)
        self.push_screen(picker)
        if choices is not None:
            picker.set_choices(choices, current)
        return picker

    def open_prompt(
        self,
        kind: str,
        title: str,
        caption: str = "",
        value: str = "",
        placeholder: str = "",
        on_submit: Callable[[str], None] | None = None,
    ) -> Prompt:
        """打开一个单行输入浮窗（路径这类自由文本）。"""
        self.close_overlays()
        prompt = Prompt(kind, title, caption, value, placeholder, on_submit)
        self.push_screen(prompt)
        return prompt

    def close_overlays(self) -> None:
        """关掉所有浮窗，把焦点交还输入框。"""
        changed = False
        while isinstance(self.screen, Overlay):
            self.screen.dismiss(None)
            changed = True
        if changed:
            self.input_area.focus()

    def action_model_config(self) -> None:
        self.open_model_config()

    def action_sessions(self) -> None:
        self.open_sessions()
