from __future__ import annotations

from typing import Any

from backend.user.model import MODEL_CONFIG_KEYS, model_config
from backend.user.permission import PERMISSION_CONFIG_KEYS, permission_config
from backend.workspace import WorkspaceManager

from . import _state


def get_config(key: str | None = None) -> dict[str, Any]:
    """读取配置；``key`` 为空则返回三块公开视图。"""

    sections = {
        "model": model_config.get(),
        "permission": permission_config.get(),
        "workspace": {
            "root": _state.workspace_manager.project_root,
            "agent_home": _state.workspace_manager.agent_home,
        },
    }

    if key is None:
        return sections
    if key in sections:
        return {key: sections[key]}
    if key in MODEL_CONFIG_KEYS:
        return {key: sections["model"].get(key)}
    if key in PERMISSION_CONFIG_KEYS:
        return {key: sections["permission"].get(key)}
    if key in ("root", "agent_home"):
        return {key: sections["workspace"].get(key)}

    raise KeyError(f"unknown config key: {key!r}")


def set_config(key: str, value: Any) -> dict[str, Any]:
    """按 KEYS 路由热更新；model/permission 挂载到全部活跃会话。"""
    if key in MODEL_CONFIG_KEYS:
        result = model_config.update(**{key: value})
        for agent in _state.workspace_manager.session_manager.live_agents():
            model_config.update(agent=agent, **{key: value})
        return result

    if key in PERMISSION_CONFIG_KEYS:
        result = permission_config.update(**{key: value})
        for agent in _state.workspace_manager.session_manager.live_agents():
            permission_config.update(agent=agent, **{key: value})
        return result

    raise KeyError(f"unknown config key: {key!r}")


async def switch_workspace(root: str) -> dict[str, Any]:
    """硬边界变更：换新工作区，旧的关沙箱清会话（磁盘存档原地保留）。"""
    new_ws = WorkspaceManager(root)
    old = _state.workspace_manager
    _state.workspace_manager = new_ws
    await old.close_workspace()
    return {
        "root": new_ws.project_root,
        "agent_home": new_ws.agent_home,
    }
