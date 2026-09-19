from __future__ import annotations

from typing import Any

from . import _state


async def list_skills() -> list[dict[str, Any]]:
    """列出工作区中所有已加载的 skill。"""
    ws, _ = await _state.workspace_manager.ensure_workspace()
    skills = await ws.list_skills()
    return [{"name": s.name, "description": s.description, "dir": s.dir} for s in skills]


async def list_mcp_servers() -> list[dict[str, Any]]:
    """列出所有已连接的 MCP 服务器及其工具数量。"""
    ws, _ = await _state.workspace_manager.ensure_workspace()
    mcps = await ws.list_mcps()
    result = []
    for client in mcps:
        tools = await client.list_tools()
        result.append({
            "name": client.name,
            "type": client.mcp_config.type,
            "tool_count": len(tools),
        })
    return result
