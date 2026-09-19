from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from agentscope.event import RequireUserConfirmEvent, UserConfirmResultEvent
from agentscope.message import UserMsg, TextBlock, DataBlock, Base64Source
from agentscope.state import AgentState

from backend.factory import build_agent
from backend.snapshot import SnapshotManager
from proxy_layer.session import conversation_id_var

from . import _state
from .translate import translate


async def stream_chat(text: str, attachments: list[dict] | None = None) -> AsyncIterator[dict[str, Any]]:
    """取（或建）会话 Agent，流式 ``reply_stream``。

    遇审批挂起，等 ``respond_approval`` 解挂后继续；run 结束（含中断/出错）回存状态。
    产出为 translate 后的 ``{event, data}``；未知事件不透传。
    """

    cid = conversation_id_var.get() or "c-local"  # 无 ContextVar 时（如直调）的兜底
    session = _state.workspace_manager.session_manager

    agent = session.get(cid)
    if agent is None:
        state = session.load_state(cid) or AgentState()
        state.session_id = cid  # 会话身份只在此处绑定
        agent = await build_agent(_state.workspace_manager, state)
        session.bind(cid, agent)

    snapshot = SnapshotManager(
        _state.workspace_manager.agent_home,
        cid,
        _state.workspace_manager.project_root,
    )
    snapshot.begin_round()

    try:
        blocks: list[TextBlock | DataBlock] = [TextBlock(text=text)]
        for att in attachments or []:
            blocks.append(
                DataBlock(
                    source=Base64Source(
                        data=att["data"],
                        media_type=att["media_type"],
                    ),
                    name=att["name"],
                )
            )
        inputs: Any = UserMsg(name="user", content=blocks)

        while True:

            pending: RequireUserConfirmEvent | None = None

            async for event in agent.reply_stream(inputs):

                if isinstance(event, RequireUserConfirmEvent):
                    pending = event
                    loop = asyncio.get_running_loop()

                    fut: asyncio.Future[UserConfirmResultEvent] = (
                        loop.create_future()

                    )
                    _state._pending_approvals[event.reply_id] = (event, fut)

                wire = translate(event)

                # None：该事件不透传；要支持需扩展 translate
                if wire is not None:
                    yield wire


            if pending is None:
                break

            aid = pending.reply_id

            try:
                inputs = await _state._pending_approvals[aid][1]

            finally:
                _state._pending_approvals.pop(aid, None)

    finally:
        session.save_state(cid, agent.state)
