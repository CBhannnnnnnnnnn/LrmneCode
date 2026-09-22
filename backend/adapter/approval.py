from __future__ import annotations
from typing import Any
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from . import _state


def respond_approval(
    approval_request_id: str,
    approved: bool,
    always: bool = False,
) -> dict[str, Any]:
    """解挂 ``stream_chat`` 中等待的审批；无挂起或已解挂则报错。

    ``always`` 把引擎随审批给出的建议规则回灌进去（发 ``RequireUserConfirmEvent``
    之前就写好了 ``tool_call.suggested_rules``：Bash 取命令前缀、文件类取目录），
    于是同类调用不再询问。这比切全局权限模式准：只放行这一类，也不动用户配的 mode。
    """

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

    remember = approved and always
    results = []
    added = 0
    for tc in req.tool_calls:
        rules = list(tc.suggested_rules or []) if remember else []
        added += len(rules)
        results.append(
            ConfirmResult(confirmed=approved, tool_call=tc, rules=rules),
        )

    fut.set_result(
        UserConfirmResultEvent(
            reply_id=req.reply_id,
            confirm_results=results,
        ),
    )
    return {
        "approval_request_id": approval_request_id,
        "approved": approved,
        "remembered": added,
    }
