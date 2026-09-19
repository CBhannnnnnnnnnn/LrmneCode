"""聊天区的显示部件：协议事件 → 专属渲染。

设计参考 claude code / opencode 的信息密度与配色：

- 用户输入      ``❯ text``（高亮）
- 助手回复      Markdown 流式块
- 思考流        ✻ 折叠块：展开淡色正文，结束后折叠为一行预览
- 系统提示      ℹ 折叠块：默认收起（如 runtime 注入的 system-reminder）
- 工具调用      ⏺ 工具(参数…) + ⎿ 结果预览；长结果折叠可展开
- 审批请求      独立面板：工具列表 + y/n 快捷键 + 按钮
- 卡片          圆角边框 + 内嵌标题（配置/帮助/状态）
- 状态栏        运行状态 / 模型 / 思考级别 / 权限模式 / token 计数
"""

from __future__ import annotations

import json
from typing import Any

from rich.text import Text
from textual import events
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import (
    Button,
    Collapsible,
    Markdown,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from .commands import SlashCommand

# ---- 色板（Tokyo Night 系） ----
COLOR_USER = "#7aa2f7"
COLOR_DIM = "#565f89"
COLOR_OK = "#9ece6a"
COLOR_ERR = "#f7768e"
COLOR_WARN = "#e0af68"
COLOR_ACCENT = "#bb9af7"
COLOR_TOOL = "#7dcfff"
COLOR_TEXT = "#c0caf5"

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class StreamMarkdown(Markdown):
    """带就绪标记的 Markdown：挂载完成前 update 会被 on_mount 清空。"""

    ready = False

    def on_mount(self) -> None:
        self.ready = True


def _one_line(text: str, limit: int) -> str:
    """压成单行并截断。"""
    line = " ".join(text.split())
    if len(line) > limit:
        return line[:limit] + "…"
    return line


class UserMessage(Static):
    """用户输入回显：``❯ text``；斜杠命令用警示色区分。"""

    def __init__(self, text: str, slash: bool = False, **kwargs: Any) -> None:
        super().__init__(classes="user-message", **kwargs)
        rendered = Text()
        rendered.append("❯ ", style=f"bold {COLOR_WARN if slash else COLOR_USER}")
        rendered.append(text, style="bold" if not slash else f"bold {COLOR_WARN}")
        self.update(rendered)


class NoticeLine(Static):
    """系统提示行：图标 + 文案。kind: info|success|error|warn|busy。"""

    ICONS = {
        "info": ("ℹ", COLOR_DIM),
        "success": ("✔", COLOR_OK),
        "error": ("✘", COLOR_ERR),
        "warn": ("⚠", COLOR_WARN),
        "busy": ("⏺", COLOR_ACCENT),
    }

    def __init__(self, text: str, kind: str = "info", **kwargs: Any) -> None:
        super().__init__(classes=f"notice notice-{kind}", **kwargs)
        icon, color = self.ICONS.get(kind, self.ICONS["info"])
        self.update(Text(f"{icon} {text}", style=color))


class Card(Static):
    """圆角边框 + 内嵌标题的信息卡片（欢迎 / 帮助 / 配置 / 状态）。"""

    def __init__(self, title: str, body: Text, **kwargs: Any) -> None:
        super().__init__(body, classes="card", **kwargs)
        self.border_title = f"◆ {title}"


class ThinkingBlock(Collapsible):
    """思考流：展开的淡色正文（实时字数），结束后折叠为一行预览。"""

    def __init__(self, block_id: str, **kwargs: Any) -> None:
        self.block_id = block_id
        self._buffer = ""
        self._body = Static("", classes="thinking-body")
        super().__init__(self._body, title="✻ 思考中", collapsed=False, **kwargs)
        self.add_class("thinking")

    def append_delta(self, delta: str) -> None:
        self._buffer += delta
        self._body.update(Text(self._buffer, style=f"italic {COLOR_DIM}"))
        self.title = f"✻ 思考中 · {len(self._buffer)} 字"

    def finish(self) -> None:
        preview = _one_line(self._buffer, 36)
        self.title = f"✻ 已思考 · {preview}" if preview else "✻ 已思考"
        self.collapsed = True


class HintBlock(Collapsible):
    """系统提示（如 runtime 注入的 system-reminder）：默认折叠为一行标记。"""

    def __init__(self, source: Any, body_text: str, **kwargs: Any) -> None:
        self._body = Static(body_text, classes="hint-body")
        super().__init__(
            self._body, title=f"ℹ {self._label(source)}", collapsed=True, **kwargs
        )
        self.add_class("hint")

    @staticmethod
    def _label(source: Any) -> str:
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


class ToolCallView(Vertical):
    """工具调用视图：状态行 ``⏺ 工具(参数…)`` + 结果区 ``⎿ …``。

    状态流转：运行中（旋转符号）→ 完成（绿 ⏺）/ 失败（红 ⏺）。
    结果超长时折叠，展开可看全文。
    """

    def __init__(self, tool_call_id: str, name: str, **kwargs: Any) -> None:
        super().__init__(classes="tool-call", **kwargs)
        self.tool_call_id = tool_call_id
        self.tool_name = name
        self._args_tail = ""
        self._result_tail = ""
        self._state = "running"  # running → executing → done / failed
        self._frame = 0
        self._timer = None
        # 事件可能先于异步挂载到达：未就绪时只更新缓存，on_mount 统一补渲染
        self._mounted = False
        self._pending_result: Any = None

    def compose(self):
        yield Static("", classes="tool-status")

    def on_mount(self) -> None:
        self._mounted = True
        self._timer = self.set_interval(0.12, self._tick)
        self._render_status()
        if self._pending_result is not None:
            self.mount(self._pending_result)
            self._pending_result = None

    def _tick(self) -> None:
        if self._state in ("running", "executing"):
            self._frame += 1
            self._render_status()

    def _detached(self) -> bool:
        """已被清出聊天区（clear_transcript）后停止自绘。"""
        try:
            self.query_one(".tool-status", Static)
            return False
        except Exception:
            if self._timer is not None:
                self._timer.stop()
            return True

    # -- 事件入口 --

    def call_delta(self, delta: str) -> None:
        self._args_tail = (self._args_tail + delta)[-200:]
        self._render_status()

    def call_end(self) -> None:
        self._state = "executing"
        self._render_status()

    def result_delta(self, delta: str) -> None:
        self._result_tail = (self._result_tail + delta)[-4000:]
        self._render_status()

    def result_data(self, media_type: str, url: str | None) -> None:
        note = f"[{media_type}]" + (f" {url}" if url else "")
        self._result_tail = (self._result_tail + " " + note).strip()[-4000:]
        self._render_status()

    def result_end(self, state: str | None) -> None:
        if self._timer is not None:
            self._timer.stop()
        bad = ("error", "failed", "failure", "rejected", "blocked", "cancelled")
        self._state = (
            "failed" if (state or "").lower() in bad else "done"
        )
        self._render_status()
        self._mount_result()

    # -- 渲染 --

    def _render_status(self) -> None:
        if not self._mounted or self._detached():
            return
        text = Text()
        running = self._state in ("running", "executing")
        if running:
            text.append(
                SPINNER[self._frame % len(SPINNER)] + " ", style=COLOR_WARN
            )
        else:
            color = COLOR_OK if self._state == "done" else COLOR_ERR
            text.append("⏺ ", style=color)
        text.append(self.tool_name, style="bold")

        args = " ".join(self._args_tail.split())
        if args:
            text.append(f"({_one_line(args, 100)})", style=COLOR_DIM)

        # 结果未折叠时实时预览在下一行
        if running and self._result_tail.strip():
            text.append(f"\n  ⎿ {_one_line(self._result_tail, 120)}", style=COLOR_DIM)

        status = self.query_one(".tool-status", Static)
        status.update(text)

    def _mount_result(self) -> None:
        raw = self._result_tail.strip()
        if not raw:
            return
        preview = _one_line(raw, 120)
        if len(" ".join(raw.split())) > 160:
            body = Static(
                Text(raw[-4000:], style=COLOR_DIM), classes="tool-result-full"
            )
            result: Any = Collapsible(
                body, title=f"⎿ {preview}", collapsed=True
            )
        else:
            result = Static(
                Text(f"⎿ {preview}", style=COLOR_DIM), classes="tool-result"
            )
        self._result_tail = ""
        if not self._mounted:
            self._pending_result = result
            return
        self.mount(result)


class ApprovalPanel(Vertical):
    """审批请求面板：聚焦后按 y / n，或点击按钮。"""

    class Decision(Message):
        """用户对某个审批面板做出决定。"""

        def __init__(self, panel: "ApprovalPanel", approved: bool) -> None:
            super().__init__()
            self.panel = panel
            self.approved = approved

    can_focus = True
    BINDINGS = [
        ("y", "approve", "允许"),
        ("n", "deny", "拒绝"),
    ]

    def __init__(
        self, approval_request_id: str, tool_calls: list[dict], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.approval_request_id = approval_request_id
        self._tool_calls = tool_calls
        self.resolved = False
        self.border_title = "🔒 工具执行审批"

    def compose(self):
        for tc in self._tool_calls:
            yield Static(self._describe_call(tc), classes="approval-item")
        yield Horizontal(
            Button("允许 (y)", variant="success", id="btn-approve"),
            Button("拒绝 (n)", variant="error", id="btn-deny"),
            classes="approval-buttons",
        )

    @staticmethod
    def _describe_call(tc: dict) -> Text:
        name = (
            tc.get("name")
            or tc.get("tool_call_name")
            or tc.get("tool_name")
            or "tool"
        )
        raw_args = tc.get("input")
        if raw_args is None:
            raw_args = tc.get("arguments", tc.get("args", {}))
        try:
            args_text = json.dumps(raw_args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_text = str(raw_args)
        text = Text()
        text.append("⏺ ", style=COLOR_WARN)
        text.append(str(name), style="bold")
        if args_text and args_text != "{}":
            text.append(f"({_one_line(args_text, 160)})", style=COLOR_DIM)
        return text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Decision(self, event.button.id == "btn-approve"))

    def action_approve(self) -> None:
        self.post_message(self.Decision(self, True))

    def action_deny(self) -> None:
        self.post_message(self.Decision(self, False))

    def mark_resolved(self, approved: bool) -> None:
        self.resolved = True
        self.add_class("resolved")
        self.border_title = "🔒 已处理"
        for button in self.query(Button):
            button.disabled = True
        note = (
            Text("⏺ 已允许，等待工具执行…", style=COLOR_OK)
            if approved
            else Text("⏺ 已拒绝。", style=COLOR_ERR)
        )
        self.mount(Static(note, classes="approval-item"))


class StatusBar(Horizontal):
    """底部状态栏：左 = 运行状态 + 关键配置，右 = 会话 id。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._left = Static("", classes="status-left")
        self._right = Static("", classes="status-right")
        self._state = "idle"  # idle | working | queued
        self._approvals = 0
        self._model = "—"
        self._thinking = "—"
        self._permission = "—"
        self._tokens_in = 0
        self._tokens_out = 0
        self._conversation = ""
        self._frame = 0
        self._timer = None

    def compose(self):
        yield self._left
        yield self._right

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
        if name:
            self._model = name
            self._refresh()

    def set_conversation(self, cid: str) -> None:
        self._conversation = cid
        self._refresh()

    def add_tokens(self, tin: int, tout: int) -> None:
        self._tokens_in += tin
        self._tokens_out += tout
        self._refresh()

    def apply_config(self, flat: dict) -> None:
        """从 config.get/set 的扁平结果中提取状态栏字段。"""
        if flat.get("model"):
            self._model = str(flat["model"])
        if flat.get("thinking_level"):
            self._thinking = str(flat["thinking_level"])
        if flat.get("mode"):
            self._permission = str(flat["mode"])
        self._refresh()

    def reset_tokens(self) -> None:
        self._tokens_in = 0
        self._tokens_out = 0
        self._refresh()

    # -- 渲染 --

    @staticmethod
    def _fmt_tokens(value: int) -> str:
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)

    def _refresh(self) -> None:
        left = Text()
        if self._state == "working":
            left.append(
                f"{SPINNER[self._frame % len(SPINNER)]} 运行中", style=COLOR_ACCENT
            )
        elif self._state == "queued":
            left.append("⏸ 排队中", style=COLOR_WARN)
        else:
            left.append("● 就绪", style=COLOR_OK)
        if self._approvals:
            left.append(f"  ⚠ 审批×{self._approvals}", style=COLOR_WARN)
        left.append(f"  {self._model}", style=COLOR_TEXT)
        left.append(f" │ think {self._thinking}", style=COLOR_DIM)
        left.append(f" │ perm {self._permission}", style=COLOR_DIM)
        if self._tokens_in or self._tokens_out:
            left.append(
                f" │ ↑{self._fmt_tokens(self._tokens_in)}"
                f" ↓{self._fmt_tokens(self._tokens_out)}",
                style=COLOR_DIM,
            )
        self._left.update(left)
        self._right.update(Text(self._conversation, style=COLOR_DIM))


class InputArea(TextArea):
    """底部输入框：Enter 发送，Tab 补全；面板可见时 ↑/↓ 导航。"""

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

    @property
    def palette_active(self) -> bool:
        """仅在输入命令首词（无空格）时视为面板激活。"""
        text = self.text
        return text.startswith("/") and " " not in text

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                self.clear()
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


class CommandPalette(OptionList):
    """斜杠命令补全面板：随输入前缀过滤，Enter 执行 / Tab 补全。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shown: list[SlashCommand] = []
        self.display = False

    def show_choices(self, cmds: list[SlashCommand]) -> None:
        self.clear_options()
        self._shown = cmds
        for cmd in cmds:
            prompt = Text()
            prompt.append(f"/{cmd.name}", style=f"bold {COLOR_TOOL}")
            if cmd.usage:
                prompt.append(f" {cmd.usage}", style=COLOR_ACCENT)
            if cmd.aliases:
                prompt.append(f" (/{' /'.join(cmd.aliases)})", style=COLOR_DIM)
            prompt.append(f"  {cmd.description}", style=COLOR_DIM)
            self.add_option(Option(prompt, id=cmd.name))
        self.display = bool(cmds)
        if cmds:
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

    @property
    def current_command(self) -> SlashCommand | None:
        index = self.highlighted
        if index is not None and 0 <= index < len(self._shown):
            return self._shown[index]
        return None
