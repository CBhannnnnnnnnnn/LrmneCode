from __future__ import annotations

from typing import Any

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
)

from ._dump import _dump


def translate(event: Any) -> dict[str, Any] | None:
    """AgentScope 事件 → {event, data}；入站/未知类型返回 None。"""

    if isinstance(event, ReplyStartEvent):
        return {
            "event": "reply.start",
            "data": {
                "session_id": event.session_id,
                "reply_id": event.reply_id,
                "name": event.name,
                "role": event.role,
            },
        }

    if isinstance(event, ReplyEndEvent):
        return {
            "event": "reply.end",
            "data": {
                "session_id": event.session_id,
                "reply_id": event.reply_id,
                "finished_reason": event.finished_reason,
                "error": _dump(event.error),
            },
        }

    if isinstance(event, ModelCallStartEvent):
        return {
            "event": "model.start",
            "data": {
                "reply_id": event.reply_id,
                "model_name": event.model_name,
            },
        }

    if isinstance(event, ModelCallEndEvent):
        return {
            "event": "model.end",
            "data": {
                "reply_id": event.reply_id,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cache_input_tokens": event.cache_input_tokens,
                "cache_creation_input_tokens": event.cache_creation_input_tokens,
                "finished_reason": event.finished_reason,
            },
        }

    if isinstance(event, TextBlockStartEvent):
        return {
            "event": "stream.text.start",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
            },
        }

    if isinstance(event, TextBlockDeltaEvent):
        return {
            "event": "stream.text",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
                "text_delta": event.delta,
            },
        }

    if isinstance(event, TextBlockEndEvent):
        return {
            "event": "stream.text.end",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
            },
        }

    if isinstance(event, ThinkingBlockStartEvent):
        return {
            "event": "stream.thinking.start",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
            },
        }

    if isinstance(event, ThinkingBlockDeltaEvent):
        return {
            "event": "stream.thinking",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
                "text_delta": event.delta,
            },
        }

    if isinstance(event, ThinkingBlockEndEvent):
        return {
            "event": "stream.thinking.end",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
            },
        }

    if isinstance(event, DataBlockStartEvent):
        return {
            "event": "stream.data.start",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
                "media_type": event.media_type,
            },
        }

    if isinstance(event, DataBlockDeltaEvent):
        return {
            "event": "stream.data",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
                "media_type": event.media_type,
                "data": event.data,
            },
        }

    if isinstance(event, DataBlockEndEvent):
        return {
            "event": "stream.data.end",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
            },
        }

    if isinstance(event, HintBlockEvent):
        hint = event.hint
        if isinstance(hint, list):
            hint = [_dump(b) for b in hint]
        return {
            "event": "stream.hint",
            "data": {
                "reply_id": event.reply_id,
                "block_id": event.block_id,
                "source": event.source,
                "hint": hint,
            },
        }

    if isinstance(event, ToolCallStartEvent):
        return {
            "event": "tool.call.start",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
                "name": event.tool_call_name,
            },
        }

    if isinstance(event, ToolCallDeltaEvent):
        return {
            "event": "tool.call.delta",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
                "delta": event.delta,
            },
        }

    if isinstance(event, ToolCallEndEvent):
        return {
            "event": "tool.call.end",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
            },
        }

    if isinstance(event, ToolResultStartEvent):
        return {
            "event": "tool.result.start",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
                "name": event.tool_call_name,
            },
        }

    if isinstance(event, ToolResultTextDeltaEvent):
        return {
            "event": "tool.result.delta",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
                "text_delta": event.delta,
            },
        }

    if isinstance(event, ToolResultDataDeltaEvent):
        return {
            "event": "tool.result.data",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
                "block_id": event.block_id,
                "media_type": event.media_type,
                "data": event.data,
                "url": event.url,
            },
        }

    if isinstance(event, ToolResultEndEvent):
        return {
            "event": "tool.result.end",
            "data": {
                "reply_id": event.reply_id,
                "tool_call_id": event.tool_call_id,
                "state": event.state,
                "metadata": event.metadata,
            },
        }

    if isinstance(event, RequireUserConfirmEvent):
        return {
            "event": "approval.request",
            "data": {
                "approval_request_id": event.reply_id,
                "reply_id": event.reply_id,
                "tool_calls": [_dump(tc) for tc in event.tool_calls],
            },
        }

    return None
