"""按 CommandMode 调度前端 command：PLAIN/CONTROL 直通，WAIT 按会话 FIFO。"""

import asyncio
from typing import AsyncGenerator

from .executor import Executor
from .register import lookup

from .schema import (
    CommandMode,
    CommandProtocol,
    ErrorCode,
    make_event,
    make_receipt_error,
)

_executor = Executor()
_queues: dict[str, list[asyncio.Task]] = {}


def cancel_pending(conversation_id: str) -> None:
    """取消指定会话中尚未结束的排队/在途 wait 任务。"""

    queue = _queues.get(conversation_id)
    if not queue:
        return
    for task in queue:
        if not task.done():
            task.cancel()

def cancel_all() -> None:
    """取消全部会话的排队/在途 wait 任务。"""
    for queue in _queues.values():
        for task in queue:
            if not task.done():
                task.cancel()

async def submit(msg: CommandProtocol) -> AsyncGenerator:
    """受理一条 command 并产出回执/事件。

    未注册操作不在此拦截，直通 Executor 回 UNKNOWN_OPERATION。
    """

    operation = lookup(msg.operation)
    
    mode = operation["mode"] if operation is not None else None

    # CONTROL：先清本会话在途与排队，再执行
    if mode == CommandMode.CONTROL:
        cancel_pending(msg.conversation_id)

    # PLAIN / CONTROL / 未注册：不排队
    if mode != CommandMode.WAIT:

        async for chunk in _executor.command_executor(
            msg.request_id, msg.operation, msg.conversation_id, msg.parameters,
        ):
            yield chunk
        return

    # WAIT：挂到本会话队尾，等前一个 wait 执行完
    task = asyncio.current_task()
    queue = _queues.setdefault(msg.conversation_id, [])
    prev = queue[-1] if queue else None
    queue.append(task)

    if prev is not None and not prev.done():
        yield make_event(
            msg.conversation_id,
            "run.queued",
            {"request_id": msg.request_id, "operation": msg.operation},
        )

        try:
            await prev
        except asyncio.CancelledError:
            # 前驱被取消则链条断掉，本命令未执行即结束
            yield make_receipt_error(
                msg.request_id,
                msg.conversation_id,
                ErrorCode.CANCELLED,
                "命令已受理，但在执行前被取消",
            )
            return
        except Exception:
            pass

    try:
        async for chunk in _executor.command_executor(
            msg.request_id, msg.operation, msg.conversation_id, msg.parameters,
        ):
            yield chunk

    finally:
        queue = _queues.get(msg.conversation_id)

        if queue is not None and task in queue:
            queue.remove(task)
            if not queue:
                _queues.pop(msg.conversation_id, None)
