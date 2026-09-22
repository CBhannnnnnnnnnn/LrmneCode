from __future__ import annotations

from typing import Any

from agentscope.credential import CredentialFactory

from backend.user.model import MODEL_CONFIG_KEYS, model_config
from backend.user.permission import PERMISSION_CONFIG_KEYS, permission_config
from backend.workspace import WorkspaceManager

from . import _state


def list_providers() -> list[dict[str, Any]]:
    """可选的凭证提供方及其字段 Schema，供前端生成配置表单。

    每条是 CredentialFactory 的 JSON Schema：``properties`` 给字段与默认值，
    ``required`` 给必填项，``properties.type.const`` 为 provider_type。
    """
    return CredentialFactory.list_schemas()


def get_config(key: str | None = None) -> dict[str, Any]:
    """读取配置；``key`` 为空则返回三块公开视图。"""

    sections = {
        "model": model_config.get(),
        "permission": permission_config.get(),
        "workspace": {
            "root": _state.workspace_manager.project_root,
            "agent_home": _state.workspace_manager.code_home,
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


def apply_model(
    model: str,
    provider_type: str | None = None,
    credential: dict[str, Any] | None = None,
    thinking_level: str | None = None,
    context_size: int | None = None,
) -> dict[str, Any]:
    """一次性写入模型相关配置。

    ``provider_type`` 缺省表示沿用当前提供方与凭证——这样「只换模型名」不必
    重填 API key。多个字段合成一次 update，避免分步写入时走中间态（_save 在
    provider 或 model 缺一时会丢掉整段 model 配置）。
    """
    patch: dict[str, Any] = {"model": model}
    if thinking_level is not None:
        patch["thinking_level"] = thinking_level
    if context_size is not None:
        patch["context_size"] = context_size
    if provider_type is not None:
        patch["provider"] = {"type": provider_type, **(credential or {})}

    result = model_config.update(**patch)
    for agent in _state.workspace_manager.session_manager.live_agents():
        model_config.update(agent=agent, **patch)
    return result


async def switch_workspace(root: str) -> dict[str, Any]:
    """硬边界变更：换新工作区，旧的关沙箱清会话（磁盘存档原地保留）。"""
    new_ws = WorkspaceManager(root)
    old = _state.workspace_manager
    _state.workspace_manager = new_ws
    await old.close_workspace()
    return {
        "root": new_ws.project_root,
        "agent_home": new_ws.code_home,
    }
