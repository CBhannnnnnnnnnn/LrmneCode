"""输入侧行为：斜杠命令执行、命令面板同步、@ 提及落字、附件与状态卡片。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from rich.text import Text
from ... import (mentions)
from ...commands import (SlashCommand, match_prefix)
from ...mentions import (Mention)
from ...theme import (S_FAINT, S_TEXT, S_TOOL)
from ...widgets import (Card, UserMessage)


class InteractionMixin:
    """输入侧行为：斜杠命令执行、命令面板同步、@ 提及落字与附件。"""

    @property
    def thinking_level(self) -> str:
        return str(self.config.get("thinking_level") or "")

    @property
    def permission_mode(self) -> str:
        return str(self.config.get("mode") or "")

    @property
    def workspace_root(self) -> str:
        return str(self.config.get("root") or "")

    @property
    def context_size(self) -> int:
        value = self.config.get("context_size")
        return value if isinstance(value, int) else 0

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

    # ---------- 本地动作（供 commands.py 调用） ----------

    def _echo_slash(self, text: str) -> None:
        self.store.current.view.add(UserMessage(text, slash=True))

    def show_command_help(self) -> None:
        from ...commands import COMMANDS

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
