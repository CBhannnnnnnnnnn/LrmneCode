import inspect
import asyncio

from .schema import (
    ErrorCode,
    make_event,
    make_receipt_error,
    make_receipt_success,
    build_error_message,
)

from proxy_layer.register import lookup
from pydantic import ValidationError
from typing import AsyncGenerator
from proxy_layer.session import conversation_id_var


class Executor:
    """按 handler 类型执行已注册操作，不负责排队与取消。

    生成器：先回成功回执，再转发产出，最后发 run.finished。
    普通函数：只回一条带 result 的成功回执（或 INTERNAL_ERROR）。
    """

    async def command_executor(
        self,
        request_id: str,
        namespace_operation: str,
        conversation_id: str,
        params: dict,
    ) -> AsyncGenerator:

        operation = lookup(namespace_operation)

        if operation is None:
            yield make_receipt_error(
                request_id,
                conversation_id,
                ErrorCode.UNKNOWN_OPERATION,
                f"操作 {namespace_operation} 未注册",
            )
            return

        try:
            validated = operation["params_model"].model_validate(params)
        except ValidationError as e:

            yield make_receipt_error(
                request_id,
                conversation_id,
                ErrorCode.INVALID_PARAMETERS,
                build_error_message(e),
            )
            return

        conversation_id_var.set(conversation_id)

        handler = operation["handler"]

        if inspect.isasyncgenfunction(handler) or inspect.isgeneratorfunction(handler):
            yield make_receipt_success(request_id, conversation_id)

            # 同步生成器没有异步迭代协议，包一层再统一 async for
            if inspect.isgeneratorfunction(handler):

                async def _stream():
                    for chunk in handler(validated):
                        yield chunk
                result = _stream()
            else:
                result = handler(validated)

            try:

                async for chunk in result:
                    yield chunk

            except asyncio.CancelledError:
                yield make_event(
                    conversation_id,
                    "run.finished",
                    {"stop_reason": "interrupted", "request_id": request_id},
                )
                raise

            except Exception as e:
                yield make_event(
                    conversation_id,
                    "run.finished",
                    {"stop_reason": "error", "error": str(e), "request_id": request_id},
                )
                return

            yield make_event(
                conversation_id,
                "run.finished",
                {"stop_reason": "completed", "request_id": request_id},
            )
            return

        try:
            if operation["is_async"]:
                result = await handler(validated)
            else:
                result = await asyncio.to_thread(handler, validated)
            yield make_receipt_success(
                request_id, conversation_id, result=result
            )
        except Exception as e:
            yield make_receipt_error(
                request_id,
                conversation_id,
                ErrorCode.INTERNAL_ERROR,
                str(e),
            )
