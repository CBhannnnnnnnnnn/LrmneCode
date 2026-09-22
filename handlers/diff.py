from proxy_layer.register import diff_register
from backend import adapter

from pydantic import BaseModel

class DiffShowParams(BaseModel):
    round: int | None = None


class DiffUndoParams(BaseModel):
    round: int | None = None


class DiffListParams(BaseModel):
    pass


@diff_register("show")
async def diff_show(params: DiffShowParams):
    """对照快照与当前文件生成 unified diff；round 缺省为最新一轮。"""
    return adapter.diff_show(params.round)


@diff_register("undo")
async def diff_undo(params: DiffUndoParams):
    """按轮次把快照写回磁盘；round 缺省为最新一轮。"""
    return adapter.diff_undo(params.round)


@diff_register("list")
async def diff_list(params: DiffListParams):
    """列出本会话有快照文件的轮次。"""
    return adapter.diff_list()
