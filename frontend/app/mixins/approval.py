"""审批与中断：表态回灌、Esc 暂停、按面板找回它所属的会话。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from ...conversation import (Conversation)
from ...widgets import (ApprovalPanel)


class ApprovalMixin:
    """审批与中断：表态回灌、Esc 暂停、按面板找回它所属的会话。"""

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
