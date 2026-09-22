"""主应用类：组装界面、键位与焦点，以及协议事件的入口。

按职责分到 :mod:`frontend.app.mixins`；但 6 个 ``@on`` 入口与 ``BINDINGS`` 必须留在
本类自身——Textual 只从类自己的 ``__dict__`` 收集装饰过的处理器（见 docs/adr/0003）。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, OptionList
from proxy_layer.schema import (new_conversation_id)
from .. import events as _events  # noqa: F401  导入即注册事件渲染器
from .. import results as _results  # noqa: F401  导入即注册结果渲染器
from ..client import (BackendClient)
from ..commands import (find, match_prefix, parse)
from ..conversation import (ConversationStore)
from ..mentions import (Mention)
from ..overlay import (ModelConfigOverlay, Overlay, Picker, SessionSwitcherOverlay)
from ..theme import (GRAPHITE)
from ..widgets import (ApprovalPanel, InlinePalette, InputArea, SidePanel, StatusBar)
from .mixins.sessions import SessionsMixin
from .mixins.protocol import ProtocolMixin
from .mixins.interaction import InteractionMixin
from .mixins.mentions import MentionsMixin
from .mixins.overlays import OverlaysMixin
from .mixins.approval import ApprovalMixin
from .params import STREAM_FLUSH_INTERVAL
from .styles import APP_CSS


class LrmneAgentApp(SessionsMixin, ProtocolMixin, InteractionMixin, MentionsMixin, OverlaysMixin, ApprovalMixin, App):
    TITLE = "LrmneAgent"
    SUB_TITLE = "coding agent"

    BINDINGS = [
        # 不设 priority：让浮窗的 priority Esc 先于此处生效（浮窗要独占 Esc）
        Binding("escape", "escape", "暂停/关闭"),
        Binding("f2", "model_config", "模型配置"),
        Binding("f3", "sessions", "会话"),
        Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    CSS = APP_CSS

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

    @on(ApprovalPanel.Decision)
    def _on_approval_decision(self, event: ApprovalPanel.Decision) -> None:
        event.stop()
        self._resolve_approval(event.panel, event.approved, always=event.always)
