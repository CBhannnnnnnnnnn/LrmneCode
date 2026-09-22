from proxy_layer.register import diff_register
from backend.adapter import (
    diff_list as adapter_diff_list,
    diff_show as adapter_diff_show,
    diff_undo as adapter_diff_undo,
)

from pydantic import BaseModel
from typing import Any


class DiffShowParams(BaseModel):
    round: int | None = None


class DiffUndoParams(BaseModel):
    round: int | None = None


class DiffListParams(BaseModel):
    pass


@diff_register("show")
def diff_show(params: DiffShowParams) -> dict[str, Any]:
    """对照快照与当前文件生成 unified diff；round 缺省为最新一轮。"""
    return adapter_diff_show(params.round)


@diff_register("undo")
def diff_undo(params: DiffUndoParams) -> dict[str, Any]:
    """按轮次把快照写回磁盘；round 缺省为最新一轮。"""
    return adapter_diff_undo(params.round)


@diff_register("list")
def diff_list(params: DiffListParams) -> dict[str, Any]:
    """列出本会话有快照文件的轮次。"""
    return adapter_diff_list()
