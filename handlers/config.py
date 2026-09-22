from proxy_layer.register import config_register
from proxy_layer.scheduler import cancel_all
from proxy_layer.schema import CommandMode

from backend.adapter import apply_model, get_config, list_providers, set_config, switch_workspace
from pydantic import BaseModel
from typing import Any


class ConfigSetParams(BaseModel):
    key: str
    value: Any

class ConfigGetParams(BaseModel):
    key: str | None = None

class ConfigProvidersParams(BaseModel):
    """列出可选提供方，无参数。"""

class ConfigApplyModelParams(BaseModel):
    model: str
    provider_type: str | None = None
    credential: dict[str, Any] = {}
    thinking_level: str | None = None
    context_size: int | None = None



@config_register("set")
async def config_set(params: ConfigSetParams) -> dict[str, Any]:
    """写入配置。key=root 为工作区硬边界：先 cancel_all 再切换。"""

    if params.key == "root":
        cancel_all()
        return await switch_workspace(params.value)
    set_config(params.key, params.value)
    return {"key": params.key, "value": params.value}


@config_register("get")
async def config_get(params: ConfigGetParams) -> dict[str, Any]:
    """读取配置；key 为空返回 model / permission / workspace 三块视图。"""
    return get_config(params.key)


@config_register("providers")
async def config_providers(params: ConfigProvidersParams) -> list[dict[str, Any]]:
    """可选提供方及其字段 Schema；前端据此生成凭证表单。"""
    return list_providers()


@config_register("apply_model")
async def config_apply_model(params: ConfigApplyModelParams) -> dict[str, Any]:
    """一次写入模型配置；provider_type 为空表示沿用当前提供方与凭证。"""
    return apply_model(
        model=params.model,
        provider_type=params.provider_type,
        credential=params.credential,
        thinking_level=params.thinking_level,
        context_size=params.context_size,
    )

