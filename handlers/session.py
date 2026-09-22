from proxy_layer.register import session_register
from backend.adapter import list_sessions, resume_session, delete_session

from pydantic import BaseModel
from typing import Any


class SessionListParams(BaseModel):
    pass


class SessionResumeParams(BaseModel):
    source_cid: str


class SessionDeleteParams(BaseModel):
    cid: str


@session_register("list")
def session_list(params: SessionListParams) -> list[dict[str, Any]]:
    """列出已持久化会话元数据。"""
    return list_sessions()


@session_register("resume")
def session_resume(params: SessionResumeParams) -> dict[str, Any]:
    """把 source_cid 的状态恢复到当前 conversation_id。"""
    return resume_session(params.source_cid)


@session_register("delete")
def session_delete(params: SessionDeleteParams) -> dict[str, Any]:
    """删除指定会话的磁盘存档。"""
    return delete_session(params.cid)
