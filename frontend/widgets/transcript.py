"""转录内容块：助手回复、用户输入、通知行、卡片、思考流、系统提示。

由 ``frontend/widgets.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

import time
from typing import Any
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import (Collapsible, Static)
from ..theme import (GLYPH_HINT, GLYPH_NOTICE, GLYPH_PANEL, GLYPH_SPINNER, GLYPH_THINKING, GLYPH_USER, S_DIM, S_ERR, S_FAINT, S_OK, S_SLASH, S_TEXT, S_USER, S_WARN)
from .base import Block, StreamMarkdown, _as_mapping, _fmt_elapsed, set_block_state


# 通知行图标 → 颜色
_NOTICE_STYLE = {
    "info": S_DIM,
    "success": S_OK,
    "error": S_ERR,
    "warn": S_WARN,
    "busy": S_TEXT,
}


class AssistantBlock(Block):
    """Agent 回复：一块就是一个回复块，框内是流式 Markdown，右下角写这次调用的产出。

    用量是「每次模型调用」的粒度，而块先结束、``model.end`` 后到，所以先写
    「已生成」，等 ``mark_usage`` 拿到 token 数再补上；这次调用没有正文时不留状态。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(f"{GLYPH_PANEL} Agent", classes="assistant-block", **kwargs)
        self.markdown = StreamMarkdown("", classes="assistant-text")
        self._frame = 0
        self._wrote = False
        self._finished = False
        self._tokens = 0
        # 已落地的正文字数：一次调用的产出 token 要按字数分摊到各块（见 share_usage）
        self.chars = 0

    def compose(self) -> ComposeResult:
        yield self.markdown

    def push(self, text: str) -> None:
        self._frame += 1
        self._wrote = bool(text)
        self.chars = len(text)
        self.markdown.push(text)
        self._render_state()

    def finish(self, text: str) -> None:
        self._finished = True
        self._wrote = bool(text)
        self.chars = len(text)
        self.markdown.push(text)
        self._render_state()

    def mark_usage(self, tokens: int) -> None:
        """补上这次模型调用里归到本块的 token 数；同一个块只标一次。"""
        if tokens <= 0 or self._tokens:
            return
        self._tokens = tokens
        self._render_state()

    def _render_state(self) -> None:
        if not self._wrote:
            set_block_state(self, "")
            return
        if not self._finished:
            spinner = GLYPH_SPINNER[self._frame % len(GLYPH_SPINNER)]
            set_block_state(self, f"{spinner} 生成中", "running")
            return
        note = f"已生成 · {self._tokens} tok" if self._tokens else "已生成"
        set_block_state(self, note, "ok")


class UserMessage(Static):
    """用户输入回显：``❯ text``；斜杠命令用等待色区分。"""

    def __init__(self, text: str, slash: bool = False, **kwargs: Any) -> None:
        super().__init__(classes="user-message", **kwargs)
        style = S_SLASH if slash else S_USER
        rendered = Text()
        rendered.append(f"{GLYPH_USER} ", style=style)
        rendered.append(text, style=style)
        self.update(rendered)


class NoticeLine(Static):
    """系统提示行：字形 + 文案。kind: info|success|error|warn|busy。"""

    def __init__(self, text: str, kind: str = "info", **kwargs: Any) -> None:
        super().__init__(classes=f"notice notice-{kind}", **kwargs)
        glyph = GLYPH_NOTICE.get(kind, GLYPH_NOTICE["info"])
        style = _NOTICE_STYLE.get(kind, _NOTICE_STYLE["info"])
        self.update(Text(f"{glyph} {text}", style=style))


class Card(Static):
    """圆角边框 + 内嵌标题的信息卡片（欢迎 / 帮助 / 配置 / 状态）。"""

    def __init__(self, title: str, body: Text, **kwargs: Any) -> None:
        super().__init__(body, classes="card", **kwargs)
        self.border_title = f"{GLYPH_PANEL} {title}"


class ThinkingBlock(Collapsible):
    """思考流：一个 ◇ thinking 块，展开是弱化正文，结束后自动折叠成一行。

    流入过程中正文只显示最近一段，避免把聊天区顶走；耗时写在边框右下角，
    ``finish`` 后折叠——收起时也知道这次想了多久。耗时从部件创建算起，
    也就是 ``stream.thinking.start`` 到达的时刻。

    思考也是模型的产出，所以 ``model.end`` 会把这次调用的 output token 按字数
    分摊一份过来（``mark_usage``），收起的标题行同时报时长与 token。
    """

    # 流式过程中正文的尾部窗口（字符）：够读两句，又不至于撑满屏幕
    LIVE_TAIL = 320

    def __init__(self, block_id: str, **kwargs: Any) -> None:
        self.block_id = block_id
        self._buffer = ""
        self._frame = 0
        self._timer = None
        self._finished = False
        self._tokens = 0
        self._started = time.monotonic()
        self._body = Static("", classes="thinking-body")
        super().__init__(
            self._body,
            title=f"{GLYPH_THINKING} thinking",
            collapsed=False,
            classes="block thinking",
            **kwargs,
        )

    @property
    def chars(self) -> int:
        """已流入的思考字数：分摊这次调用的产出 token 时用。"""
        return len(self._buffer)

    def on_mount(self) -> None:
        # 思考可能整段在挂载前就流完了（挂载是延迟落地的），那时不该再转动画
        if self._finished:
            return
        self._timer = self.set_interval(0.15, self._tick)
        self._render_state()

    def _tick(self) -> None:
        if self._finished or not self.is_attached:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            return
        self._frame += 1
        self._render_state()

    def _elapsed(self) -> float:
        return time.monotonic() - self._started

    @property
    def finished(self) -> bool:
        """是否已收尾；run 结束时的兜底据此只收尾还开着的块。"""
        return self._finished

    def _render_state(self) -> None:
        spinner = GLYPH_SPINNER[self._frame % len(GLYPH_SPINNER)]
        set_block_state(
            self,
            f"{spinner} 思考中 · {_fmt_elapsed(self._elapsed())}",
            "running",
        )

    def _render_finished_state(self) -> None:
        note = f"已思考 · {_fmt_elapsed(self._elapsed())}"
        if self._tokens:
            note += f" · {self._tokens} tok"
        set_block_state(self, note, "ok")

    def mark_usage(self, tokens: int) -> None:
        """补上这次调用里归到思考的 token 数；``model.end`` 晚于 finish，故可后补。"""
        if tokens <= 0 or self._tokens:
            return
        self._tokens = tokens
        if self._finished:
            self._render_finished_state()

    def append_delta(self, delta: str) -> None:
        self._buffer += delta
        tail = " ".join(self._buffer.split())
        if len(tail) > self.LIVE_TAIL:
            tail = "…" + tail[-self.LIVE_TAIL :]
        self._body.update(Text(tail, style=f"italic {S_FAINT}"))
        self._render_state()

    def finish(self) -> None:
        self._finished = True
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._body.update(Text(" ".join(self._buffer.split()), style=f"italic {S_FAINT}"))
        self._render_finished_state()
        self.collapsed = True


class HintBlock(Static):
    """系统提示（runtime 注入的 system-reminder）：只留一行人类可读的标记。

    这类内容是喂给模型的运行时上下文（当前时间、时区、「以下为准」那套措辞），
    属于实现细节而不是对话，铺在聊天区里对用户没有价值，因此正文不进转录。
    """

    def __init__(self, source: Any, **kwargs: Any) -> None:
        super().__init__(classes="hint", **kwargs)
        self.update(Text(f"{GLYPH_HINT} {self._label(source)}", style=S_FAINT))

    @staticmethod
    def _label(source: Any) -> str:
        if isinstance(source, str):
            source = _as_mapping(source)
        if isinstance(source, dict):
            parts = [
                str(source[key])
                for key in ("label", "sublabel")
                if source.get(key)
            ]
            return " · ".join(parts) if parts else "系统提示"
        if source:
            return str(source)
        return "系统提示"
