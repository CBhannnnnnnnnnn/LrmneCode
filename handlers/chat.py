from proxy_layer.register import chat_register
from proxy_layer.schema import CommandMode
from proxy_layer.session import conversation_id_var
from proxy_layer.scheduler import cancel_pending
from backend.adapter import stream_chat

from pydantic import BaseModel
from typing import Any, AsyncIterator


class Attachment(BaseModel):
    name: str
    media_type: str
    data: str  # base64 编码内容

 
class ChatSendParams(BaseModel):
    text: str
    attachments: list[Attachment] = []


class ChatInterruptParams(BaseModel):
    pass


@chat_register("send", mode=CommandMode.WAIT)
async def chat_send(params: ChatSendParams) -> AsyncIterator[dict[str, Any]]:
    """WAIT：流式对话；产出为事件 dict，由 Runtime 包装成协议帧。"""
    async for chunk in stream_chat(
        text=params.text,
        attachments=[a.model_dump() for a in params.attachments],
    ):
        yield chunk


@chat_register("interrupt", mode=CommandMode.CONTROL)
def chat_interrupt(params: ChatInterruptParams) -> dict[str, Any]:
    """CONTROL：取消本会话在途与排队的 wait 命令。"""
    cid = conversation_id_var.get() or ""
    cancel_pending(cid)
    
    return {"interrupted": True, "conversation_id": cid}
