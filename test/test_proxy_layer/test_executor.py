"""executor 单元测试：只锁「怎么执行已注册函数」，不测排队。"""

import asyncio

import pytest
from pydantic import BaseModel

from proxy_layer.executor import Executor
from proxy_layer.register import register_command
from proxy_layer.schema import CommandMode, ErrorCode, MessageType
from proxy_layer.session import conversation_id_var


class TextParams(BaseModel):
    text: str


@pytest.fixture(autouse=True)
def isolated_unit_namespace():
    import proxy_layer.register as register

    register._registry.pop("unit", None)
    yield
    register._registry.pop("unit", None)


def _drive(coro):
    return asyncio.run(coro)


def _register(name, func, mode=CommandMode.PLAIN):
    register_command("unit", name, mode)(func)


def _run(name, params=None, request_id="req-1", conversation_id="c-1"):
    async def collect():
        chunks = []
        async for chunk in Executor().command_executor(
            request_id,
            f"unit.{name}",
            conversation_id,
            {} if params is None else params,
        ):
            chunks.append(chunk)
        return chunks

    return _drive(collect())


def test_unknown_operation_returns_error_receipt():
    chunks = _run("missing")

    assert len(chunks) == 1
    assert chunks[0].message_type == MessageType.RECEIPT
    assert chunks[0].accepted is False
    assert chunks[0].error_code == ErrorCode.UNKNOWN_OPERATION
    assert "unit.missing" in chunks[0].error_message


def test_invalid_parameters_return_error_and_do_not_call_handler():
    called = False

    def echo(params: TextParams):
        nonlocal called
        called = True
        return params.text

    _register("echo", echo)

    chunks = _run("echo", {})

    assert called is False
    assert chunks[0].accepted is False
    assert chunks[0].error_code == ErrorCode.INVALID_PARAMETERS


def test_invalid_parameters_do_not_publish_conversation_id():
    def echo(params: TextParams):
        return params.text

    _register("echo", echo)

    async def collect():
        token = conversation_id_var.set("before")
        try:
            async for _chunk in Executor().command_executor(
                "req-1", "unit.echo", "c-1", {}
            ):
                pass
            return conversation_id_var.get()
        finally:
            conversation_id_var.reset(token)

    assert _drive(collect()) == "before"


def test_sync_handler_returns_result_and_publishes_conversation_id():
    seen = {}

    def echo(params: TextParams):
        seen["cid"] = conversation_id_var.get()
        return params.text

    _register("echo", echo)

    chunks = _run("echo", {"text": "hi"}, conversation_id="c-9")

    assert seen["cid"] == "c-9"
    assert len(chunks) == 1
    assert chunks[0].accepted is True
    assert chunks[0].result == "hi"
    assert chunks[0].error_code is None


def test_async_handler_returns_result():
    async def echo(params: TextParams):
        return params.text.upper()

    _register("echo", echo)

    chunks = _run("echo", {"text": "hi"})

    assert chunks[0].accepted is True
    assert chunks[0].result == "HI"


def test_handler_exception_becomes_internal_error():
    def echo(params: TextParams):
        raise RuntimeError("boom")

    _register("echo", echo)

    chunks = _run("echo", {"text": "hi"})

    assert chunks[0].accepted is False
    assert chunks[0].error_code == ErrorCode.INTERNAL_ERROR
    assert chunks[0].error_message == "boom"


def test_async_generator_emits_receipt_chunks_then_completed():
    async def stream(params: TextParams):
        yield {"event": "token", "data": {"text": params.text}}

    _register("stream", stream)

    chunks = _run("stream", {"text": "hi"})

    assert chunks[0].accepted is True
    assert chunks[0].result is None
    assert chunks[1] == {"event": "token", "data": {"text": "hi"}}
    assert chunks[2].event == "run.finished"
    assert chunks[2].data["stop_reason"] == "completed"
    assert chunks[2].data["request_id"] == "req-1"


def test_sync_generator_is_wrapped_as_stream():
    def stream(params: TextParams):
        yield {"event": "token", "data": {"text": params.text}}

    _register("stream", stream)

    chunks = _run("stream", {"text": "hi"})

    assert [type(chunk).__name__ for chunk in chunks] == [
        "ReceiptProtocol",
        "dict",
        "EventProtocol",
    ]
    assert chunks[2].data["stop_reason"] == "completed"


def test_generator_exception_finishes_with_error_and_stops():
    async def stream(params: TextParams):
        yield {"event": "token", "data": {}}
        raise RuntimeError("fail")

    _register("stream", stream)

    chunks = _run("stream", {"text": "hi"})

    assert chunks[-1].event == "run.finished"
    assert chunks[-1].data["stop_reason"] == "error"
    assert chunks[-1].data["error"] == "fail"
    assert [getattr(chunk, "event", None) for chunk in chunks].count("run.finished") == 1


def test_generator_cancelled_error_finishes_interrupted_then_reraises():
    async def stream(params: TextParams):
        yield {"event": "token", "data": {}}
        raise asyncio.CancelledError()

    _register("stream", stream)

    async def collect():
        items = []
        with pytest.raises(asyncio.CancelledError):
            async for chunk in Executor().command_executor(
                "req-1", "unit.stream", "c-1", {"text": "hi"}
            ):
                items.append(chunk)
        return items

    chunks = _drive(collect())

    assert chunks[-1].event == "run.finished"
    assert chunks[-1].data == {"stop_reason": "interrupted", "request_id": "req-1"}
