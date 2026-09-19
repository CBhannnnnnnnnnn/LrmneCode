"""scheduler 单元测试：只锁排队、取消和直通，handler 用假操作。"""

import asyncio

import pytest
from pydantic import BaseModel

from proxy_layer.register import register_command
from proxy_layer.schema import (
    CommandMode,
    ErrorCode,
    MessageType,
    make_command,
    make_receipt_success,
)
from proxy_layer.scheduler import cancel_all, cancel_pending, submit
import proxy_layer.scheduler as scheduler


class UnitParams(BaseModel):
    pass


@pytest.fixture(autouse=True)
def isolated_scheduler_state():
    import proxy_layer.register as register

    register._registry.pop("unit", None)
    scheduler._queues.clear()
    yield
    for queue in list(scheduler._queues.values()):
        for task in queue:
            if not task.done():
                task.cancel()
    scheduler._queues.clear()
    register._registry.pop("unit", None)


def _register(name, mode, func):
    register_command("unit", name, mode)(func)


async def _until(predicate, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("条件未在时限内成立")
        await asyncio.sleep(0.01)


def test_cancel_pending_cancels_only_that_conversation():
    async def hang():
        await asyncio.Event().wait()

    async def body():
        own = asyncio.create_task(hang())
        other = asyncio.create_task(hang())
        await asyncio.sleep(0)
        scheduler._queues["c-own"] = [own]
        scheduler._queues["c-other"] = [other]

        cancel_pending("c-own")
        cancel_pending("c-missing")

        assert own.cancelling()
        assert not other.cancelling()
        other.cancel()
        for task in (own, other):
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()

    asyncio.run(body())


def test_cancel_all_cancels_every_conversation():
    async def hang():
        await asyncio.Event().wait()

    async def body():
        first = asyncio.create_task(hang())
        second = asyncio.create_task(hang())
        await asyncio.sleep(0)
        scheduler._queues["c-1"] = [first]
        scheduler._queues["c-2"] = [second]

        cancel_all()

        assert first.cancelling()
        assert second.cancelling()
        for task in (first, second):
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()

    asyncio.run(body())


def test_plain_and_unknown_operation_do_not_queue():
    def ping(params: UnitParams):
        return "pong"

    _register("ping", CommandMode.PLAIN, ping)

    async def collect(operation):
        return [chunk async for chunk in submit(make_command("req-1", "c-1", operation))]

    plain = asyncio.run(collect("unit.ping"))
    missing = asyncio.run(collect("unit.missing"))

    assert plain[0].accepted is True
    assert plain[0].result == "pong"
    assert missing[0].error_code == ErrorCode.UNKNOWN_OPERATION
    assert scheduler._queues == {}


def test_plain_runs_while_wait_command_is_blocked():
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked(params: UnitParams):
        started.set()
        await release.wait()
        return "waited"

    def ping(params: UnitParams):
        return "pong"

    _register("blocked", CommandMode.WAIT, blocked)
    _register("ping", CommandMode.PLAIN, ping)

    async def collect(operation, request_id):
        cmd = make_command(request_id, "c-1", operation)
        return [chunk async for chunk in submit(cmd)]

    async def body():
        waiting = asyncio.create_task(collect("unit.blocked", "req-wait"))
        await started.wait()
        plain = await collect("unit.ping", "req-plain")
        assert plain[0].result == "pong"
        assert not waiting.done()
        release.set()
        waited = await waiting
        assert waited[0].result == "waited"
        assert scheduler._queues == {}

    asyncio.run(body())


def test_second_wait_command_stays_queued_until_first_finishes():
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def first(params: UnitParams):
        calls.append("first")
        started.set()
        await release.wait()
        return "first"

    async def second(params: UnitParams):
        calls.append("second")
        return "second"

    _register("first", CommandMode.WAIT, first)
    _register("second", CommandMode.WAIT, second)

    async def collect(operation, request_id):
        cmd = make_command(request_id, "c-1", operation)
        return [chunk async for chunk in submit(cmd)]

    async def body():
        first_task = asyncio.create_task(collect("unit.first", "req-1"))
        await started.wait()
        second_task = asyncio.create_task(collect("unit.second", "req-2"))
        await _until(lambda: len(scheduler._queues.get("c-1", [])) == 2)

        assert calls == ["first"]
        release.set()
        first_chunks, second_chunks = await asyncio.gather(first_task, second_task)

        assert calls == ["first", "second"]
        assert first_chunks[0].result == "first"
        assert second_chunks[0].message_type == MessageType.EVENT
        assert second_chunks[0].event == "run.queued"
        assert second_chunks[0].data["request_id"] == "req-2"
        assert second_chunks[-1].result == "second"
        assert scheduler._queues == {}

    asyncio.run(body())


def test_wait_command_is_cancelled_when_previous_task_is_cancelled():
    started = asyncio.Event()
    queued = asyncio.Event()
    calls = []

    async def first(params: UnitParams):
        started.set()
        await asyncio.Event().wait()

    async def second(params: UnitParams):
        calls.append("second")
        return "second"

    _register("first", CommandMode.WAIT, first)
    _register("second", CommandMode.WAIT, second)

    async def collect(operation, request_id, on_chunk=None):
        chunks = []
        async for chunk in submit(make_command(request_id, "c-1", operation)):
            chunks.append(chunk)
            if on_chunk is not None:
                on_chunk(chunk)
        return chunks

    async def body():
        first_task = asyncio.create_task(collect("unit.first", "req-1"))
        await started.wait()
        second_task = asyncio.create_task(
            collect(
                "unit.second",
                "req-2",
                on_chunk=lambda chunk: queued.set()
                if getattr(chunk, "event", None) == "run.queued"
                else None,
            )
        )
        await queued.wait()

        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        second_chunks = await second_task

        assert calls == []
        assert second_chunks[-1].message_type == MessageType.RECEIPT
        assert second_chunks[-1].error_code == ErrorCode.CANCELLED

    asyncio.run(body())


def test_control_cancels_pending_work_then_runs():
    started = asyncio.Event()

    async def blocked(params: UnitParams):
        started.set()
        await asyncio.Event().wait()

    def stop(params: UnitParams):
        return "stopped"

    _register("blocked", CommandMode.WAIT, blocked)
    _register("stop", CommandMode.CONTROL, stop)

    async def collect(operation, request_id):
        cmd = make_command(request_id, "c-1", operation)
        return [chunk async for chunk in submit(cmd)]

    async def body():
        waiting = asyncio.create_task(collect("unit.blocked", "req-wait"))
        await started.wait()
        stopped = await collect("unit.stop", "req-stop")
        with pytest.raises(asyncio.CancelledError):
            await waiting

        assert stopped[0].accepted is True
        assert stopped[0].result == "stopped"

    asyncio.run(body())


def test_failed_previous_wait_does_not_block_the_next():
    entered = asyncio.Event()
    release = asyncio.Event()

    class Scripted:
        def __init__(self):
            self.calls = 0

        async def command_executor(self, request_id, operation, conversation_id, params):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                await release.wait()
                raise RuntimeError("boom")
            yield make_receipt_success(request_id, conversation_id, result="second")

    def unused(params: UnitParams):
        return None

    _register("first", CommandMode.WAIT, unused)
    _register("second", CommandMode.WAIT, unused)
    scripted = Scripted()

    async def collect(operation, request_id):
        return [
            chunk
            async for chunk in submit(make_command(request_id, "c-1", operation))
        ]

    async def body():
        first_task = asyncio.create_task(collect("unit.first", "req-1"))
        await entered.wait()
        second_task = asyncio.create_task(collect("unit.second", "req-2"))
        await _until(lambda: len(scheduler._queues.get("c-1", [])) == 2)
        release.set()

        with pytest.raises(RuntimeError, match="boom"):
            await first_task
        second_chunks = await second_task

        assert scripted.calls == 2
        assert second_chunks[-1].result == "second"

    original = scheduler._executor.command_executor
    scheduler._executor.command_executor = scripted.command_executor
    try:
        asyncio.run(body())
    finally:
        scheduler._executor.command_executor = original
