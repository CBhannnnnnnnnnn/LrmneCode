"""聊天区的显示部件：协议事件 → 专属渲染。

渲染规格（所有视觉 token 来自 ``theme``，本模块不写裸 hex）：
- 用户输入      ``❯ text``
- Agent 回复     ◆ Agent 块：框内是流式 Markdown，右下角写这次调用分到多少 token
- 思考流        ◇ thinking 块：框内是弱化正文，结束后折叠为一行（右下角写思考时长与 token）
- 系统提示      ▪ 折叠块：默认收起（如 runtime 注入的 system-reminder）
- 工具调用      ▤/▸ 工具块：边框标题只写工具名（按类别带图标），右下角写状态与耗时，
                框内第一行是「在干什么」（命令 / 路径 / 模式），其后才是彩色 diff
                （默认折叠）与 ``└`` 结果行
- 内部过程      工具块、结果行、思考一律走同一档浅灰（``S_INTERNAL``），
                折叠块收起时只有一行浅色标题，用户点开才提亮正文
- 审批请求      独立面板：工具摘要 + 彩色 diff + y/n/a 快捷键 + 按钮
- 数据块        text/* 内嵌展示；image/* 落地临时文件并显示路径；其余报大小
- 卡片          圆角边框 + 内嵌标题（配置/帮助/状态）
- 状态栏        底部只留一眼要看到的：运行状态（含审批数）/ 模型 / 上下文占用
- 右侧栏        其余量化信息纵向排开：上下文构成、token 计数与吞吐、缓存命中、
                思考与权限档位、工作目录、会话号

「一块」的约定（``Block``）：一次输出就是一块，标题与状态都画在边框上、不占正文行；
边框只有状态语义色会变，其余一律最弱一档灰——转录里的亮度应该来自正文本身。

工具参数的解读统一交给 ``tools``（摘要与 diff 渲染），本模块只管部件与状态流转。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.strip import Strip
from textual.widgets import (
    Button,
    Collapsible,
    Markdown,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from . import datablocks
from . import mentions
from . import tools
from .commands import SlashCommand
from .theme import (
    GLYPH_ACTIVE,
    GLYPH_HINT,
    GLYPH_INTERRUPT,
    GLYPH_METER_EMPTY,
    GLYPH_METER_FULL,
    GLYPH_ELLIPSIS,
    GLYPH_NOTICE,
    GLYPH_PANEL,
    GLYPH_QUEUED,
    GLYPH_RESULT,
    GLYPH_SPINNER,
    GLYPH_THINKING,
    GLYPH_TOOL,
    GLYPH_USER,
    S_DIM,
    S_ERR,
    S_FAINT,
    S_GHOST,
    S_INTERNAL,
    S_INTERNAL_OPEN,
    S_OK,
    S_REFERENCE,
    S_SLASH,
    S_TEXT,
    S_USER,
    S_WARN,
)

# 通知行图标 → 颜色
_NOTICE_STYLE = {
    "info": S_DIM,
    "success": S_OK,
    "error": S_ERR,
    "warn": S_WARN,
    "busy": S_TEXT,
}

# 上下文压力表的格子数：4 格足够看出「还早 / 过半 / 快满」
_METER_CELLS = 4

# 状态栏分段之间的分隔符（宽度固定，用于窄屏裁剪时预估）
_SEPARATOR = "  │  "
_SEP_WIDTH = cell_len(_SEPARATOR)
# 被裁掉时补的省略号（含前面的空格）
_TAIL_WIDTH = cell_len(f" {GLYPH_ELLIPSIS}")


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


def _as_mapping(text: str) -> Any:
    """source 可能是 dict，也可能是被后端 dump 成字符串的 dict。"""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    return parsed if isinstance(parsed, dict) else text


class ToolCallView(Block):
    """工具调用：一次调用就是一块——标题只写「哪个工具」，框内第一行写「在干什么」。

    边框标题挤不下一条命令，硬塞只会把整块压成一堵墙；所以标题留工具名，正文
    第一行给命令 / 路径 / 模式（随参数流入逐步变完整），其后才是改动 diff 与
    ``└ …`` 结果行。右下角是状态与耗时：运行中 → 执行中 →（等审批时）等待中 →
    完成 / 失败 / 已中断。
    整块都是内部过程，正文一律浅灰；只有失败与中断才让状态点一下颜色。
    """

    # 结果预览行的单行长度上限
    RESULT_PREVIEW = 160
    # 框内第一行（在干什么）的单行长度上限
    DETAIL_WIDTH = 120

    # runtime 注入的 system-reminder：整段包在这对标记里的文本是写给模型的，不是工具输出
    _REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)

    def __init__(self, tool_call_id: str, name: str, **kwargs: Any) -> None:
        super().__init__(
            Text(f"{tools.icon_of(name)} {tools.display_name(name)}"),
            classes="tool-call",
            **kwargs,
        )
        self.tool_call_id = tool_call_id
        self.tool_name = name
        self._raw_args = ""
        self._args: dict[str, Any] = {}
        self._args_complete = False
        self._result_tail = ""
        # running → executing → awaiting（等审批）→ done / failed / interrupted
        self._state = "running"
        self._frame = 0
        self._timer = None
        self._t0 = time.monotonic()
        self._elapsed: float | None = None
        self._paused_at: float | None = None
        self._body_done = False
        # 名字避开 Textual 内部的 ``_pending_children``（compose 的待挂列表），
        # 覆写它会让这些部件被 _compose 提前挂掉，顺序就失控了。
        self._queued_children: list[Any] = []
        # 组合是否已落地：没落地时子部件只能先排队，落地后统一插到结果行前面
        self._ready = False
        # 框内正文在构造时就备好（on_mount 是延迟落地的，事件可能赶在前面到），
        # 挂载后原地更新，免得一行一行往聊天区里追加。
        # 第一行是「这一步在干什么」（命令 / 路径 / 模式），没有就不占一行。
        self._detail = Static("", classes="tool-detail")
        self._detail.display = False
        self._detail_text: str | None = None
        # 结果预览行 ``└ …``：恒为最后一行，没有结果可给时不占一行
        # ——中断的调用就是这种情形，块只留边框那两行。
        self._live = Static("", classes="tool-result")
        self._live.display = False
        # 内联二进制结果按 block_id 攒 base64，result.end 时统一落地
        self._data_buffers: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        # 固定的首尾两行：第一行「在干什么」，最后一行结果；中间的正文挂在两者之间
        yield self._detail
        yield self._live

    def on_mount(self) -> None:
        if self._state in ("running", "executing"):
            self._timer = self.set_interval(0.12, self._tick)
        self._ready = True
        queued, self._queued_children = self._queued_children, []
        for widget in queued:
            self.mount(widget, before=self._live)
        self._render_state()

    def _mount_inner(self, widget: Any) -> None:
        """补挂框内正文/结果，一律插在结果行前面，读下来顺序不散。

        名字避开 ``Widget._attach``：那是 Textual 挂载时给子树认父的钩子
        （``App._register_child`` 会调 ``child._attach(parent)``），同名会把它顶掉，
        部件永远挂不上（见 memory: 私有属性/方法撞名）。
        """
        if self._ready:
            self.mount(widget, before=self._live)
        else:
            # 还没落位（会话不在前台）或组合尚未跑完：先排队，on_mount 统一补
            self._queued_children.append(widget)

    def _tick(self) -> None:
        if self._detached():
            if self._timer is not None:
                self._timer.stop()
            return
        if self._state in ("running", "executing"):
            self._frame += 1
            self._render_state()

    def _detached(self) -> bool:
        """已被清出聊天区（clear_transcript）后停止自绘；组合未完成不算。"""
        return not self.is_attached

    @property
    def finished(self) -> bool:
        """是否已有结束状态；run 结束时的兜底据此只收尾还开着的块。"""
        return self._state in ("done", "failed", "interrupted")

    # -- 事件入口 --

    def call_delta(self, delta: str) -> None:
        self._raw_args = (self._raw_args + delta)[-tools.ARG_BUFFER:]
        # 参数一旦能整体解析就是最终值，不必再随每个片段重复解析
        if not self._args_complete:
            self._args_complete = tools.load_args(self._raw_args) is not None
            self._args = tools.parse_args(self._raw_args)
        self._render_state()

    def call_end(self) -> None:
        self._args = tools.parse_args(self._raw_args)
        self._state = "executing"
        self._render_state()
        self._mount_body()

    def result_delta(self, delta: str) -> None:
        self._result_tail = (self._result_tail + delta)[-4000:]
        self._render_state()
        visible = self._visible_result()
        if not visible:
            return
        self._live.display = True
        self._set_result_line(f"{GLYPH_RESULT} {_one_line(visible, self.RESULT_PREVIEW)}")

    def _visible_result(self) -> str:
        """结果行的文本：整段都是 system-reminder 时按空处理。

        工具被中断时，agentscope 会把「工具调用已被用户打断」这条提醒写进结果文本；
        那是写给模型的运行时上下文，与 ``HintBlock`` 同一政策——不进转录。工具真输出
        里夹带的原文不动：只有整段都是这类标记才忽略。
        """
        raw = self._result_tail.strip()
        return "" if self._REMINDER.fullmatch(raw) else raw

    def result_data(
        self,
        block_id: str,
        media_type: str,
        data: str | None,
        url: str | None,
    ) -> None:
        """二进制结果：url 记一行；内联 base64 攒起来，result.end 统一落地。"""
        if url:
            self._mount_note(f"{media_type}  {url}")
        elif data:
            key = block_id or media_type
            block = self._data_buffers.setdefault(
                key, {"media_type": media_type, "chunks": []}
            )
            block["chunks"].append(data)

    def result_end(self, state: str | None) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        bad = ("error", "failed", "failure", "rejected", "denied", "blocked", "cancelled")
        final = (state or "").lower()
        if final == "interrupted":
            self._state = "interrupted"
        elif final in bad:
            self._state = "failed"
        else:
            self._state = "done"
        self._elapsed = time.monotonic() - self._t0
        self._flush_data_blocks()
        self._mount_result()
        self._render_state()

    def _flush_data_blocks(self) -> None:
        """把攒下的内联数据写进临时文件，附在结果区。"""
        for block in self._data_buffers.values():
            media = str(block["media_type"])
            raw = datablocks.decode_base64("".join(block["chunks"]))
            if raw is None:
                self._mount_note(f"{media}  无法解码")
            else:
                path = datablocks.save_temp(raw, media)
                self._mount_note(
                    f"{media}  {datablocks.human_size(len(raw))} → {path}"
                )
        self._data_buffers.clear()

    def _mount_note(self, note: str) -> None:
        self._mount_inner(
            Static(
                Text(f"{GLYPH_RESULT} {note}", style=S_INTERNAL),
                classes="tool-result",
            )
        )

    # -- 渲染 --

    def _render_state(self) -> None:
        self._render_detail()
        # 只看是否已被清出聊天区；挂载/组合的先后顺序由 query 的兜底吸收
        if self._detached():
            return
        self.border_title = Text(
            f"{tools.icon_of(self.tool_name)} {tools.display_name(self.tool_name)}"
        )

        if self._state == "done":
            self.set_state(f"✓ 完成 · {_fmt_elapsed(self._elapsed or 0)}", "ok")
        elif self._state == "failed":
            self.set_state(f"✗ 失败 · {_fmt_elapsed(self._elapsed or 0)}", "err")
        elif self._state == "interrupted":
            self.set_state(
                f"{GLYPH_INTERRUPT} 已中断 · {_fmt_elapsed(self._elapsed or 0)}",
                "warn",
            )
        elif self._state == "awaiting":
            # 等用户表态：表停在这里，等待的那段时间不算进工具耗时
            self.set_state(f"等待中 · {_fmt_elapsed(self._elapsed or 0)}", "warn")
        else:
            spinner = GLYPH_SPINNER[self._frame % len(GLYPH_SPINNER)]
            if self._state == "executing":
                label = "执行中"
            elif self._args:
                label = "准备中"
            else:
                label = "参数流入中"
            spent = _fmt_elapsed(time.monotonic() - self._t0)
            self.set_state(f"{spinner} {label} · {spent}", "running")

    def _render_detail(self) -> None:
        """框内第一行：这一步在干什么，随参数流入逐步变完整。"""
        line = _one_line(tools.detail_of(self.tool_name, self._args), self.DETAIL_WIDTH)
        if line == self._detail_text:
            return
        self._detail_text = line
        self._detail.display = bool(line)
        # 无样式 Text：字符串会被当 console markup 解析，参数里一个方括号就能吃掉一行
        self._detail.update(Text(line))

    def await_approval(self) -> None:
        """挂起等审批：停表并把状态改成「等待中」。"""
        if self.finished or self._state == "awaiting":
            return
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._state = "awaiting"
        self._elapsed = time.monotonic() - self._t0
        self._paused_at = time.monotonic()
        self._render_state()

    def resume(self) -> None:
        """审批过了接着跑：把等待的那段从计时里摘掉，别记成工具执行时间。"""
        if self._state != "awaiting":
            return
        now = time.monotonic()
        if self._paused_at is not None:
            self._t0 += now - self._paused_at
            self._paused_at = None
        self._elapsed = None
        self._state = "executing"
        if self._timer is None and self.is_attached:
            self._timer = self.set_interval(0.12, self._tick)
        self._render_state()

    def _set_result_line(self, text: str) -> None:
        """结果预览行原地更新：结果流式到达时不该一行一行往聊天区里堆。"""
        self._live.update(Text(text, style=S_INTERNAL))

    def _mount_body(self) -> None:
        """参数到齐后补上正文（diff）；默认折叠，一次调用只挂一份。

        聊天区要能一眼扫过，改动细节收在折叠里，需要时再展开。
        """
        if self._body_done:
            return
        body = diff_fold(self.tool_name, self._args)
        if body is None:
            return
        self._body_done = True
        self._mount_inner(body)

    def _mount_result(self) -> None:
        raw = self._visible_result()
        self._result_tail = ""
        if not raw:
            # 没有结果、或结果只是 runtime 的提醒：预览行不留残字
            self._live.update("")
            return
        if len(" ".join(raw.split())) <= self.RESULT_PREVIEW:
            self._live.display = True
            self._set_result_line(f"{GLYPH_RESULT} {_one_line(raw, self.RESULT_PREVIEW)}")
            return
        # 长输出默认折起：正文只在用户点开后出现，且比标题亮一档
        self._live.display = False
        body = Static(
            Text(raw[-4000:], style=S_INTERNAL_OPEN), classes="tool-result-full"
        )
        self._mount_inner(
            Collapsible(
                body,
                title=tools.result_label(raw),
                collapsed=True,
                classes="result-fold",
            )
        )


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


def diff_fold(name: str, args: dict[str, Any]) -> Collapsible | None:
    """把 diff 正文包成默认折叠的一块；没有正文时返回 None。

    工具调用与审批共用：同一份改动在两处长得一样，且都不占满屏幕。
    """
    detail = tools.body(name, args)
    if detail is None:
        return None
    return Collapsible(
        Static(detail, classes="diff-body"),
        title=tools.body_label(name, args),
        collapsed=True,
        classes="diff-fold",
    )


def _call_parts(tc: dict) -> tuple[str, dict[str, Any]]:
    name = str(
        tc.get("name")
        or tc.get("tool_call_name")
        or tc.get("tool_name")
        or "tool"
    )
    for key in ("input", "arguments", "args"):
        if tc.get(key) is not None:
            return name, tools.coerce_args(tc[key])
    return name, {}


def _fmt_tokens(value: int) -> str:
    """token 数：一千以内原样，往上折成 k（状态栏与右侧栏同一套读法）。"""
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


# 这些提供方的 input_tokens 不含缓存那部分（Anthropic 系把读到与写入分开报）：
# 算占比要先把两块都加回分母，否则分母比真实提示词小，命中率虚高、占用也偏低
_CACHE_OUTSIDE_INPUT = frozenset({"anthropic_credential"})


def prompt_total(
    context_tokens: int,
    cache_tokens: int,
    provider: str,
    created_tokens: int = 0,
) -> int:
    """这次调用真正喂进去的提示词总量（含从缓存读到的与写进缓存的那两部分）。

    底部压力表与右侧栏的缓存命中都要这个量，口径只能有一份。
    """
    if provider in _CACHE_OUTSIDE_INPUT:
        return context_tokens + cache_tokens + created_tokens
    return context_tokens


class StatusBar(Horizontal):
    """底部状态栏：只留一眼要看到的三样——运行态（含审批数）、模型、上下文占用。

    这行只有一格高，随后续开发一定塞不下更多东西，硬挤只会把要紧的三项挤到看不
    见；其余量化信息在右侧栏 ``SidePanel``。ctx 表留在底部，因为它是"还能不能继续
    跑"的即时信号，模型名是"在跟谁说话"的即时信号。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._left = Static("", classes="status-left")
        self._state = "idle"  # idle | working | queued
        self._approvals = 0
        self._model = ""
        self._config_model = ""
        self._context_size = 0
        self._context_tokens = 0
        self._cache_tokens = 0
        self._cache_created = 0
        self._provider = ""
        self._frame = 0
        self._timer = None

    def compose(self):
        yield self._left

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.15, self._tick)
        self._refresh()

    def _tick(self) -> None:
        if self._state == "working":
            self._frame += 1
            self._refresh()

    # -- 对外接口 --

    def set_state(self, state: str) -> None:
        self._state = state
        self._refresh()

    def set_approvals(self, count: int) -> None:
        self._approvals = count
        self._refresh()

    def set_model(self, name: str) -> None:
        self._model = name or ""
        self._refresh()

    def set_usage(
        self, context_tokens: int, cache_tokens: int, cache_created: int = 0
    ) -> None:
        """压力表只要最近一次调用的量；其余计数归右侧栏。"""
        self._context_tokens = context_tokens
        self._cache_tokens = cache_tokens
        self._cache_created = cache_created
        self._refresh()

    def apply_config(self, flat: dict) -> None:
        """这里只取影响压力表的两项：窗口大小与提供方口径。

        字段缺席表示这次回执没说这件事，保留原值。
        """
        if flat.get("model"):
            self._config_model = str(flat["model"])
        if "provider_type" in flat:
            self._provider = str(flat["provider_type"] or "")
        if isinstance(flat.get("context_size"), int):
            self._context_size = flat["context_size"]
        self._refresh()

    # -- 渲染 --

    def _pressure(self) -> tuple[str, str] | None:
        """上下文占用：(文本, 样式)；不知道窗口大小时不给这一段。"""
        total = prompt_total(
            self._context_tokens,
            self._cache_tokens,
            self._provider,
            self._cache_created,
        )
        if not total or not self._context_size:
            return None
        ratio = total / self._context_size
        if ratio < 0.6:
            style = S_OK
        elif ratio < 0.85:
            style = S_WARN
        else:
            style = S_ERR
        filled = max(1, min(_METER_CELLS, round(ratio * _METER_CELLS)))
        bar = GLYPH_METER_FULL * filled + GLYPH_METER_EMPTY * (_METER_CELLS - filled)
        return f"ctx {bar} {ratio * 100:.0f}%", style

    def _segments(self) -> list[tuple[str, str]]:
        """模型名 + 上下文占用，就这两段；其余量化信息在右侧栏。"""
        segments: list[tuple[str, str]] = [
            (self._model or self._config_model or "—", S_TEXT)
        ]
        pressure = self._pressure()
        if pressure is not None:
            segments.append(pressure)
        return segments

    def _refresh(self) -> None:
        left = Text()
        if self._state == "working":
            left.append(
                f"{GLYPH_SPINNER[self._frame % len(GLYPH_SPINNER)]} 运行中",
                style=S_WARN,
            )
        elif self._state == "queued":
            left.append(f"{GLYPH_QUEUED} 排队中", style=S_WARN)
        else:
            left.append(f"{GLYPH_ACTIVE} 就绪", style=S_OK)
        if self._approvals:
            left.append(
                f"  {GLYPH_NOTICE['warn']} 审批×{self._approvals}", style=S_WARN
            )

        # 窄屏时从左往右保留，尾段先丢。宽度不能用左块的 size —— 它由 1fr 算出来，
        # on_resize 时会晚一拍，所以自己从整行扣，末尾留一列不顶到边界。
        available = self.content_size.width - 1
        for index, (text, style) in enumerate(self._segments()):
            if (
                index
                and available > 0
                and left.cell_len + _SEP_WIDTH + cell_len(text) > available
            ):
                if left.cell_len + _TAIL_WIDTH <= available:
                    left.append(f" {GLYPH_ELLIPSIS}", style=S_GHOST)
                break
            left.append(_SEPARATOR, style=S_GHOST)
            left.append(text, style=style)

        self._left.update(left)

    def on_resize(self) -> None:
        self._refresh()


class SidePanel(Vertical):
    """右侧栏：这次会话跑出来的量化信息，一类一个小框。

    底部那一行放不下的都在这里：上下文构成（谁把上下文撑起来的）、token 计数与
    吞吐、缓存命中、思考与权限档位、工作目录、会话号。

    三类各占一个带边框的小框，框标题写类别名，框内只放数值——这样"这是一组"
    不靠空行去暗示。配置那一框钉在底部：它高度固定、内容不随运行变化，而上面
    两框会随分段数长高，钉住才不会在窗口不够高时被挤出可视区（转录区那边
    长出来的内容不该决定这里看不看得见档位）。上面两框放进可滚动容器兜底。
    """

    # 上下文构成的展示顺序与中文名；key 由后端 model.end 的 context 段给出
    _CONTEXT_LABELS = {
        "prompt": "提示词",
        "skills": "技能",
        "tools": "工具定义",
        "messages": "对话",
        "tool_calls": "工具调用",
        "summary": "摘要",
    }
    # 三列：标签（左对齐）/ 数值（右对齐）/ 占比（右对齐）。数值右对齐才看得出谁在涨
    _LABEL_WIDTH = 10
    _VALUE_WIDTH = 11
    _NOTE_WIDTH = 4

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._context_box = Static("", classes="side-box", id="side-context")
        self._metric_box = Static("", classes="side-box", id="side-metric")
        self._config_box = Static("", classes="side-box", id="side-config")
        self._tokens_in = 0
        self._tokens_out = 0
        self._context_tokens = 0
        self._cache_tokens = 0
        self._cache_created = 0
        self._gen_seconds = 0.0
        self._context_size = 0
        self._provider = ""
        self._thinking: str | None = None  # None = 还没读到配置
        self._permission: str | None = None
        self._root = ""
        self._conversation = ""
        self._usage: dict | None = None

    def compose(self):
        yield VerticalScroll(
            self._context_box, self._metric_box, id="side-scroll"
        )
        yield self._config_box

    # -- 对外接口 --

    def set_usage(
        self,
        tokens_in: int,
        tokens_out: int,
        context_tokens: int,
        cache_tokens: int,
        cache_created: int = 0,
        gen_seconds: float = 0.0,
    ) -> None:
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._context_tokens = context_tokens
        self._cache_tokens = cache_tokens
        self._cache_created = cache_created
        self._gen_seconds = gen_seconds
        self._refresh()

    def set_context(self, usage: dict | None) -> None:
        """上下文构成：后端随 model.end 估好的分段量；没有最近一次调用就没有这一段。"""
        if usage == self._usage:
            return
        self._usage = usage
        self._refresh()

    def set_conversation(self, cid: str) -> None:
        self._conversation = cid
        self._refresh()

    def apply_config(self, flat: dict) -> None:
        """配置里的运行档位与工作区；字段缺席保留原值，空串表示后端确实没设。"""
        if "thinking_level" in flat:
            self._thinking = str(flat["thinking_level"] or "")
        if "mode" in flat:
            self._permission = str(flat["mode"] or "")
        if "provider_type" in flat:
            self._provider = str(flat["provider_type"] or "")
        if "root" in flat:
            self._root = str(flat["root"] or "")
        if isinstance(flat.get("context_size"), int):
            self._context_size = flat["context_size"]
        self._refresh()

    # -- 渲染 --

    def _row(self, label: str, value: str, note: str = "") -> Text:
        """一行「标签 …… 数值 …… 占比」：数值右对齐，两列之间留够空隙。

        左边距交给小框自己的 padding，行内不再补空格；中文标签占两列，按显示
        宽度补齐才对得上数值列。
        """
        pad = " " * max(1, self._LABEL_WIDTH - cell_len(label))
        out = Text(f"{label}{pad}", style=S_FAINT)
        out.append(f"{value:>{self._VALUE_WIDTH}}", style=S_TEXT)
        if note:
            out.append(f" {note:>{self._NOTE_WIDTH}}", style=S_FAINT)
        return out

    def _context_section(self) -> tuple[str, list[Text]]:
        """上下文构成：百分比对着「这次上下文的总量」算——看的是谁把它撑起来的。

        总量写在框标题上而不单占一行：窗口不够高时这一栏要能整个露出来，少一行
        就少一行滚动。

        分段是后端用本地计数器（字节 ÷ 4）估的，窗口占用却是 provider 实报的
        ``input_tokens``；两者不该各说各话，所以估算只用来定「谁占几成」，量级
        一律按这个比例摊到实报总量上——框里的数与底部压力表因此始终同源。
        """
        if not self._usage:
            return "上下文", []
        items = [
            (
                self._CONTEXT_LABELS.get(str(item.get("key")), str(item.get("key"))),
                int(item.get("tokens") or 0),
            )
            for item in self._usage.get("segments") or []
        ]
        estimated = int(self._usage.get("total") or 0) or sum(
            tokens for _, tokens in items
        )
        if not items or estimated <= 0:
            return "上下文", []
        # 新一轮开始时用量归零而构成还在（上一次调用的），此时回落到估算值
        total = prompt_total(
            self._context_tokens,
            self._cache_tokens,
            self._provider,
            self._cache_created,
        ) or estimated
        scale = total / estimated
        title = f"上下文 {_fmt_tokens(total)}"
        if self._context_size:
            title += f"/{_fmt_tokens(self._context_size)}"
        rows = [
            self._row(
                label,
                _fmt_tokens(int(tokens * scale + 0.5)),
                f"{tokens / estimated * 100:.0f}%",
            )
            for label, tokens in items
        ]
        return title, rows

    def _metric_section(self) -> tuple[str, list[Text]]:
        rows = []
        if self._tokens_in or self._tokens_out:
            rows.append(
                self._row(
                    "输入输出",
                    f"↑{_fmt_tokens(self._tokens_in)} ↓{_fmt_tokens(self._tokens_out)}",
                )
            )
        if self._gen_seconds and self._tokens_out:
            # 吞吐取整场累计：单次调用的量抖得厉害，看不出趋势
            rows.append(
                self._row("吞吐", f"{self._tokens_out / self._gen_seconds:.0f} tok/s")
            )
        hit = self._cache_hit()
        if hit:
            rows.append(self._row("缓存命中", hit))
        return "用量", rows

    def _cache_hit(self) -> str:
        """缓存命中率：这次调用从提示缓存读到的量 ÷ 提示词总量。

        取最近一次调用而不是整场累计：累计值把每轮重复发送的前缀一直摊进分母，
        越跑越低，看不出此刻的命中情况。
        """
        total = prompt_total(
            self._context_tokens,
            self._cache_tokens,
            self._provider,
            self._cache_created,
        )
        if not total or not self._cache_tokens:
            return ""
        return f"{self._cache_tokens / total * 100:.0f}%"

    def _config_section(self) -> tuple[str, list[Text]]:
        rows = [
            # 没读到配置时按引擎默认值显示，不留一串看不懂的破折号
            self._row("思考", self._thinking or "off"),
            self._row("权限", self._permission or "default"),
        ]
        root = _tail_path(self._root)
        if root:
            rows.append(self._row("目录", root))
        if self._conversation:
            rows.append(self._row("会话", self._conversation))
        return "配置", rows

    def _fill(self, box: Static, title: str, rows: list[Text]) -> None:
        """一个小框：类别名写在边框上，没有内容就连框一起收起来。"""
        box.display = bool(rows)
        box.border_title = title
        body = Text()
        for index, row in enumerate(rows):
            if index:
                body.append("\n")
            body.append(row)
        box.update(body)

    def _refresh(self) -> None:
        for box, section in (
            (self._context_box, self._context_section),
            (self._metric_box, self._metric_section),
            (self._config_box, self._config_section),
        ):
            self._fill(box, *section())


class InputArea(TextArea):
    """底部输入框：Enter 发送，Tab 补全/插入；面板可见时 ↑/↓ 导航。

    正文里的 ``@引用`` 会额外套一层底色（``S_REFERENCE``），让"我引用了哪些文件"在
    一行字里看得出来。实现只能落在 ``render_line`` 上：``TextArea`` 的着色入口是
    私有 highlighter，而它只认 tree-sitter 的语法规则，不是我们这种按 token 标。
    """

    class Submitted(Message):
        def __init__(self, area: "InputArea", text: str) -> None:
            super().__init__()
            self.area = area
            self.text = text

    class TabPressed(Message):
        pass

    class PaletteNavigate(Message):
        def __init__(self, direction: str) -> None:
            super().__init__()
            self.direction = direction

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    _REFERENCE_STYLE = Style.parse(S_REFERENCE)

    @property
    def palette_active(self) -> bool:
        """光标前有活着的触发词（``/`` 命令或 ``@`` 提及）时，↑↓ 归面板导航。"""
        text = self.text
        offset = mentions.cursor_offset(text, self.cursor_location)
        return mentions.triggered(text, offset) is not None

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        row = self.scroll_offset.y + y
        if row >= self.document.line_count:
            return strip
        line = self.document[row]
        spans: list[tuple[int, int]] = []
        for start, end in mentions.reference_spans(line):
            spans.extend(self._cells(line, start, end, row))
        if not spans:
            return strip
        return self._paint(strip, spans)

    def _cells(self, line: str, start: int, end: int, row: int) -> list[tuple[int, int]]:
        """字符区间 → 屏幕列区间（中文占两格、横向滚动要减掉）。

        光标正落在引用里时把它那一格切出去：底色盖住光标就看不见光标了。
        """
        scroll = self.scroll_offset.x
        left = max(0, cell_len(line[:start]) - scroll)
        right = cell_len(line[:end]) - scroll
        if row == self.cursor_location[0]:
            cursor = cell_len(line[: self.cursor_location[1]]) - scroll
            if left <= cursor < right:
                return [span for span in ((left, cursor), (cursor + 1, right)) if span[0] < span[1]]
        return [(left, right)] if left < right else []

    def _paint(self, strip: Strip, spans: list[tuple[int, int]]) -> Strip:
        """按引用边界把这一行切段，落在引用里的段换样式再拼回去。"""
        width = strip.cell_length
        cuts = sorted({cut for span in spans for cut in span if 0 < cut < width})
        cuts = [*cuts, width]
        pieces = list(strip.divide(cuts))
        painted: list[Strip] = []
        for index, piece in enumerate(pieces):
            start = cuts[index - 1] if index else 0
            if any(left <= start < right for left, right in spans):
                piece = self._chip(piece)
            painted.append(piece)
        return Strip.join(painted)

    def _chip(self, strip: Strip) -> Strip:
        """给一段套上引用样式。

        不能用 ``Strip.apply_style``：它是"传入样式在前、段自带样式在后"，而输入框的
        段自带底色（``$surface``）——引用那层底会被它盖掉，只剩加粗。这里在段级别把
        引用样式叠在**后**面。
        """
        segments = [
            Segment(
                segment.text,
                segment.style + self._REFERENCE_STYLE
                if segment.style
                else self._REFERENCE_STYLE,
                segment.control,
            )
            for segment in strip
        ]
        return Strip(segments, strip.cell_length)

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                # 不在这里清空：清空会立刻收起命令面板，而 _on_submit 还要读
                # 面板当前选中的命令，时序反过来 Enter 就等于没按面板走
                self.post_message(self.Submitted(self, text))
            return
        if key == "tab":
            event.stop()
            event.prevent_default()
            self.post_message(self.TabPressed())
            return
        if key in ("up", "down") and self.palette_active:
            event.stop()
            event.prevent_default()
            self.post_message(self.PaletteNavigate(key))
            return
        await super()._on_key(event)

    def set_text(self, text: str) -> None:
        self.clear()
        self.insert(text, location=(0, 0))


class InlinePalette(OptionList):
    """输入框上方的内联候选列表：``/`` 给命令，``@`` 给工作区文件与 skill。

    两种模式共用一套渲染，只是行文本与「选中后干什么」不同：命令是执行，提及是把
    ``@标签`` 插进正文。模式名交给 app 解释选中项（命令 → ``current_command``，
    提及 → ``current_mention``）。

    键盘焦点始终留在输入框（↑↓ 由输入框转成导航消息），所以高亮只能靠边框提示与行
    底色表达，面板自己不接受键盘——点一下也能选中（见 app 的 OptionSelected）。
    """

    HINT_COMMAND = "↑↓ 选择 · Enter 执行 · Tab 补全 · Esc 收起"
    HINT_MENTION = "↑↓ 选择 · Enter 进目录/引用 · Esc 收起"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mode = ""
        self._shown: list[Any] = []
        self.display = False
        self._header("命令", self.HINT_COMMAND)

    # -- 填充 --

    def show_commands(self, cmds: list[SlashCommand]) -> None:
        self.mode = "command"
        self._header("命令", self.HINT_COMMAND)
        self._fill(
            [Option(self._command_row(cmd), id=cmd.name) for cmd in cmds],
            cmds,
            "没有匹配的命令",
        )

    def show_mentions(self, items: list[mentions.Mention]) -> None:
        self.mode = "mention"
        self._header("提及", self.HINT_MENTION)
        self._fill(
            [
                Option(self._mention_row(item), id=str(index))
                for index, item in enumerate(items)
            ],
            items,
            "没有匹配的文件或 skill",
        )

    def _fill(self, options: list[Option], shown: list[Any], empty: str) -> None:
        """候选为空时留一行说明：什么都不显示会让人以为按键没生效。"""
        self.clear_options()
        self._shown = list(shown)
        for option in options:
            self.add_option(option)
        if not options:
            self.add_option(Option(Text(empty, style=S_FAINT), disabled=True))
        self.display = True
        self.highlighted = 0

    def hide(self) -> None:
        self.display = False

    def move_highlight(self, direction: str) -> None:
        count = self.option_count
        if not count:
            return
        current = self.highlighted if self.highlighted is not None else 0
        step = -1 if direction == "up" else 1
        self.highlighted = (current + step) % count

    # -- 当前项 --

    @property
    def current(self) -> Any:
        """高亮行的负载（命令或提及）；落在说明行上时为 None。"""
        index = self.highlighted
        if index is not None and 0 <= index < len(self._shown):
            return self._shown[index]
        return None

    @property
    def current_command(self) -> SlashCommand | None:
        item = self.current
        return item if isinstance(item, SlashCommand) else None

    @property
    def current_mention(self) -> mentions.Mention | None:
        item = self.current
        return item if isinstance(item, mentions.Mention) else None

    # -- 行渲染 --

    def _header(self, title: str, hint: str) -> None:
        self.border_title = Text(f"{GLYPH_PANEL} {title}")
        self.border_subtitle = hint

    @staticmethod
    def _command_row(cmd: SlashCommand) -> Text:
        row = Text()
        row.append(f"/{cmd.name}", style=S_TEXT)
        if cmd.aliases:
            row.append(f" (/{' /'.join(cmd.aliases)})", style=S_GHOST)
        row.append(f"  {cmd.description}", style=S_DIM)
        return row

    @staticmethod
    def _mention_row(item: mentions.Mention) -> Text:
        row = Text()
        # 目录行尾带斜杠：一眼分清"能钻进去的"和"能引用的"
        row.append(f"@{item.token}", style=S_TEXT)
        if item.note:
            row.append(f"  {item.note}", style=S_FAINT)
        return row
