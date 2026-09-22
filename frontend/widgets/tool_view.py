"""工具调用块：命令行摘要、彩色 diff 折叠、结果行与状态流转。

由 ``frontend/widgets.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

import re
import time
from typing import Any
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import (Collapsible, Static)
from .. import (datablocks)
from .. import (tools)
from ..theme import (GLYPH_INTERRUPT, GLYPH_RESULT, GLYPH_SPINNER, S_INTERNAL, S_INTERNAL_OPEN)
from .base import Block, _fmt_elapsed, _one_line
from .transcript import HintBlock




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
