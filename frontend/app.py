"""CodeAgent TUI 主应用。

职责：
- 组装界面（聊天流 / 命令面板 / 状态栏 / 输入框）
- 把后端事件渲染成聊天流（文本 / 思考 / 工具 / 审批）
- 把用户输入（普通文本 或 /命令）映射为协议命令
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header

from proxy_layer.schema import (
    PROTOCOL_VERSION,
    EventProtocol,
    ReceiptProtocol,
    make_command,
    new_conversation_id,
    new_request_id,
)

from .client import BackendClient
from .commands import OP_CHAT_SEND, find, match_prefix, parse
from .widgets import (
    COLOR_ACCENT,
    COLOR_DIM,
    COLOR_ERR,
    COLOR_OK,
    COLOR_TOOL,
    COLOR_USER,
    COLOR_WARN,
    ApprovalPanel,
    Card,
    CommandPalette,
    HintBlock,
    InputArea,
    NoticeLine,
    StatusBar,
    StreamMarkdown,
    ThinkingBlock,
    ToolCallView,
    UserMessage,
)


@dataclass
class PendingCommand:
    """已发出、等待回执的命令。"""

    request_id: str
    operation: str
    label: str
    kind: str  # chat | config | session | approval | diff | undo | other


@dataclass
class _TextBlock:
    """流式文本块：Markdown 组件 + 待刷新缓冲。"""

    key: str
    markdown: Markdown
    buffer: str = ""
    scheduled: bool = False


class CodeAgentApp(App):
    TITLE = "CodeAgent"
    SUB_TITLE = "agent tui"

    BINDINGS = [
        Binding("escape", "escape", "中断/关闭", priority=True),
        Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    CSS = """
    Screen {
        background: #16161e;
        color: #c0caf5;
    }
    Header { background: #16161e; }
    Footer { background: #1f2335; }
    #chat {
        height: 1fr;
        padding: 0 1;
        scrollbar-background: #16161e;
        scrollbar-color: #2a2e42;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
    }
    .user-message { margin: 1 0 0 0; }
    .notice { margin: 0; }
    .assistant-text { margin: 0; }
    .thinking, .hint { margin: 0; }
    .thinking CollapsibleTitle, .hint CollapsibleTitle {
        color: #565f89;
    }
    .thinking-body, .hint-body {
        color: #565f89;
        text-style: italic;
    }
    .tool-call {
        height: auto;
        margin: 0;
    }
    .tool-status { margin: 0; }
    .tool-result, .tool-result-full {
        color: #565f89;
        margin: 0;
    }
    .card {
        border: round #2a2e42;
        background: #1a1b26;
        padding: 0 1;
        margin: 1 0;
        border-title-color: #bb9af7;
        border-title-align: left;
    }
    .approval-panel {
        height: auto;
        border: round #e0af68;
        background: #1f2335;
        padding: 0 1;
        margin: 1 0;
        border-title-color: #e0af68;
        border-title-align: left;
    }
    .approval-panel.resolved {
        border: round #2a2e42;
        opacity: 0.55;
    }
    .approval-title { margin: 0; }
    .approval-item { margin: 0; }
    .approval-buttons {
        height: auto;
        margin: 1 0 0 0;
    }
    .approval-buttons Button { margin-right: 1; min-width: 12; }
    #palette {
        height: auto;
        max-height: 12;
        margin: 0 1;
        border: round #2a2e42;
        background: #1a1b26;
        scrollbar-size-vertical: 1;
    }
    #status { height: 1; background: #1f2335; padding: 0 1; }
    .status-left { width: 1fr; color: #9aa5ce; }
    .status-right { width: auto; color: #565f89; }
    #input {
        height: 5;
        border: round #2a2e42;
        background: #16161e;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    #input:focus { border: round #7aa2f7; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.theme = "textual-dark"
        self.conversation_id = new_conversation_id()
        self.client = BackendClient(
            on_event=self._dispatch_event,
            on_receipt=self._dispatch_receipt,
            on_exit=self._dispatch_exit,
        )
        # 回执等待表 & 在途 chat 命令
        self._pending: dict[str, PendingCommand] = {}
        self._chat_inflight: set[str] = set()
        # 渲染状态（按事件键索引）
        self._text_blocks: dict[str, _TextBlock] = {}
        self._thinking: dict[str, ThinkingBlock] = {}
        self._tools: dict[str, ToolCallView] = {}
        self._approvals: dict[str, ApprovalPanel] = {}
        self._data_blocks: dict[str, dict] = {}  # block_id → {media_type, buffer}
        self._pending_attachments: list[dict] = []  # 待发送附件
        self._cached_skills: list[dict] = []  # skill.list 缓存，供 /use-skill 编号查找
        self._cached_mcp: list[dict] = []  # mcp.list 缓存
        # 缓存的组件引用（on_mount 填充）
        self._chat: VerticalScroll | None = None
        self._palette: CommandPalette | None = None
        self._status: StatusBar | None = None
        self._input: InputArea | None = None

    # ---------- 组件快捷访问 ----------

    @property
    def chat_log(self) -> VerticalScroll:
        assert self._chat is not None
        return self._chat

    @property
    def palette(self) -> CommandPalette:
        assert self._palette is not None
        return self._palette

    @property
    def status_bar(self) -> StatusBar:
        assert self._status is not None
        return self._status

    @property
    def input_area(self) -> InputArea:
        assert self._input is not None
        return self._input

    # ---------- 组装 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="chat")
        yield CommandPalette(id="palette")
        yield StatusBar(id="status")
        yield InputArea(
            id="input",
            placeholder="发送消息，/ 呼出命令，Esc 中断",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._chat = self.query_one("#chat", VerticalScroll)
        self._palette = self.query_one("#palette", CommandPalette)
        self._status = self.query_one("#status", StatusBar)
        self._input = self.query_one("#input", InputArea)

        self.status_bar.set_conversation(self.conversation_id)
        self.input_area.focus()

        body = Text()
        body.append(
            f"protocol {PROTOCOL_VERSION} · conversation {self.conversation_id}\n",
            style=COLOR_DIM,
        )
        body.append("❯ ", style=f"bold {COLOR_USER}")
        body.append("输入消息开始对话\n", style="#c0caf5")
        body.append("/ ", style=f"bold {COLOR_TOOL}")
        body.append("呼出命令    ", style=COLOR_DIM)
        body.append("Esc ", style=f"bold {COLOR_WARN}")
        body.append("中断    ", style=COLOR_DIM)
        body.append("Ctrl+Q ", style="bold #565f89")
        body.append("退出", style=COLOR_DIM)
        self._mount_chat(Card("CodeAgent", body))

        self.run_worker(self.client.start(), exclusive=False)
        # 启动即拉取配置，填充状态栏
        self.send_command("config.get", {}, kind="config", label="config.get")

    async def _on_exit_app(self) -> None:
        await self.client.stop()

    # ---------- 对外动作（commands.py 调用） ----------

    def send_command(
        self,
        operation: str,
        parameters: dict[str, Any],
        *,
        kind: str = "other",
        label: str = "",
    ) -> str:
        """发送一条协议命令并登记回执等待。"""
        request_id = new_request_id()
        command = make_command(request_id, self.conversation_id, operation, parameters)
        self._pending[request_id] = PendingCommand(
            request_id, operation, label or operation, kind
        )
        if kind == "chat":
            self._chat_inflight.add(request_id)
            self.status_bar.set_state("working")
        try:
            self.client.send(command)
        except RuntimeError as exc:
            self._pending.pop(request_id, None)
            self._chat_inflight.discard(request_id)
            self.show_notice(f"发送失败：{exc}", "error")
            self.status_bar.set_state("idle")
        return request_id

    def show_notice(self, text: str, kind: str = "info") -> None:
        self._mount_chat(NoticeLine(text, kind))

    def show_command_help(self) -> None:
        from .commands import COMMANDS

        body = Text()
        for cmd in COMMANDS:
            body.append(f"/{cmd.name}", style=f"bold {COLOR_TOOL}")
            if cmd.usage:
                body.append(f" {cmd.usage}", style=COLOR_ACCENT)
            if cmd.aliases:
                body.append(f" (/{' /'.join(cmd.aliases)})", style=COLOR_DIM)
            body.append(f" — {cmd.description}\n", style=COLOR_DIM)
        self._mount_chat(Card("命令", body))

    def show_status_card(self) -> None:
        body = Text()
        body.append("protocol", style=f"bold {COLOR_TOOL}")
        body.append(f" {PROTOCOL_VERSION}\n", style="#c0caf5")
        body.append("conversation", style=f"bold {COLOR_TOOL}")
        body.append(f" {self.conversation_id}\n", style="#c0caf5")
        body.append("在途 chat 命令", style=f"bold {COLOR_TOOL}")
        body.append(f" {len(self._chat_inflight)}\n", style="#c0caf5")
        body.append("待处理审批", style=f"bold {COLOR_TOOL}")
        body.append(f" {len(self._approvals)}", style="#c0caf5")
        self._mount_chat(Card("状态", body))

    def clear_transcript(self) -> None:
        self._text_blocks.clear()
        self._thinking.clear()
        self._tools.clear()
        self._data_blocks.clear()
        for panel in list(self._approvals.values()):
            panel.resolved = True
        self._approvals.clear()
        self.status_bar.set_approvals(0)
        self.status_bar.reset_tokens()

    @property
    def pending_attachments(self) -> list[dict]:
        return self._pending_attachments

    def add_attachment(self, attachment: dict) -> None:
        self._pending_attachments.append(attachment)

    def new_conversation(self) -> None:
        """开新对话：生成新 CID，清空显示，保留旧会话可恢复。"""
        self.conversation_id = new_conversation_id()
        self.clear_transcript()
        self.status_bar.set_conversation(self.conversation_id)
        self.show_notice(f"已开新对话 {self.conversation_id}", "success")

    def resolve_skill_name(self, ref: str) -> str | None:
        """通过名称或编号解析 skill 名称。"""
        if not self._cached_skills:
            return None
        if ref.isdigit():
            idx = int(ref) - 1
            if 0 <= idx < len(self._cached_skills):
                return self._cached_skills[idx].get("name")
            return None
        for s in self._cached_skills:
            if s.get("name") == ref:
                return ref
        return None

    # ---------- 用户输入 ----------

    @on(InputArea.Submitted)
    def _on_submit(self, event: InputArea.Submitted) -> None:
        event.stop()
        text = event.text
        if self.palette.display:
            command = self.palette.current_command
            if command is not None:
                if command.usage:
                    # 需要参数的命令：仅补全，不执行
                    self.palette.hide()
                    self.input_area.set_text(f"/{command.name} ")
                    return
                self.palette.hide()
                self._echo_slash(f"/{command.name}")
                if command.handler is not None:
                    command.handler(self, [])
                return

        parsed = parse(text)
        if parsed is not None:
            self._echo_slash(text)
            name, args = parsed
            command = find(name)
            if command is None or command.handler is None:
                self.show_notice(
                    f"未知命令 /{name}，输入 /help 查看全部命令", "error"
                )
            else:
                command.handler(self, args)
            return

        # 普通文本 → chat.send（后端按会话 FIFO 排队）
        echo_text = text
        if self._pending_attachments:
            names = [a["name"] for a in self._pending_attachments]
            echo_text += f"  📎 [{', '.join(names)}]"
        self._mount_chat(UserMessage(echo_text))
        self.status_bar.reset_tokens()
        params: dict[str, Any] = {"text": text}
        if self._pending_attachments:
            params["attachments"] = self._pending_attachments
            self._pending_attachments = []
        self.send_command(
            OP_CHAT_SEND, params, kind="chat", label=OP_CHAT_SEND
        )

    @on(InputArea.TabPressed)
    def _on_tab(self, event: InputArea.TabPressed) -> None:
        event.stop()
        if not self.palette.display:
            return
        command = self.palette.current_command
        if command is not None:
            self.input_area.set_text(
                f"/{command.name} " if command.usage else f"/{command.name}"
            )

    @on(InputArea.PaletteNavigate)
    def _on_palette_navigate(self, event: InputArea.PaletteNavigate) -> None:
        event.stop()
        if self.palette.display:
            self.palette.move_highlight(event.direction)

    @on(ApprovalPanel.Decision)
    def _on_approval_decision(self, event: ApprovalPanel.Decision) -> None:
        event.stop()
        self._resolve_approval(event.panel, event.approved)

    def action_escape(self) -> None:
        """Esc：关面板 > 拒绝审批 > 中断在途 chat。"""
        if self.palette.display:
            self.palette.hide()
            return
        if self._approvals:
            panel = list(self._approvals.values())[-1]
            self._resolve_approval(panel, False)
            return
        if self._chat_inflight:
            self.show_notice("发送中断请求 (chat.interrupt)…", "warn")
            self.send_command(
                "chat.interrupt", {}, kind="clear", label="chat.interrupt"
            )
            return
        self.show_notice("当前没有需要处理的内容", "info")

    # ---------- 命令面板随输入联动 ----------

    @on(InputArea.Changed)
    def _on_input_changed(self, event: InputArea.Changed) -> None:
        if event.text_area is not self.input_area:
            return
        text = self.input_area.text
        if text.startswith("/") and " " not in text:
            self.palette.show_choices(match_prefix(text[1:]))
        else:
            self.palette.hide()

    # ---------- 协议分发 ----------

    def _dispatch_event(self, event: EventProtocol) -> None:
        self.call_next(self._handle_event, event)

    def _dispatch_receipt(self, receipt: ReceiptProtocol) -> None:
        self.call_next(self._handle_receipt, receipt)

    def _dispatch_exit(self, code: int, tail: str) -> None:
        self.call_next(self._handle_backend_exit, code, tail)

    # ---------- 事件 → 渲染 ----------

    @staticmethod
    def _block_key(event: EventProtocol) -> str:
        data = event.data
        return f"{data.get('reply_id')}:{data.get('block_id')}"

    def _handle_event(self, event: EventProtocol) -> None:
        name = event.event
        data = event.data

        if name == "stream.text.start":
            key = self._block_key(event)
            markdown = StreamMarkdown("", classes="assistant-text")
            self._text_blocks[key] = _TextBlock(key, markdown)
            self._mount_chat(markdown)

        elif name == "stream.text":
            block = self._text_blocks.get(self._block_key(event))
            if block is not None:
                block.buffer += data.get("text_delta") or ""
                if not block.scheduled:
                    block.scheduled = True
                    self.set_timer(
                        0.07, functools.partial(self._flush_text, block.key)
                    )

        elif name == "stream.text.end":
            self._flush_text(self._block_key(event), final=True)

        elif name == "stream.thinking.start":
            block = ThinkingBlock(str(data.get("block_id")))
            self._thinking[self._block_key(event)] = block
            self._mount_chat(block)

        elif name == "stream.thinking":
            block = self._thinking.get(self._block_key(event))
            if block is not None:
                block.append_delta(data.get("text_delta") or "")
                self._stick_bottom()

        elif name == "stream.thinking.end":
            block = self._thinking.get(self._block_key(event))
            if block is not None:
                block.finish()

        elif name == "stream.data.start":
            key = self._block_key(event)
            self._data_blocks[key] = {
                "media_type": data.get("media_type", "data"),
                "buffer": b"",
            }

        elif name == "stream.data":
            key = self._block_key(event)
            block = self._data_blocks.get(key)
            if block is not None:
                raw = data.get("data", "")
                if isinstance(raw, str):
                    block["buffer"] += raw.encode(errors="replace")

        elif name == "stream.data.end":
            key = self._block_key(event)
            block = self._data_blocks.pop(key, None)
            if block is not None:
                media = block.get("media_type", "data")
                size = len(block.get("buffer", b""))
                self._mount_chat(
                    HintBlock("data", f"收到数据块: {media} ({size} bytes)")
                )

        elif name == "model.start":
            self.status_bar.set_model(str(data.get("model_name") or "—"))

        elif name == "model.end":
            self.status_bar.add_tokens(
                int(data.get("input_tokens") or 0),
                int(data.get("output_tokens") or 0),
            )

        elif name == "stream.hint":
            self._show_hint(data)

        elif name == "tool.call.start":
            view = ToolCallView(
                str(data.get("tool_call_id")), str(data.get("name") or "tool")
            )
            self._tools[view.tool_call_id] = view
            self._mount_chat(view)

        elif name == "tool.call.delta":
            view = self._tools.get(str(data.get("tool_call_id")))
            if view is not None:
                view.call_delta(str(data.get("delta") or ""))

        elif name == "tool.call.end":
            view = self._tools.get(str(data.get("tool_call_id")))
            if view is not None:
                view.call_end()

        elif name == "tool.result.start":
            pass  # 名称已在 tool.call.start 显示

        elif name == "tool.result.delta":
            view = self._tools.get(str(data.get("tool_call_id")))
            if view is not None:
                view.result_delta(str(data.get("text_delta") or ""))

        elif name == "tool.result.data":
            view = self._tools.get(str(data.get("tool_call_id")))
            if view is not None:
                view.result_data(
                    str(data.get("media_type") or "data"), data.get("url")
                )

        elif name == "tool.result.end":
            view = self._tools.get(str(data.get("tool_call_id")))
            if view is not None:
                state = data.get("state")
                view.result_end(str(state) if state is not None else None)

        elif name == "approval.request":
            self._open_approval(data)

        elif name == "reply.end":
            error = data.get("error")
            if error:
                self.show_notice(f"回复出错：{error}", "error")

        elif name == "run.queued":
            self.show_notice(
                f"已排队等待执行：{data.get('operation')}", "info"
            )
            self.status_bar.set_state("queued")

        elif name == "run.finished":
            self._run_finished(data)

        # reply.start 无需渲染；未知事件静默丢弃

    def _flush_text(self, key: str, final: bool = False) -> None:
        block = self._text_blocks.get(key)
        if block is None:
            return
        # Markdown 挂载完成前 update 的内容会被 on_mount 清空，延迟重试
        if not block.markdown.ready:
            self.set_timer(
                0.03, functools.partial(self._flush_text, key, final)
            )
            return
        block.scheduled = False
        try:
            block.markdown.update(block.buffer)
        except Exception:
            return
        if final:
            self._text_blocks.pop(key, None)
        self._stick_bottom()

    def _run_finished(self, data: dict) -> None:
        request_id = data.get("request_id")
        if request_id is not None:
            self._pending.pop(request_id, None)
            self._chat_inflight.discard(request_id)
        reason = data.get("stop_reason")
        if reason == "error":
            self.show_notice(f"执行出错：{data.get('error') or '未知错误'}", "error")
        elif reason == "interrupted":
            self.show_notice("已中断", "warn")
        for key in list(self._text_blocks):
            self._flush_text(key, final=True)
        if not self._chat_inflight:
            self.status_bar.set_state("idle")

    def _show_hint(self, data: dict) -> None:
        hint = data.get("hint")
        if isinstance(hint, (list, dict)):
            try:
                text = json.dumps(hint, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                text = str(hint)
        else:
            text = str(hint)
        self._mount_chat(HintBlock(data.get("source"), text))

    # ---------- 审批 ----------

    def _open_approval(self, data: dict) -> None:
        approval_id = str(
            data.get("approval_request_id") or data.get("reply_id") or ""
        )
        if not approval_id or approval_id in self._approvals:
            return
        panel = ApprovalPanel(
            approval_id,
            list(data.get("tool_calls") or []),
            classes="approval-panel",
        )
        self._approvals[approval_id] = panel
        self._mount_chat(panel)
        self.call_after_refresh(panel.focus)
        self.status_bar.set_approvals(len(self._approvals))

    def _resolve_approval(self, panel: ApprovalPanel, approved: bool) -> None:
        if panel.resolved:
            return
        panel.mark_resolved(approved)
        self._approvals.pop(panel.approval_request_id, None)
        self.status_bar.set_approvals(len(self._approvals))
        self.send_command(
            "approval.respond",
            {
                "approval_request_id": panel.approval_request_id,
                "approved": approved,
            },
            kind="approval",
            label="approval.respond",
        )
        self.show_notice(
            "已允许该工具调用" if approved else "已拒绝该工具调用",
            "success" if approved else "warn",
        )
        if not self._approvals:
            self.input_area.focus()

    # ---------- 回执 → 展示 ----------

    def _handle_receipt(self, receipt: ReceiptProtocol) -> None:
        request_id = receipt.request_id
        info = self._pending.pop(request_id, None) if request_id else None

        if receipt.accepted:
            if info is None:
                return
            if info.kind == "config":
                self._apply_config_result(receipt.result, info.label)
            elif info.kind == "session":
                self._apply_session_list(receipt.result)
            elif info.kind == "session-resume":
                self._apply_session_resume(receipt.result, info.label)
            elif info.kind == "session-delete":
                self._apply_session_delete(receipt.result)
            elif info.kind == "diff":
                self._apply_diff_show(receipt.result)
            elif info.kind == "undo":
                self._apply_diff_undo(receipt.result)
            elif info.kind == "skill-list":
                self._apply_skill_list(receipt.result)
            elif info.kind == "mcp-list":
                self._apply_mcp_list(receipt.result)
            # chat / approval 的成功回执由后续事件驱动展示
            return

        code = receipt.error_code
        message = receipt.error_message or ""
        self.show_notice(f"命令失败 [{code}] {message}", "error")
        if info is None:
            return
        if info.kind == "chat":
            self._chat_inflight.discard(request_id)
            if not self._chat_inflight:
                self.status_bar.set_state("idle")
        elif info.kind == "approval":
            self.show_notice("审批回执失败，可重新选择 y/n", "warn")

    def _apply_config_result(self, result: Any, label: str) -> None:
        """config.get/set 的回执结果：更新状态栏 + 展示卡片。"""
        flat: dict[str, Any] = {}
        if isinstance(result, dict):
            for section, value in result.items():
                if isinstance(value, dict):
                    flat.update(value)
                else:
                    flat[section] = value
        else:
            flat["result"] = result

        self.status_bar.apply_config(flat)

        body = Text()
        for key, value in flat.items():
            if key in ("credential", "api_key") and isinstance(value, dict):
                # 后端公开视图形如 credential = {"api_key": {"configured": …}}
                api_key = value.get("api_key")
                if not isinstance(api_key, dict):
                    api_key = value
                if api_key.get("configured"):
                    shown = f"已配置 (****{api_key.get('suffix', '')})"
                else:
                    shown = "未配置"
            elif isinstance(value, (dict, list)):
                try:
                    shown = json.dumps(value, ensure_ascii=False)
                except (TypeError, ValueError):
                    shown = str(value)
            else:
                shown = str(value)
            if len(shown) > 72:
                shown = shown[:72] + "…"
            body.append(f"{key:<16}", style=f"bold {COLOR_TOOL}")
            body.append(f"{shown}\n", style="#c0caf5")
        self._mount_chat(Card(label, body))

    def _apply_session_list(self, result: Any) -> None:
        """渲染 session.list 的回执：展示所有历史会话。"""
        sessions = result if isinstance(result, list) else []
        body = Text()
        if not sessions:
            body.append("暂无历史会话", style=COLOR_DIM)
        else:
            for s in sessions:
                cid = s.get("cid", "?")
                summary = s.get("summary") or ""
                modified = s.get("modified")
                body.append(f"  {cid}", style=f"bold {COLOR_TOOL}")
                if summary:
                    body.append(f"  {summary}", style="#c0caf5")
                if modified:
                    from datetime import datetime
                    ts = datetime.fromtimestamp(modified).strftime("%m-%d %H:%M")
                    body.append(f"  {ts}", style=COLOR_DIM)
                body.append("\n")
            body.append("使用 /resume <cid> 恢复，/delete <cid> 删除", style=COLOR_DIM)
        self._mount_chat(Card("历史会话", body))

    def _apply_session_resume(self, result: Any, label: str) -> None:
        """渲染 session.resume 的回执。"""
        if isinstance(result, dict) and result.get("resumed"):
            source = result.get("source_cid", "?")
            self.show_notice(f"已恢复会话 {source}，发送消息即可继续", "success")
        else:
            self.show_notice(f"恢复失败：{result}", "error")

    def _apply_session_delete(self, result: Any) -> None:
        """渲染 session.delete 的回执。"""
        if isinstance(result, dict) and result.get("deleted"):
            self.show_notice(f"已删除会话 {result.get('cid', '?')}", "success")
        else:
            self.show_notice(f"删除失败：{result}", "error")

    def _apply_diff_show(self, result: Any) -> None:
        """渲染 diff.show 的回执：展示文件改动差异。"""
        if not isinstance(result, dict):
            self.show_notice("无改动记录", "info")
            return
        diffs = result.get("diffs", [])
        round_num = result.get("round", "?")
        if not diffs:
            self.show_notice(f"第 {round_num} 轮无文件改动", "info")
            return
        body = Text()
        body.append(f"第 {round_num} 轮，共 {len(diffs)} 个文件\n\n", style=COLOR_DIM)
        for item in diffs:
            file_path = item.get("file", "?")
            diff_text = item.get("diff", "")
            body.append(f"── {file_path} ──\n", style=f"bold {COLOR_TOOL}")
            for line in diff_text.splitlines():
                if line.startswith("+++") or line.startswith("---"):
                    body.append(line + "\n", style=f"bold {COLOR_ACCENT}")
                elif line.startswith("+"):
                    body.append(line + "\n", style=COLOR_OK)
                elif line.startswith("-"):
                    body.append(line + "\n", style=COLOR_ERR)
                elif line.startswith("@@"):
                    body.append(line + "\n", style=COLOR_DIM)
                else:
                    body.append(line + "\n")
            body.append("\n")
        self._mount_chat(Card("文件改动", body))

    def _apply_diff_undo(self, result: Any) -> None:
        """渲染 diff.undo 的回执。"""
        if isinstance(result, dict):
            message = result.get("message", "")
            self.show_notice(message, "success" if result.get("restored") else "warn")
        else:
            self.show_notice("撤销操作完成", "success")

    def _apply_skill_list(self, result: Any) -> None:
        """渲染 skill.list 的回执。"""
        body = Text()
        skills = result if isinstance(result, list) else []
        self._cached_skills = skills
        if not skills:
            body.append("暂无可用 skills", style=COLOR_DIM)
        else:
            for i, s in enumerate(skills, 1):
                name = s.get("name", "?")
                desc = s.get("description", "")
                body.append(f"  {i}. ", style=f"bold {COLOR_TOOL}")
                body.append(name, style="bold #c0caf5")
                if desc:
                    body.append(f"  {desc}", style=COLOR_DIM)
                body.append("\n")
            body.append("使用 /use-skill <编号或名称> 使用", style=COLOR_DIM)
        self._mount_chat(Card("Skills", body))

    def _apply_mcp_list(self, result: Any) -> None:
        """渲染 mcp.list 的回执。"""
        body = Text()
        mcps = result if isinstance(result, list) else []
        self._cached_mcp = mcps
        if not mcps:
            body.append("暂无已连接的 MCP 服务器", style=COLOR_DIM)
        else:
            for i, m in enumerate(mcps, 1):
                name = m.get("name", "?")
                mtype = m.get("type", "?")
                tool_count = m.get("tool_count", 0)
                body.append(f"  {i}. ", style=f"bold {COLOR_TOOL}")
                body.append(name, style="bold #c0caf5")
                body.append(f"  {mtype}", style=COLOR_DIM)
                body.append(f"  {tool_count} tools", style=COLOR_OK)
                body.append("\n")
        self._mount_chat(Card("MCP 服务器", body))

    # ---------- 后端退出 ----------

    def _handle_backend_exit(self, code: int, tail: str) -> None:
        self.status_bar.set_state("idle")
        self.show_notice(f"后端进程已退出 (code={code})", "error")
        if tail:
            shown = tail[-600:]
            self._mount_chat(Card("stderr", Text(shown, style=COLOR_DIM)))

    # ---------- 挂载辅助 ----------

    def _mount_chat(self, widget: Any) -> None:
        self.chat_log.mount(widget)
        self._stick_bottom()

    def _echo_slash(self, text: str) -> None:
        self._mount_chat(UserMessage(text, slash=True))

    def _stick_bottom(self) -> None:
        try:
            log = self.chat_log
            at_bottom = log.scroll_offset.y >= log.max_scroll_y - 2
        except Exception:
            return
        if at_bottom:
            log.scroll_end(animate=False, force=True)
