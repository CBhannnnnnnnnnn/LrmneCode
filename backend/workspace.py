from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any,List

from agentscope.permission import AdditionalWorkingDirectory
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolBase
from agentscope.tool import Bash, Edit, Glob, Grep, PowerShell, Read, Write
from agentscope.workspace import LocalWorkspace, WorkspaceBase
from agentscope.agent import Agent


AGENT_HOME_NAME = ".lrmneagent"
SESSIONS_DIR = "sessions"


class ProjectLocalWorkspace(LocalWorkspace):
    """workdir 落在 .lrmneagent；工具 cwd 绑到项目源码根，而非 agent home。"""

    def __init__(self, project_root: str, **kwargs: Any):

        self.project_root = os.path.abspath(project_root) 
        agent_home = os.path.join(self.project_root, AGENT_HOME_NAME)

        super().__init__(workdir=agent_home, **kwargs)
        
        self.instructions = (
            f"<workspace>Project source root: {self.project_root}\n"
            f"Agent home (skills/mcp/sessions): {agent_home}\n"
            f"Edit and run commands against the project source root. "
            f"Treat {agent_home} as product metadata, not application code."
            f"</workspace>"
        )

    async def list_tools(self) -> List[ToolBase]:
        """覆盖默认 list_tools：shell/读写工具的工作目录指向 project_root。"""

        backend = self.get_backend() 

        if os.name == "nt":                         
            shell = PowerShell(cwd=self.project_root, backend=backend)
        else:         
            shell = Bash(cwd=self.project_root, backend=backend)

        glob_kwargs = {"backend": backend}
        if self._glob_helper_path is not None:
            glob_kwargs["glob_helper_path"] = self._glob_helper_path

        return [shell, Edit(backend=backend), Glob(**glob_kwargs),
                Grep(backend=backend), Read(backend=backend), Write(backend=backend)]


class SessionManager:
    """按 conversation_id（cid）持久化 AgentState，并缓存进程内 live agent。"""

    def __init__(self, agent_home: str):
        
        self._dir = Path(agent_home) / SESSIONS_DIR
        self._agents: dict[str, Agent] = {}

    def _state_path(self, cid: str) -> Path:
        return self._dir / cid / "state.json"

    def load_state(self, cid: str) -> AgentState | None:
        path = self._state_path(cid)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AgentState.model_validate(data)
        except (OSError, ValueError):
            return None

    def save_state(self, cid: str, state: AgentState) -> None:
        path = self._state_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False),
            encoding="utf-8",
        )
    
    def get(self, cid: str) -> Agent | None:
        return self._agents.get(cid)

    def bind(self, cid: str, agent: Agent) -> None:
        self._agents[cid] = agent

    def live_agents(self) -> list[Agent]:
        return list(self._agents.values())

    def drop_all(self) -> None:
        self._agents.clear()


class WorkspaceManager:

    def __init__(self, project_root: str | None = None):

        if project_root is None:
            project_root = os.getcwd()

        path = os.path.abspath(os.path.expanduser(project_root.strip()))
        self.project_root = path
        self.agent_home = os.path.join(self.project_root, AGENT_HOME_NAME)


        os.makedirs(self.agent_home, exist_ok=True)
        
        # WorkspaceBase.initialize 需 await，同步 __init__ 里只能先占位
        self.session_manager = SessionManager(self.agent_home)
        self._workspace: WorkspaceBase | None = None

        self._toolkit: Toolkit | None = None


    async def ensure_workspace(self) -> tuple[WorkspaceBase, Toolkit]:
        
        if self._workspace is None or not self._workspace.is_alive:
            await self._build_workspace()
        
        return self._workspace, self._toolkit


    async def close_workspace(self) -> None:
        
        ws = self._workspace
        if ws is None:
            return
        if ws.is_alive:
            await ws.close()

        ws.is_alive = False
        self._workspace = None
        self._toolkit = None


    async def _build_workspace(self) -> None:

        await self.close_workspace()
        ws = ProjectLocalWorkspace(self.project_root)

        # initialize 会拉起 MCP 等异步资源
        await ws.initialize()
        ws.is_alive = True

        self._workspace = ws
        self._toolkit = Toolkit(tools=await ws.list_tools(), 
                                skills_or_loaders=await ws.list_skills(),mcps=await ws.list_mcps())

    def mount_directories(self, agent: Agent) -> None:
        context = agent.state.permission_context
        context.working_directories[self.project_root] = AdditionalWorkingDirectory(
            path=self.project_root,
            source="userSettings",
        )


workspace_manager = WorkspaceManager()