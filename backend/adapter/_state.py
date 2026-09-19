from __future__ import annotations

import asyncio

from agentscope.event import RequireUserConfirmEvent, UserConfirmResultEvent

from backend.workspace import workspace_manager

# 包内共享：审批挂起表 + 当前工作区（switch_workspace 只改此绑定）
_pending_approvals: dict[
    str,
    tuple[RequireUserConfirmEvent, asyncio.Future[UserConfirmResultEvent]],
] = {}

__all__ = ["_pending_approvals", "workspace_manager"]
