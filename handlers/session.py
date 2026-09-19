from proxy_layer.register import session_register
from backend.adapter import list_sessions, resume_session, delete_session

from pydantic import BaseModel


class SessionListParams(BaseModel):
    pass


class SessionResumeParams(BaseModel):
    source_cid: str


class SessionDeleteParams(BaseModel):
    cid: str


@session_register("list")
async def session_list(params: SessionListParams):
    """列出已持久化会话元数据。"""
    return list_sessions()


@session_register("resume")
async def session_resume(params: SessionResumeParams):
    """把 source_cid 的状态恢复到当前 conversation_id。"""
    return resume_session(params.source_cid)


@session_register("delete")
async def session_delete(params: SessionDeleteParams):
    """删除指定会话的磁盘存档。"""
    return delete_session(params.cid)
