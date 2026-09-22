from proxy_layer.register import approval_register
from backend.adapter import respond_approval
from pydantic import BaseModel
from typing import Any


class ApprovalRespondParams(BaseModel):
    approval_request_id: str
    approved: bool
    always: bool = False


@approval_register("respond")
async def approval_respond(params: ApprovalRespondParams) -> dict[str, Any]:
    """解挂 chat.send 中等待的工具审批；``always`` 一并记住这类放行。"""
    return respond_approval(
        params.approval_request_id,
        params.approved,
        params.always,
    )
