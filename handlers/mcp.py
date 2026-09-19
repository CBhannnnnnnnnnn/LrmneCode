from proxy_layer.register import mcp_register
from backend.adapter import list_mcp_servers

from pydantic import BaseModel


class McpListParams(BaseModel):
    pass


@mcp_register("list")
async def mcp_list(params: McpListParams):
    """列出已连接的 MCP 服务器。"""
    return await list_mcp_servers()
