from __future__ import annotations
from typing import Any
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from . import _state


def respond_approval(
    approval_request_id: str,
    approved: bool,

) -> dict[str, Any]:
    """解挂 ``stream_chat`` 中等待的审批；无挂起或已解挂则报错。"""

    entry = _state._pending_approvals.get(approval_request_id)
    if entry is None:
        raise KeyError(
            f"no pending approval: {approval_request_id!r}",
        )

    req, fut = entry
    if fut.done():
        raise RuntimeError(
            f"approval already resolved: {approval_request_id!r}",
        )

    results = [
        ConfirmResult(confirmed=approved, tool_call=tc)
        for tc in req.tool_calls
    ]
    fut.set_result(
        UserConfirmResultEvent(
            reply_id=req.reply_id,
            confirm_results=results,
        ),
    )
    return {
        "approval_request_id": approval_request_id,
        "approved": approved,
    }
