from proxy_layer.register import approval_register
from backend.adapter import respond_approval
from pydantic import BaseModel


class ApprovalRespondParams(BaseModel):
    approval_request_id: str
    approved: bool


@approval_register("respond")
async def approval_respond(params: ApprovalRespondParams):
    """解挂 chat.send 中等待的工具审批。"""
    return respond_approval(params.approval_request_id, 
                        params.approved)
