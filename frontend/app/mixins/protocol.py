"""协议收发：命令下发、事件与回执按 cid 路由、流式合并、后端退出。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any
from rich.text import Text
from proxy_layer.schema import (EventProtocol, ReceiptProtocol, make_command, new_request_id)
from ...commands import (OP_CHAT_SEND)
from ...conversation import (Conversation, PendingCommand)
from ...registry import (lookup_event, lookup_operation)
from ...theme import (S_FAINT)
from ...widgets import (Card, NoticeLine, UserMessage)


class ProtocolMixin:
    """协议收发：命令下发、事件与回执按 cid 路由、流式合并、后端退出。"""

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
