from proxy_layer.register import config_register
from proxy_layer.scheduler import cancel_all
from proxy_layer.schema import CommandMode

from backend.adapter import set_config, get_config, switch_workspace
from pydantic import BaseModel
from typing import Any


class ConfigSetParams(BaseModel):
    key: str
    value: Any

class ConfigGetParams(BaseModel):
    key: str | None = None



@config_register("set")
async def config_set(params: ConfigSetParams):
    """写入配置。key=root 为工作区硬边界：先 cancel_all 再切换。"""

    if params.key == "root":
        cancel_all()
        return await switch_workspace(params.value)
    set_config(params.key, params.value)
    return {"key": params.key, "value": params.value}


@config_register("get")
async def config_get(params: ConfigGetParams):
    """读取配置；key 为空返回 model / permission / workspace 三块视图。"""
    return get_config(params.key)

