"""LrmneAgent TUI 主应用：组装界面、生命周期、路由与键位。

分层（与后端 register / handlers / adapter 的分工对称）：
- ``client``       后端子进程与 stdio 行协议
- ``registry``     operation / event 两张分发表
- ``events``       事件渲染器（导入即注册）
- ``results``      成功回执的结果渲染器（导入即注册）
- ``conversation`` 按 cid 的会话状态与聊天流
- ``commands``     斜杠命令 → operation
- ``widgets``      通用部件
- ``theme``        视觉 token

本模块只做四件事：组装、键位与焦点、协议收发、按 cid 路由与会话切换。
"""

from __future__ import annotations

import os
from typing import Any, Callable

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, OptionList

from proxy_layer.schema import (
    PROTOCOL_VERSION,
    EventProtocol,
    ReceiptProtocol,
    make_command,
    new_conversation_id,
    new_request_id,
)

from . import events as _events  # noqa: F401  导入即注册事件渲染器
from . import mentions
from . import results as _results  # noqa: F401  导入即注册结果渲染器
from .client import BackendClient
from .commands import OP_CHAT_SEND, SlashCommand, find, match_prefix, parse
from .conversation import Conversation, ConversationStore, PendingCommand
from .mentions import Mention
from .overlay import (
    Choice,
    ModelConfigOverlay,
    Overlay,
    Picker,
    Prompt,
    SessionSwitcherOverlay,
)
from .registry import lookup_event, lookup_operation
from .theme import (
    GLYPH_USER,
    GRAPHITE,
    S_FAINT,
    S_TEXT,
    S_TOOL,
    S_WARN,
)
from .widgets import (
    ApprovalPanel,
    Card,
    InlinePalette,
    InputArea,
    NoticeLine,
    SidePanel,
    StatusBar,
    UserMessage,
)

# 流式文本的合并刷新间隔：把高频 delta 攒成低频整块写入
STREAM_FLUSH_INTERVAL = 0.08


class LrmneAgentApp(App):
    TITLE = "LrmneAgent"
    SUB_TITLE = "coding agent"

    BINDINGS = [
        # 不设 priority：让浮窗的 priority Esc 先于此处生效（浮窗要独占 Esc）
        Binding("escape", "escape", "暂停/关闭"),
        Binding("f2", "model_config", "模型配置"),
        Binding("f3", "sessions", "会话"),
        Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    CSS = """
    Screen {
        background: $background;
        color: $text;
    }
    Header {
        background: $background;
        color: $text;
    }
    Footer {
        background: $surface;
        color: $text-dim;
    }
    #main-row {
        height: 1fr;
    }
    #chat-area {
        width: 1fr;
        height: 1fr;
    }
    /* 右侧栏：与底部状态栏同一档底色，读起来是同一层"边框外的 chrome"。
       三类信息各占一个小框，类别名写在框上；宽度按最长的一行（目录 + 会话号）
       留够，不做省略。配置框钉在底部：上面两框会随上下文分段数长高，窗口不够高
       时该长高的自己滚，不能把不常变的档位挤出可视区。 */
    #side {
        width: 34;
        height: 1fr;
        background: $surface;
        padding: 1 1 0 1;
    }
    #side-scroll {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-background: $surface;
        scrollbar-color: $border;
    }
    .side-box {
        width: 100%;
        height: auto;
        border: round $border;
        background: $surface;
        padding: 0 1;
        margin: 0 0 1 0;
        color: $text-dim;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        border-title-color: $text-dim;
        border-title-align: left;
    }
    #side-config {
        dock: bottom;
    }
    ChatView {
        height: 1fr;
        padding: 0 2;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $background;
        scrollbar-color: $border;
    }
    .user-message {
        margin: 1 0 0 0;
    }
    .notice {
        margin: 0;
    }
    /* 转录区的「一块」：左上标题写这是什么，右下状态写进行到哪。边框只有状态语义色
       会变，其余一律最弱一档灰——转录里的亮度应该来自正文，而不是容器。
       标题本身走「次要」那一档（$text-dim）：它是块的名字，太浅就读不出结构，
       提到正文那一档又会跟正文抢注意力。 */
    .block {
        height: auto;
        border: round $border;
        background: $surface;
        padding: 0 1;
        margin: 1 0;
        border-title-color: $text-dim;
        border-title-align: left;
        border-subtitle-align: right;
        border-subtitle-color: $text-faint;
    }
    .block.state-running {
        border-subtitle-color: $warn;
    }
    .block.state-ok {
        border-subtitle-color: $ok;
    }
    .block.state-err {
        border-subtitle-color: $err;
    }
    .block.state-warn {
        border-subtitle-color: $warn;
    }
    /* 分类型描边：一眼看出这块是助手、工具还是思考，色相只到「分得出来」为止。
       审批单独一档最暖的陶土——它是唯一要用户表态的块。 */
    .assistant-block {
        border: round $tint-assistant;
    }
    .tool-call {
        border: round $tint-tool;
    }
    .thinking {
        border: round $tint-thinking;
    }
    .tool-detail {
        margin: 0;
        color: $text-dim;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    .assistant-text {
        margin: 0;
    }
    .thinking-body {
        color: $text-faint;
        text-style: italic;
    }
    /* 系统提示：一行弱标记，正文（运行时上下文）不进转录 */
    .hint {
        margin: 0;
    }
    /* 块内的折叠块（思考正文 / 改动正文 / 命令输出）：块本身已经画了边框，
       里层不能再铺一层 hkey 顶线与底色，否则一个块里又套出一个格子。 */
    .block CollapsibleTitle,
    .diff-fold CollapsibleTitle,
    .result-fold CollapsibleTitle {
        padding: 0;
        background: transparent;
        text-style: none;
        color: $text-faint;
    }
    /* 只有用户落到这一行（hover 或点开）才提亮。CollapsibleTitle 默认在 hover/focus
       时铺一块主题色实心底（石墨主题下是浅灰块），点一下标题就跳出一大块亮色，抹掉。 */
    .block CollapsibleTitle:hover,
    .block CollapsibleTitle:focus,
    .diff-fold CollapsibleTitle:hover,
    .diff-fold CollapsibleTitle:focus,
    .result-fold CollapsibleTitle:hover,
    .result-fold CollapsibleTitle:focus {
        background: transparent;
        color: $text-dim;
    }
    .block Collapsible Contents {
        padding: 0;
        background: transparent;
    }
    .diff-fold,
    .result-fold {
        padding: 0 0 0 2;
        margin: 0;
        border: none;
        background: transparent;
    }
    .diff-fold Contents,
    .result-fold Contents {
        padding: 0 0 0 2;
        background: transparent;
    }
    .diff-body {
        color: $text-dim;
    }
    .tool-result {
        color: $text-faint;
        padding-left: 2;
        margin: 0;
    }
    .tool-result-full {
        color: $text-dim;
        margin: 0;
    }
    .card {
        border: round $border;
        background: $surface;
        padding: 0 1;
        margin: 1 0;
        border-title-color: $text-dim;
        border-title-align: left;
    }
    .approval-panel {
        height: auto;
        border: round $tint-approval;
        background: $surface;
        padding: 0 1;
        margin: 1 0;
        border-title-color: $warn;
        border-title-align: left;
    }
    .approval-panel.resolved {
        border: round $border;
        opacity: 0.55;
    }
    .approval-item {
        margin: 0;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    /* 扁平文字按钮：不画边框不填色，只在悬停/聚焦时抬一层底色 */
    .approval-buttons {
        height: auto;
        margin: 1 0 0 0;
    }
    .approval-buttons Button {
        margin-right: 1;
        min-width: 0;
        height: 1;
        padding: 0 1;
        border: none;
        background: $surface;
        color: $text-dim;
    }
    .approval-buttons Button:hover,
    .approval-buttons Button:focus {
        background: $surface-alt;
        color: $text;
    }
    #palette {
        height: auto;
        max-height: 12;
        margin: 0 1;
        border: round $border-strong;
        background: $surface;
        scrollbar-size-vertical: 1;
        border-title-color: $text-faint;
        border-title-align: left;
        border-subtitle-align: right;
        border-subtitle-color: $text-faint;
    }
    /* 当前项：抬一层底色 + 提亮文字，而不是 Textual 默认的实心亮灰块。
       $surface-alt 在石墨底上还是偏沉，用边框那一档灰才看得出光标在哪一行。 */
    #palette > .option-list--option-highlighted {
        background: $border-strong;
        color: $text;
        text-style: bold;
    }
    #status {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    .status-left {
        width: 1fr;
        color: $text-dim;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    #input {
        height: 5;
        border: round $border;
        background: $surface;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    #input:focus {
        border: round $border-focus;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        # 主题必须在 App.CSS 被解析之前注册：本文件的 CSS 直接引用 GRAPHITE
        # 暴露的 $text-dim / $surface-sunk 等自定义变量，晚一步就会解析失败。
        self.register_theme(GRAPHITE)
        self.theme = GRAPHITE.name
        self.store = ConversationStore()
        self.client = BackendClient(
            on_event=self._dispatch_event,
            on_receipt=self._dispatch_receipt,
            on_exit=self._dispatch_exit,
        )
        # 最近一次 config.* 回执的扁平值：选择卡片据此标出当前项
        self.config: dict[str, Any] = {}
        # 待用的配置确认语：由 set_config_value 写入，回执渲染时取走
        self._pending_set = ""
        # /status 主动要一张配置卡片：启动时的静默 config.get 不该在转录里出内容
        self._pending_status = False
        # @ 提及的候选：文件按工作目录缓存一次，skill 列表由后端回执填
        self._files_cache: tuple[str, list[Mention]] | None = None
        self.skills: list[Mention] = []
        self._skills_pending = False
        self._chat_area: Container | None = None
        self._side: SidePanel | None = None
        self._palette: InlinePalette | None = None
        self._status: StatusBar | None = None
        self._input: InputArea | None = None

    # ---------- 组件快捷访问 ----------

    @property
    def chat_area(self) -> Container:
        assert self._chat_area is not None
        return self._chat_area

    @property
    def side(self) -> SidePanel:
        """右侧栏：底部那一行放不下的量化信息都归它。"""
        assert self._side is not None
        return self._side

    @property
    def palette(self) -> InlinePalette:
        assert self._palette is not None
        return self._palette

    @property
    def status(self) -> StatusBar:
        assert self._status is not None
        return self._status

    @property
    def input_area(self) -> InputArea:
        assert self._input is not None
        return self._input

    @property
    def active_overlay(self) -> Overlay | None:
        """当前压在最上层的浮窗；没有则为 None。"""
        try:
            screen = self.screen
        except Exception:
            return None
        return screen if isinstance(screen, Overlay) else None

    @property
    def model_config_overlay(self) -> ModelConfigOverlay | None:
        """模型配置浮窗是否在前台；结果渲染器据此决定回执交给谁。"""
        overlay = self.active_overlay
        return overlay if isinstance(overlay, ModelConfigOverlay) else None

    @property
    def session_overlay(self) -> SessionSwitcherOverlay | None:
        """会话切换浮窗是否在前台。"""
        overlay = self.active_overlay
        return overlay if isinstance(overlay, SessionSwitcherOverlay) else None

    @property
    def picker_overlay(self) -> Picker | None:
        """前台的选择浮窗；回执渲染器据此把列表投给它。"""
        overlay = self.active_overlay
        return overlay if isinstance(overlay, Picker) else None

    # ---------- 配置缓存 ----------

    def remember_config(self, flat: dict[str, Any]) -> None:
        """记下 config.* 回执里的最新值，供各浮窗标出「当前是哪一项」。"""
        self.config.update({key: value for key, value in flat.items() if value is not None})

    def set_config_value(self, key: str, value: Any, confirmation: str) -> None:
        """写入单个配置项。

        ``confirmation`` 是一句用户语言的结果描述（"思考级别已设为 high"）：
        后端回执会带上整个配置块，那不是用户该看的东西，由发起方提供这一句，
        由 :meth:`consume_pending_set` 在回执渲染时取走。
        """
        self._pending_set = confirmation
        self.send("config.set", {"key": key, "value": value})

    def consume_pending_set(self) -> str:
        """取走待用的配置确认语（读一次即清）。"""
        confirmation, self._pending_set = self._pending_set, ""
        return confirmation

    def request_status_card(self) -> None:
        """请求在 config.get 回执到达时渲染一张配置卡片（/status 用）。"""
        self._pending_status = True

    def consume_status_card(self) -> bool:
        requested, self._pending_status = self._pending_status, False
        return requested

    @property
    def thinking_level(self) -> str:
        return str(self.config.get("thinking_level") or "")

    @property
    def permission_mode(self) -> str:
        return str(self.config.get("mode") or "")

    @property
    def workspace_root(self) -> str:
        return str(self.config.get("root") or "")

    # ---------- 组装与生命周期 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Horizontal(
            Container(id="chat-area"),
            SidePanel(id="side"),
            id="main-row",
        )
        yield InlinePalette(id="palette")
        yield StatusBar(id="status")
        yield InputArea(
            id="input",
            placeholder="发送消息，/ 呼出命令，@ 引用文件，Esc 暂停",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._chat_area = self.query_one("#chat-area", Container)
        self._side = self.query_one("#side", SidePanel)
        self._palette = self.query_one("#palette", InlinePalette)
        self._status = self.query_one("#status", StatusBar)
        self._input = self.query_one("#input", InputArea)

        self.open_conversation(new_conversation_id(), welcome=True)
        self.set_interval(STREAM_FLUSH_INTERVAL, self._flush_streams)
        self.run_worker(self.client.start(), exclusive=False)
        # 启动即拉配置，填充状态栏
        self.send("config.get", {})

    async def _on_exit_app(self) -> None:
        await self.client.stop()

    # ---------- 会话 ----------

    def open_conversation(self, cid: str, *, welcome: bool = False) -> Conversation:
        """取得或新建会话，并切到前台。"""
        conv = self.store.get(cid) or self.store.create(cid)
        if conv.view.parent is None:
            self.chat_area.mount(conv.view)
        if welcome:
            self._mount_welcome(conv)
        self.activate(cid)
        return conv

    def activate(self, cid: str) -> None:
        """切换前台会话：置换显示、交接草稿、刷新状态栏。"""
        previous = self.store.get(self.store.current_cid)
        if previous is not None and previous.cid != cid:
            previous.draft = self.input_area.text
        conv = self.store.switch(cid)
        for item in self.store.all():
            item.view.display = item.cid == cid
        self.input_area.set_text(conv.draft)
        # 浮窗在前台时别抢焦点：会话切换浮窗自己还要继续用键盘
        if self.active_overlay is None:
            self.input_area.focus()
        self.refresh_status()

    def switch_conversation(self, cid: str) -> bool:
        """切到本进程已打开的会话；未打开则提示并返回 False。"""
        if self.store.get(cid) is None:
            self.notify_line(f"会话 {cid} 未在本进程打开", "warn")
            return False
        self.activate(cid)
        return True

    def resume_conversation(self, source_cid: str) -> Conversation:
        """把磁盘存档读进一个**新**会话。

        后端 ``session.resume`` 固定写到当前 cid（协议既成事实），所以先开一个空会话
        再在它上面恢复：既不覆盖用户正开着的会话，恢复后的对话也有自己的 cid。
        """
        conv = self.open_conversation(new_conversation_id())
        self.notify_line(f"正在载入存档 {source_cid}…", "info", conv=conv)
        self.send("session.resume", {"source_cid": source_cid}, conv=conv)
        return conv

    def delete_session(self, cid: str) -> None:
        """删除磁盘存档（会话切换浮窗的 d 键）。"""
        self.send("session.delete", {"cid": cid})

    def close_conversation(self, cid: str) -> None:
        """关闭会话；关掉最后一个时自动补一个新的，保证前台始终存在。"""
        self.store.close(cid)
        if not self.store.all():
            self.open_conversation(new_conversation_id(), welcome=True)
        elif self.store.current_cid != cid:
            self.activate(self.store.current_cid)

    def refresh_status(self) -> None:
        """把当前会话的运行态同步到状态栏与右侧栏。

        两个部件的分工只有一处判据：一眼要看到的留底部，其余进右侧栏。
        """
        conv = self.store.current
        self.status.set_state(conv.run_state)
        self.status.set_model(conv.model)
        self.status.set_approvals(len(conv.approvals))
        self.status.set_usage(
            conv.context_tokens, conv.cache_tokens, conv.cache_created
        )
        self.side.set_usage(
            conv.tokens_in,
            conv.tokens_out,
            conv.context_tokens,
            conv.cache_tokens,
            conv.cache_created,
            conv.gen_seconds,
        )
        self.side.set_context(conv.context_usage)
        self.side.set_conversation(conv.cid)

    def apply_config(self, flat: dict[str, Any]) -> None:
        """config.* 回执落到两个部件：底部取压力表口径，右侧栏取档位与工作区。"""
        self.status.apply_config(flat)
        self.side.apply_config(flat)

    # ---------- 协议收发 ----------

    def send(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        conv: Conversation | None = None,
    ) -> str:
        """按 operation 名发一条协议命令，并登记回执等待。"""
        target = conv or self.store.current
        spec = lookup_operation(operation)
        request_id = new_request_id()
        target.pending[request_id] = PendingCommand(
            request_id,
            operation,
            spec.label if spec is not None else operation,
        )
        if spec is not None and spec.stream:
            target.inflight.add(request_id)
        try:
            self.client.send(
                make_command(request_id, target.cid, operation, parameters or {}),
            )
        except RuntimeError as exc:
            target.pending.pop(request_id, None)
            target.inflight.discard(request_id)
            self.notify_line(f"发送失败：{exc}", "error", conv=target)
        self.refresh_status()
        return request_id

    def notify_line(
        self, text: str, kind: str = "info", *, conv: Conversation | None = None
    ) -> None:
        (conv or self.store.current).view.add(NoticeLine(text, kind))

    # ---------- 分发 ----------

    def _dispatch_event(self, event: EventProtocol) -> None:
        self.call_next(self._handle_event, event)

    def _dispatch_receipt(self, receipt: ReceiptProtocol) -> None:
        self.call_next(self._handle_receipt, receipt)

    def _dispatch_exit(self, code: int, tail: str) -> None:
        self.call_next(self._handle_backend_exit, code, tail)

    def _handle_event(self, event: EventProtocol) -> None:
        """事件按信封上的 conversation_id 路由到对应会话。"""
        conv = self.store.get_or_create(event.conversation_id)
        handler = lookup_event(event.event)
        if handler is not None:
            handler(self, conv, event.data)

    def _handle_receipt(self, receipt: ReceiptProtocol) -> None:
        conv = self.store.get_or_create(receipt.conversation_id)
        request_id = receipt.request_id
        info = conv.pending.pop(request_id, None) if request_id else None

        if receipt.accepted:
            spec = lookup_operation(info.operation) if info is not None else None
            if spec is not None and spec.render_result is not None:
                spec.render_result(self, conv, receipt.result)
            return

        conv.view.add(
            NoticeLine(
                f"命令失败 [{receipt.error_code}] {receipt.error_message or ''}",
                "error",
            ),
        )
        # 参数校验失败等路径不会再有 run.finished，需在此清掉在途标记
        if info is not None and request_id is not None:
            conv.inflight.discard(request_id)
        # 失败时渲染器不会跑，别把这一轮的确认语留给下一条回执
        self._pending_set = ""
        self._pending_status = False
        self._skills_pending = False
        self.refresh_status()

    def _handle_backend_exit(self, code: int, tail: str) -> None:
        self.status.set_state("idle")
        self.notify_line(f"后端进程已退出 (code={code})", "error")
        if tail:
            self.store.current.view.add(
                Card("stderr", Text(tail[-600:], style=S_FAINT)),
            )

    def _flush_streams(self) -> None:
        """把各会话攒下的流式文本合并写入并保持粘底。"""
        for conv in self.store.all():
            if conv.flush_dirty():
                conv.view.stick_bottom()

    # ---------- 用户输入 ----------

    @on(InputArea.Submitted)
    def _on_submit(self, event: InputArea.Submitted) -> None:
        event.stop()
        text = event.text

        # 先把面板当前选中的项读下来再动输入框：清空会触发的 Changed 会异步收起面板，
        # 晚一步读就永远是空的。光一个 "/" 也走这里（默认选中第一条命令），否则它会被
        # parse() 判为非命令，当成聊天内容发给后端。
        if self.palette.display:
            item = self.palette.current_mention
            if item is not None:
                self._accept_mention(item)
                return
            chosen = self.palette.current_command
            if chosen is not None:
                self._run_command(chosen)
                return
        if text == "/":
            # 面板被 Esc 收起后只剩一个 "/"：重开列表，别把它当聊天内容发出去
            self.palette.show_commands(match_prefix(""))
            return
        self.input_area.clear()

        parsed = parse(text)
        if parsed is not None:
            self._echo_slash(text)
            name, args = parsed
            command = find(name)
            if command is None or command.handler is None:
                self.notify_line(f"未知命令 /{name}，输入 /help 查看全部命令", "error")
                return
            if args:
                # 老写法（/thinking high、/config key value）不再吃参数
                self.notify_line(
                    f"/{command.name} 不接受参数，取值请在弹出的卡片里选", "warn"
                )
            command.handler(self)
            return

        self.send_chat(text)

    def _accept_mention(self, item: Mention) -> None:
        """把正在输入的 ``@片段`` 换成选中的候选。

        目录只补成 ``@backend/adapter/``：面板不收起，列表接着列下一层，等于往下钻；
        文件与 skill 补成 ``@标签 ``，光标落到空格后继续写正文。光标放在哪个词上决定
        了「当前在哪一层」，所以这里不需要额外记状态。
        """
        area = self.input_area
        text = area.text
        offset = mentions.cursor_offset(text, area.cursor_location)
        replaced = mentions.accept(text, offset, item)
        if replaced is None:
            self.palette.hide()
            return
        updated, caret = replaced
        area.set_text(updated)
        area.move_cursor(mentions.location_of(updated, caret))
        if item.kind == mentions.DIR_KIND:
            self.sync_palette()  # 接着列下一层；列表空了自己会让出一行说明
        else:
            self.palette.hide()
        area.focus()

    def _run_command(self, command: SlashCommand) -> None:
        """执行命令面板里选中的命令（Enter 或鼠标点选都走这里）。"""
        self.palette.hide()
        self.input_area.clear()
        self._echo_slash(f"/{command.name}")
        if command.handler is not None:
            command.handler(self)
        # 命令可能弹了浮窗，那就别把焦点抢回输入框；否则焦点会留在已收起的面板上
        if self.active_overlay is None:
            self.input_area.focus()

    def send_chat(self, text: str) -> None:
        """普通文本 → chat.send（后端按会话 FIFO 排队）。"""
        conv = self.store.current
        echo = text
        if conv.attachments:
            names = [item["name"] for item in conv.attachments]
            echo += f"  [{', '.join(names)}]"
        conv.view.add(UserMessage(echo))
        conv.reset_tokens()

        # 只有 /attach 带来的附件；正文里的 @路径 不读文件——它只是提示词里的一行字，
        # 由模型自己用读文件的工具去取（省 token，也不会先塞进一份会过期的副本）
        params: dict[str, Any] = {"text": text}
        if conv.attachments:
            params["attachments"] = conv.attachments
        conv.attachments = []
        self.send(OP_CHAT_SEND, params, conv=conv)

    @on(InputArea.TabPressed)
    def _on_tab(self, event: InputArea.TabPressed) -> None:
        event.stop()
        if not self.palette.display:
            return
        if self.palette.mode == "mention":
            item = self.palette.current_mention
            if item is not None:
                self._accept_mention(item)
            return
        chosen = self.palette.current_command
        if chosen is not None:
            self.input_area.set_text(f"/{chosen.name}")

    @on(InputArea.PaletteNavigate)
    def _on_palette_navigate(self, event: InputArea.PaletteNavigate) -> None:
        event.stop()
        if self.palette.display:
            self.palette.move_highlight(event.direction)

    @on(OptionList.OptionSelected, "#palette")
    def _on_palette_clicked(self, event: OptionList.OptionSelected) -> None:
        """鼠标点选与 Enter 等价：点哪条就执行 / 插入哪条。"""
        event.stop()
        if self.palette.mode == "mention":
            item = self.palette.current_mention
            if item is not None:
                self._accept_mention(item)
            return
        command = find(str(event.option_id))
        if command is not None:
            self._run_command(command)

    @on(InputArea.Changed)
    def _on_input_changed(self, event: InputArea.Changed) -> None:
        if event.text_area is not self.input_area:
            return
        self.sync_palette()

    def sync_palette(self) -> None:
        """按触发词决定面板：``/`` 给命令，``@`` 给工作区目录树与 skill，其余收起。"""
        area = self.input_area
        offset = mentions.cursor_offset(area.text, area.cursor_location)
        trigger = mentions.triggered(area.text, offset)
        if trigger is None:
            self.palette.hide()
            return
        kind, fragment = trigger
        if kind == "command":
            self.palette.show_commands(match_prefix(fragment))
            return
        self.palette.show_mentions(self.mention_candidates(fragment))

    def mention_candidates(self, fragment: str) -> list[Mention]:
        """提及候选：skill 在前（数量少、语义强），其后是路径。

        ``@`` 空着就列**当前那一层**（目录在前、能接着往下钻）；打了字则在当前目录
        之下**递归搜关键字**——不然 ``@work`` 找不到深处的 ``backend/workspace.py``。
        当前在哪一层由正文本身决定：``@backend/adapter/`` 的作用域就是它自己。
        """
        self._load_skills_once()
        root = self.workspace_root or os.getcwd()
        scope, query = mentions.scope_of(fragment)
        if not query:
            return [*self.skills, *mentions.browse(root, scope)]
        pool = self.workspace_files()
        if scope:
            # 已经钻进某一层了：只在它下面搜（缓存里就是带前缀的相对路径，过滤即可）
            pool = [item for item in pool if item.label.startswith(scope)]
        return mentions.match([*self.skills, *pool], query)

    def workspace_files(self) -> list[Mention]:
        """整个工作区的文件与目录：按工作目录缓存一次，扫描是同步 IO，别每次按键都走。"""
        # 启动时 config.get 还没回来，root 是空的；此时按进程 cwd 列（前端就是从
        # 项目根启动的，见 README 的启动方式），等配置到了再按真正的 root 重扫
        root = self.workspace_root or os.getcwd()
        if self._files_cache is None or self._files_cache[0] != root:
            self._files_cache = (root, mentions.walk(root))
        return self._files_cache[1]

    def _load_skills_once(self) -> None:
        if self.skills or self._skills_pending:
            return
        self._skills_pending = True
        self.send("skill.list", {})

    def remember_skills(self, skills: list) -> None:
        """``skill.list`` 回执落进提及候选；@ 面板正开着就顺手重排一遍。"""
        self.skills = mentions.from_skills(skills)
        if self.palette.display and self.palette.mode == "mention":
            self.sync_palette()

    def consume_mention_skills(self) -> bool:
        """取走「这次 skill.list 是 @ 面板发起的」标记（读一次即清）。

        这种请求只是为了填候选，不能在转录里出卡片。
        """
        pending, self._skills_pending = self._skills_pending, False
        return pending

    @on(ApprovalPanel.Decision)
    def _on_approval_decision(self, event: ApprovalPanel.Decision) -> None:
        event.stop()
        self._resolve_approval(event.panel, event.approved, always=event.always)

    def action_escape(self) -> None:
        """Esc：关面板 > 拒绝审批 > 暂停在途对话；没有可暂停的事就什么都不做。

        浮窗打开时由 ``Overlay`` 自己的 Esc 绑定先消费，走不到这里。

        一次运行只下发一次中断：中断就是取消正在跑的那个任务，重复下发会让
        agentscope 在收尾途中再挨一次取消，结果帧与 run.finished 都发不出来，
        界面反而卡在「执行中」（见 ``Conversation.stopping``）。
        """
        if self.palette.display:
            self.palette.hide()
            return
        conv = self.store.current
        if conv.approvals:
            self._resolve_approval(list(conv.approvals.values())[-1], False)
            return
        if conv.inflight and not conv.stopping:
            conv.stopping = True
            self.send("chat.interrupt", {}, conv=conv)
            self.notify_line("已暂停", "warn", conv=conv)

    # ---------- 审批 ----------

    def _conversation_of(self, panel: ApprovalPanel) -> Conversation:
        for conv in self.store.all():
            if panel.approval_request_id in conv.approvals:
                return conv
        return self.store.current

    def _resolve_approval(
        self, panel: ApprovalPanel, approved: bool, *, always: bool = False
    ) -> None:
        if panel.resolved:
            return
        conv = self._conversation_of(panel)
        panel.mark_resolved(approved)
        conv.approvals.pop(panel.approval_request_id, None)
        # 没有别的要等的审批了才恢复计时：等待那段不算进工具耗时
        if not conv.approvals:
            conv.set_tools_awaiting(False)
        self.refresh_status()
        self.send(
            "approval.respond",
            {
                "approval_request_id": panel.approval_request_id,
                "approved": approved,
                "always": approved and always,
            },
            conv=conv,
        )
        # 结果由面板自己说（已处理 / 已允许，等待工具执行…），不再往转录里补一行
        if not conv.approvals:
            self.input_area.focus()

    # ---------- 本地动作（供 commands.py 调用） ----------

    def _echo_slash(self, text: str) -> None:
        self.store.current.view.add(UserMessage(text, slash=True))

    def _mount_welcome(self, conv: Conversation) -> None:
        body = Text()
        body.append(
            f"protocol {PROTOCOL_VERSION}  ·  conversation {conv.cid}\n",
            style=S_FAINT,
        )
        body.append(f"{GLYPH_USER} ", style=S_TEXT)
        body.append("输入消息开始对话\n", style=S_TEXT)
        body.append("/ ", style=S_TOOL)
        body.append("呼出命令    ", style=S_FAINT)
        body.append("@ ", style=S_TOOL)
        body.append("引用文件或 skill\n", style=S_FAINT)
        body.append("Esc ", style=S_WARN)
        body.append("暂停    ", style=S_FAINT)
        body.append("Ctrl+Q ", style=S_FAINT)
        body.append("退出", style=S_FAINT)
        conv.view.add(Card("LrmneAgent", body))

    def new_conversation(self) -> None:
        cid = new_conversation_id()
        self.open_conversation(cid, welcome=True)
        self.notify_line(f"已开新对话 {cid}", "success")

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

    def clear_transcript(self) -> None:
        conv = self.store.current
        conv.clear_render_state()
        conv.view.remove_children()
        self.refresh_status()

    def show_command_help(self) -> None:
        from .commands import COMMANDS

        body = Text()
        for cmd in COMMANDS:
            body.append(f"/{cmd.name}", style=S_TEXT)
            if cmd.aliases:
                body.append(f" (/{' /'.join(cmd.aliases)})", style=S_TOOL)
            body.append(f" — {cmd.description}\n", style=S_FAINT)
        self.store.current.view.add(Card("命令", body))

    def add_attachment(self, attachment: dict) -> None:
        self.store.current.attachments.append(attachment)

    @property
    def pending_attachments(self) -> list[dict]:
        return self.store.current.attachments
