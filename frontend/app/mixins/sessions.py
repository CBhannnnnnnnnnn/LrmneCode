"""会话生命周期与配置落地：开/切/续/删会话，把后端回执刷进界面。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any
from rich.text import Text
from proxy_layer.schema import (PROTOCOL_VERSION, new_conversation_id)
from ...conversation import (Conversation)
from ...theme import (GLYPH_USER, S_FAINT, S_TEXT, S_TOOL, S_WARN)
from ...widgets import (Card)


class SessionsMixin:
    """会话生命周期与配置落地：开/切/续/删会话，把后端回执刷进界面。"""

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
        pending_out, pending_chars = conv.live_output()
        self.side.set_usage(
            conv.tokens_in,
            conv.tokens_out,
            conv.context_tokens,
            conv.cache_tokens,
            conv.cache_created,
            conv.gen_seconds,
            pending_out,
            pending_chars,
            conv.live_seconds(),
        )
        self.side.set_context(conv.context_usage)
        self.side.set_conversation(conv.cid)

    def apply_config(self, flat: dict[str, Any]) -> None:
        """config.* 回执落到两个部件：底部取压力表口径，右侧栏取档位与工作区。"""
        self.status.apply_config(flat)
        self.side.apply_config(flat)

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

    def clear_transcript(self) -> None:
        conv = self.store.current
        conv.clear_render_state()
        conv.view.remove_children()
        self.refresh_status()
