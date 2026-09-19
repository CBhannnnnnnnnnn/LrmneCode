from __future__ import annotations

from typing import TYPE_CHECKING

from agentscope.agent import Agent, ReActConfig
from agentscope.state import AgentState

from backend.developer.prompt import get_name, get_system_prompt
from backend.user.model import model_config
from backend.user.permission import permission_config

if TYPE_CHECKING:
    from backend.workspace import WorkspaceManager


async def build_agent(workspace: WorkspaceManager, state: AgentState | None = None) -> Agent:
    """按当前配置组装 Agent；state 为空即全新会话。"""
    if not model_config.get().get("configured"):
        raise RuntimeError(
            "model is not configured: set provider and model first",
        )

    ws, toolkit = await workspace.ensure_workspace()
    chat_model = model_config._build_model()

    agent = Agent(
        name=get_name(),
        system_prompt=get_system_prompt(),
        model=chat_model,
        toolkit=toolkit,
        offloader=ws,
        react_config=ReActConfig(interruption_raise_cancelled_error=True),
        state=state,
    )

    permission_config.update(agent=agent)
    workspace.mount_directories(agent)

    return agent
