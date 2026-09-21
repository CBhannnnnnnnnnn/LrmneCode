"""审批回执：「允许并记住」把引擎建议的放行规则一并回灌，而不是改全局权限模式。"""

import asyncio

from agentscope.event import RequireUserConfirmEvent
from agentscope.message import ToolCallBlock
from agentscope.permission import PermissionBehavior, PermissionRule

from backend.adapter import _state
from backend.adapter.approval import respond_approval


def _resolve(approved: bool, always: bool):
    """造一条挂着审批的引擎建议规则，走一次回执，返回 (回执, 解挂事件)。"""

    async def body():
        rule = PermissionRule(
            tool_name="PowerShell",
            rule_content="Get-ChildItem:*",
            behavior=PermissionBehavior.ALLOW,
            source="suggestion",
        )
        call = ToolCallBlock(
            id="t1",
            name="PowerShell",
            input='{"command": "Get-ChildItem -Force"}',
            suggested_rules=[rule],
        )
        req = RequireUserConfirmEvent(reply_id="a1", tool_calls=[call])
        fut = asyncio.get_running_loop().create_future()
        _state._pending_approvals["a1"] = (req, fut)
        try:
            receipt = respond_approval("a1", approved, always)
            return receipt, await fut
        finally:
            _state._pending_approvals.pop("a1", None)

    return asyncio.run(body())


def test_always_sends_the_engines_suggested_rules_back():
    receipt, result = _resolve(True, True)

    (confirmation,) = result.confirm_results
    assert confirmation.confirmed is True
    assert [rule.rule_content for rule in confirmation.rules] == ["Get-ChildItem:*"]
    assert receipt["remembered"] == 1


def test_plain_approve_remembers_nothing():
    receipt, result = _resolve(True, False)

    (confirmation,) = result.confirm_results
    assert confirmation.rules == []
    assert receipt["remembered"] == 0


def test_deny_never_remembers_even_when_asked():
    _, result = _resolve(False, True)

    (confirmation,) = result.confirm_results
    assert confirmation.confirmed is False
    assert confirmation.rules == []
