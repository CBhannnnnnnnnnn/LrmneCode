import json
import itertools
import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

PROTOCOL_VERSION = "version-1"

class CommandMode(StrEnum):
    PLAIN = "plain"  # 直通执行，不参与排队
    WAIT = "wait"  # 按会话 FIFO 排队，等前序 wait 命令执行完
    CONTROL = "control"  # 管理动作：不排队，执行前先中断本会话的在途与排队命令

class MessageType(StrEnum):
    """前后端的通信方式。该字段固定永不添加新的通信方式。"""

    COMMAND = "command"
    RECEIPT = "receipt"
    EVENT = "event"


class ErrorCode(StrEnum):
    """代理层中的错误码和业务无关，用于返回一次命令执行失败的原因，只出现在回执中"""


    INVALID_MESSAGE = "invalid_message"  # 收到的整行消息无法解释：不是合法 JSON，或缺少必要字段
    UNKNOWN_OPERATION = "unknown_operation"  # 操作名没有注册
    INVALID_PARAMETERS = "invalid_parameters"  # 参数缺失或类型不符

    BUSY = "busy"  # 该操作要求空闲，但当前正在运行
    CANCELLED = "cancelled"  # 命令已被受理但尚未执行
    
    INTERNAL_ERROR = "internal_error"  # 命令执行中发生其他异常


class BaseProtocol(BaseModel):
    """三种消息共有的通用字段"""

    message_type: MessageType
    protocol_version: str = PROTOCOL_VERSION
    conversation_id: str | None


class CommandProtocol(BaseProtocol):
    """前端 → 后端。parameters 必带：无参操作显式传空 dict。"""

    message_type: Literal[MessageType.COMMAND] = MessageType.COMMAND
    request_id: str
    operation: str
    parameters: dict[str, Any]


class EventProtocol(BaseProtocol):
    """后端 → 前端，单向推送，无配对无回执。"""

    message_type: Literal[MessageType.EVENT] = MessageType.EVENT
    event: str
    data: dict[str, Any]


class ReceiptProtocol(BaseProtocol):
    """
    成功 = accepted=True，error_code/error_message 必须为 None。
    失败 = accepted=False，error_code 必填，result 必须为 None。
    """

    message_type: Literal[MessageType.RECEIPT] = MessageType.RECEIPT
    request_id: str | None
    accepted: bool
    error_code: ErrorCode | None = None
    error_message: str | None = None

    result: Any = None  # 成功时可携带任意同步返回值

    @model_validator(mode="after")
    def _check_receipt_shape(self) -> "ReceiptProtocol":
        if self.accepted:
            if self.error_code is not None:
                raise ValueError("accepted=True 的回执不得携带 error_code")
            if self.error_message is not None:
                raise ValueError("accepted=True 的回执不得携带 error_message")
        else:
            if self.error_code is None:
                raise ValueError("accepted=False 的回执必须给出 error_code")
            if self.result is not None:
                raise ValueError("accepted=False 的回执不得携带 result")
        return self


Message = Annotated[
    Union[CommandProtocol, ReceiptProtocol, EventProtocol],
    Field(discriminator="message_type"),
]

message_adapter: TypeAdapter[Message] = TypeAdapter(Message)


def from_line(line: str) -> BaseProtocol:
    """把一行 JSON 解析成三种协议消息之一。

    失败时抛 ValidationError
    错误码由调用方按场景映射：整行格式解析失败 → invalid_message；
    参数校验失败 → invalid_parameters。

    """
    return message_adapter.validate_json(line)


def build_error_message(exc: ValidationError) -> str:

    """构造回执的 error_message：把 ValidationError 转为可读。"""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<根级>"
        parts.append(f"{loc}: {err['msg']}")
    return "；".join(parts)


def new_conversation_id() -> str:
    """生成会话 id：基于 uuid4，前端进程启动时调用一次。后端不生成、不校验、只透传。"""
    return f"c-{uuid.uuid4().hex[:8]}"


_request_counter = itertools.count(1)


def new_request_id() -> str:
    """生成命令 id：模块级计数器自增（req-1、req-2、…），天然满足"本次运行内唯一"。

    仅前端应调用；后端只回显 request_id，不生成。
    """
    return f"req-{next(_request_counter)}"


def make_command(
    request_id: str,
    conversation_id: str,
    operation: str,
    parameters: dict[str, Any] | None = None,
) -> CommandProtocol:
    """构造命令。parameters 缺省为空 dict。"""
    return CommandProtocol(
        request_id=request_id,
        conversation_id=conversation_id,
        operation=operation,
        parameters=parameters if parameters is not None else {},
    )


def make_receipt_success(
    request_id: str,
    conversation_id: str,
    result: Any = None,
) -> ReceiptProtocol:
    """构造受理成功的回执，可携带命令的同步直接返回值。"""
    return ReceiptProtocol(
        request_id=request_id,
        conversation_id=conversation_id,
        accepted=True,
        result=result,
    )


def make_receipt_error(
    request_id: str | None,
    conversation_id: str,
    error_code: ErrorCode,
    error_message: str | None = None,
) -> ReceiptProtocol:
    """构造受理失败的回执。request_id 传 None 仅用于 invalid_message。"""
    return ReceiptProtocol(
        request_id=request_id,
        conversation_id=conversation_id,
        accepted=False,
        error_code=error_code,
        error_message=error_message,
    )

def make_event(
    conversation_id: str,
    event: str,
    data: dict[str, Any] | None = None,
) -> EventProtocol:
    """构造事件。data 缺省为空 dict。"""
    return EventProtocol(
        conversation_id=conversation_id,
        event=event,
        data=data if data is not None else {},
    )


def to_line(msg: BaseProtocol) -> str:
    """序列化为一行协议消息"""
    return json.dumps(msg.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
