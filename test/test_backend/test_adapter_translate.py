"""translate 单元测试：agentscope 事件 → 前端协议事件的映射。

``translate`` 是纯函数（不碰模型、不发请求），却是前端整条事件流的唯一来源。
这里锁两件事：**哪类事件映射成哪个事件名**（改名即失败），以及少数有实际行为
的字段（工具结果只留尾部）。
"""

from agentscope.event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    HintBlockEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserInterruptEvent,
)

from backend.adapter.translate import _RESULT_DELTA_TAIL, translate


def test_every_mapped_event_carries_its_expected_name():
    cases = [
        (ReplyStartEvent(session_id="s", reply_id="r", name="a"), "reply.start"),
        (ReplyEndEvent(session_id="s", reply_id="r"), "reply.end"),
        (ModelCallStartEvent(reply_id="r", model_name="m"), "model.start"),
        (ModelCallEndEvent(reply_id="r", input_tokens=1, output_tokens=2), "model.end"),
        (TextBlockStartEvent(reply_id="r", block_id="b"), "stream.text.start"),
        (TextBlockDeltaEvent(reply_id="r", block_id="b", delta="x"), "stream.text"),
        (TextBlockEndEvent(reply_id="r", block_id="b"), "stream.text.end"),
        (ThinkingBlockStartEvent(reply_id="r", block_id="b"), "stream.thinking.start"),
        (ThinkingBlockDeltaEvent(reply_id="r", block_id="b", delta="x"), "stream.thinking"),
        (ThinkingBlockEndEvent(reply_id="r", block_id="b"), "stream.thinking.end"),
        (
            DataBlockStartEvent(reply_id="r", block_id="b", media_type="text/plain"),
            "stream.data.start",
        ),
        (
            DataBlockDeltaEvent(reply_id="r", block_id="b", media_type="text/plain", data="x"),
            "stream.data",
        ),
        (DataBlockEndEvent(reply_id="r", block_id="b"), "stream.data.end"),
        (HintBlockEvent(reply_id="r", block_id="b", hint="提示"), "stream.hint"),
        (
            ToolCallStartEvent(reply_id="r", tool_call_id="t", tool_call_name="Read"),
            "tool.call.start",
        ),
        (ToolCallDeltaEvent(reply_id="r", tool_call_id="t", delta="x"), "tool.call.delta"),
        (ToolCallEndEvent(reply_id="r", tool_call_id="t"), "tool.call.end"),
        (
            ToolResultStartEvent(reply_id="r", tool_call_id="t", tool_call_name="Read"),
            "tool.result.start",
        ),
        (
            ToolResultTextDeltaEvent(reply_id="r", tool_call_id="t", delta="x"),
            "tool.result.delta",
        ),
        (
            ToolResultDataDeltaEvent(
                reply_id="r", tool_call_id="t", media_type="image/png", data="x"
            ),
            "tool.result.data",
        ),
        (
            ToolResultEndEvent(reply_id="r", tool_call_id="t", state="success"),
            "tool.result.end",
        ),
        (RequireUserConfirmEvent(reply_id="r", tool_calls=[]), "approval.request"),
    ]

    for event, expected in cases:
        translated = translate(event)
        assert translated is not None, type(event).__name__
        assert translated["event"] == expected, type(event).__name__


def test_unmapped_event_is_dropped():
    """未登记的事件一律返回 None（前端静默丢弃，与「未知事件不透传」一致）。"""
    assert translate(UserInterruptEvent(reply_id="r")) is None


def test_model_end_reports_the_token_breakdown():
    translated = translate(
        ModelCallEndEvent(
            reply_id="r",
            input_tokens=10,
            output_tokens=20,
            cache_input_tokens=3,
            cache_creation_input_tokens=4,
        ),
    )

    data = translated["data"]
    assert data["input_tokens"] == 10
    assert data["output_tokens"] == 20
    assert data["cache_input_tokens"] == 3
    assert data["cache_creation_input_tokens"] == 4


def test_tool_result_delta_keeps_only_the_tail():
    """整块大输出不该原样变成一行协议消息，只留尾部。"""
    delta = "x" * (_RESULT_DELTA_TAIL + 5)

    translated = translate(ToolResultTextDeltaEvent(reply_id="r", tool_call_id="t", delta=delta))

    assert translated["data"]["text_delta"] == delta[-_RESULT_DELTA_TAIL:]
