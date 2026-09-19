"""集成测试：真实 handler 穿过 register、scheduler、executor。不调用模型。"""

import asyncio

import pytest
from pydantic import BaseModel

import handlers  # noqa: F401  确保真实操作已注册
from proxy_layer.register import register_command
from proxy_layer.schema import CommandMode, ErrorCode, make_command
from proxy_layer.scheduler import submit
import proxy_layer.scheduler as scheduler


class _Params(BaseModel):
    pass


@pytest.fixture(autouse=True)
def clean_scheduler():
    import proxy_layer.register as register

    scheduler._queues.clear()
    register._registry.pop("unit", None)
    yield
    for queue in list(scheduler._queues.values()):
        for task in queue:
            if not task.done():
                task.cancel()
    scheduler._queues.clear()
    register._registry.pop("unit", None)


def _drive(operation, parameters=None, conversation_id="c-int", request_id="req-1"):
    command = make_command(request_id, conversation_id, operation, parameters or {})

    async def collect():
        return [chunk async for chunk in submit(command)]

    return asyncio.run(collect())


def test_config_get_returns_permission_mode():
    chunks = _drive("config.get", {"key": "mode"})

    assert len(chunks) == 1
    assert chunks[0].accepted is True
    assert "mode" in chunks[0].result


def test_config_set_rejects_missing_and_unknown_keys():
    missing = _drive("config.set", {})
    unknown = _drive("config.set", {"key": "nope", "value": 1})

    assert missing[0].error_code == ErrorCode.INVALID_PARAMETERS
    assert unknown[0].error_code == ErrorCode.INTERNAL_ERROR
    assert "unknown config key" in unknown[0].error_message


def test_unknown_operation_is_rejected_by_the_pipeline():
    chunks = _drive("unit.missing")

    assert chunks[0].error_code == ErrorCode.UNKNOWN_OPERATION


def test_chat_interrupt_cancels_a_waiting_command():
    started = asyncio.Event()

    async def blocked(params: _Params):
        started.set()
        await asyncio.Event().wait()

    register_command("unit", "blocked", CommandMode.WAIT)(blocked)

    async def collect(operation, request_id):
        command = make_command(request_id, "c-int", operation)
        return [chunk async for chunk in submit(command)]

    async def body():
        waiting = asyncio.create_task(collect("unit.blocked", "req-wait"))
        await started.wait()
        stopped = await collect("chat.interrupt", "req-stop")
        with pytest.raises(asyncio.CancelledError):
            await waiting
        return stopped

    stopped = asyncio.run(body())

    assert stopped[0].accepted is True
    assert stopped[0].result["interrupted"] is True
    assert stopped[0].result["conversation_id"] == "c-int"
