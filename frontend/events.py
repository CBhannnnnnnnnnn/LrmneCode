"""事件渲染器：后端 event → 展示动作。

每个事件对应一个 ``@event`` 注册的函数，与后端 ``adapter/translate.py`` 的事件名单
一一对应；未注册的事件静默丢弃（与后端「未知事件不透传」的取舍一致）。

处理函数签名统一为 ``(app, conv, data)``：``conv`` 是按事件信封上的
conversation_id 路由到的会话，多会话下无需额外判断。
"""

from __future__ import annotations

import time

from rich.text import Text

from . import datablocks
from .registry import event
from .theme import S_FAINT, S_TEXT
from .widgets import (
    ApprovalPanel,
    Card,
    HintBlock,
    NoticeLine,
    ThinkingBlock,
    ToolCallView,
)


def block_key(data: dict) -> str:
    """流式块的索引键：同一 reply 内的 block 唯一。"""
    return f"{data.get('reply_id')}:{data.get('block_id')}"


# ---------- 回复与模型 ----------


@event("reply.start")
def _reply_start(app, conv, data) -> None:
    """回复开始本身无需渲染。"""


@event("reply.end")
def _reply_end(app, conv, data) -> None:
    error = data.get("error")
    if error:
        conv.view.add(NoticeLine(f"回复出错：{error}", "error"))
    conv.finalize_streams()


@event("model.start")
def _model_start(app, conv, data) -> None:
    conv.model = str(data.get("model_name") or "")
    # 这次调用的产出归因目标重新开始攒
    conv.call_outputs = []
    # 耗时按事件到达时刻量：两端都在同一条本地管道上，差的那点延迟可以忽略
    conv.call_started = time.monotonic()
    app.refresh_status()


@event("model.end")
def _model_end(app, conv, data) -> None:
    tokens_in = int(data.get("input_tokens") or 0)
    tokens_out = int(data.get("output_tokens") or 0)
    conv.tokens_in += tokens_in
    conv.tokens_out += tokens_out
    # input_tokens 就是这次调用实际喂进去的上下文长度，直接当压力表的分子
    conv.context_tokens = tokens_in
    # 命中率取「这一次调用」的量：累计值把每轮重复发送的前缀一直摊进分母，
    # 越跑越低，看不出此刻的命中情况
    conv.cache_tokens = int(data.get("cache_input_tokens") or 0)
    conv.cache_created = int(data.get("cache_creation_input_tokens") or 0)
    conv.context_usage = data.get("context") or None
    if conv.call_started:
        conv.gen_seconds += time.monotonic() - conv.call_started
        conv.call_started = 0.0
    # 用量是整次调用的，正文块早就收尾了，这里按字数把它分回思考块与正文块
    conv.share_usage(tokens_out)
    app.refresh_status()


# ---------- 助手文本流 ----------


@event("stream.text.start")
def _text_start(app, conv, data) -> None:
    conv.view.add(conv.begin_text(block_key(data)))


@event("stream.text")
def _text_delta(app, conv, data) -> None:
    conv.push_text(block_key(data), str(data.get("text_delta") or ""))


@event("stream.text.end")
def _text_end(app, conv, data) -> None:
    conv.end_text(block_key(data))
    conv.view.stick_bottom()


# ---------- 思考流 ----------


@event("stream.thinking.start")
def _thinking_start(app, conv, data) -> None:
    block = ThinkingBlock(str(data.get("block_id")))
    conv.thinking[block_key(data)] = block
    conv.call_outputs.append(block)
    conv.view.add(block)


@event("stream.thinking")
def _thinking_delta(app, conv, data) -> None:
    block = conv.thinking.get(block_key(data))
    if block is not None:
        block.append_delta(str(data.get("text_delta") or ""))
        conv.view.stick_bottom()


@event("stream.thinking.end")
def _thinking_end(app, conv, data) -> None:
    block = conv.thinking.get(block_key(data))
    if block is not None:
        block.finish()


# ---------- 数据块 ----------


@event("stream.data.start")
def _data_start(app, conv, data) -> None:
    conv.data_blocks[block_key(data)] = {
        "media_type": data.get("media_type") or "data",
        "buffer": "",
    }


@event("stream.data")
def _data_delta(app, conv, data) -> None:
    block = conv.data_blocks.get(block_key(data))
    if block is None:
        return
    raw = data.get("data")
    # 协议约定 data 是增量 base64 字符串，先攒满，end 时统一解码
    if isinstance(raw, str):
        block["buffer"] += raw


@event("stream.data.end")
def _data_end(app, conv, data) -> None:
    block = conv.data_blocks.pop(block_key(data), None)
    if block is None:
        return
    media = str(block.get("media_type") or "data")
    raw = datablocks.decode_base64(str(block.get("buffer") or ""))
    if raw is None:
        conv.view.add(NoticeLine(f"数据块 {media} 无法解码", "warn"))
        return
    if media.startswith("text/"):
        conv.view.add(Card(f"数据块 · {media}", Text(_clip_text(raw.decode("utf-8", "replace")))))
        return
    if media.startswith("image/"):
        path = datablocks.save_temp(raw, media)
        body = Text()
        body.append(datablocks.describe_image(raw, media) + "\n", style=S_TEXT)
        body.append(f"→ {path}", style=S_FAINT)
        conv.view.add(Card(f"数据块 · {media}", body))
        return
    conv.view.add(
        NoticeLine(
            f"收到数据块 {media}（{datablocks.human_size(len(raw))}）", "info"
        ),
    )


def _clip_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…… 已截断（共 {len(text)} 字符）"


# ---------- 系统提示 ----------


@event("stream.hint")
def _hint(app, conv, data) -> None:
    """运行时注入的系统提示：只落一行标记，原文不进转录（见 HintBlock）。"""
    conv.view.add(HintBlock(data.get("source")))


@event("context.compressed")
def _context_compressed(app, conv, data) -> None:
    """引擎把早期对话归纳成了摘要：上下文被有损改写，必须说一声。

    压缩是引擎自己在每次推理前按窗口占比触发的，不经前端；摘要内容不进转录，
    它的体量在右侧栏「上下文」框里以「摘要」一段体现。
    """
    before = int(data.get("before") or 0)
    after = int(data.get("after") or 0)
    conv.view.add(
        NoticeLine(
            f"上下文接近窗口上限，已压缩：{before} 条消息归纳为摘要，保留最近 {after} 条",
            "warn",
        ),
    )
    conv.view.stick_bottom()
    app.refresh_status()


# ---------- 工具调用与结果 ----------


@event("tool.call.start")
def _tool_start(app, conv, data) -> None:
    view = ToolCallView(
        str(data.get("tool_call_id")), str(data.get("name") or "tool")
    )
    conv.tools[view.tool_call_id] = view
    conv.view.add(view)


@event("tool.call.delta")
def _tool_delta(app, conv, data) -> None:
    view = conv.tools.get(str(data.get("tool_call_id")))
    if view is not None:
        view.call_delta(str(data.get("delta") or ""))


@event("tool.call.end")
def _tool_call_end(app, conv, data) -> None:
    view = conv.tools.get(str(data.get("tool_call_id")))
    if view is not None:
        view.call_end()


@event("tool.result.start")
def _tool_result_start(app, conv, data) -> None:
    """名称已在 tool.call.start 显示。"""


@event("tool.result.delta")
def _tool_result_delta(app, conv, data) -> None:
    view = conv.tools.get(str(data.get("tool_call_id")))
    if view is not None:
        view.result_delta(str(data.get("text_delta") or ""))


@event("tool.result.data")
def _tool_result_data(app, conv, data) -> None:
    view = conv.tools.get(str(data.get("tool_call_id")))
    if view is not None:
        view.result_data(
            str(data.get("block_id") or ""),
            str(data.get("media_type") or "data"),
            data.get("data"),
            data.get("url"),
        )


@event("tool.result.end")
def _tool_result_end(app, conv, data) -> None:
    view = conv.tools.get(str(data.get("tool_call_id")))
    if view is not None:
        state = data.get("state")
        view.result_end(str(state) if state is not None else None)


# ---------- 审批 ----------


@event("approval.request")
def _approval_request(app, conv, data) -> None:
    approval_id = str(data.get("approval_request_id") or data.get("reply_id") or "")
    if not approval_id or approval_id in conv.approvals:
        return
    panel = ApprovalPanel(
        approval_id,
        list(data.get("tool_calls") or []),
        classes="approval-panel",
    )
    conv.approvals[approval_id] = panel
    conv.view.add(panel)
    # 整批调用都被审批挂住了：别让没表态的那些继续转「执行中」
    conv.set_tools_awaiting(True)
    app.call_after_refresh(panel.focus)
    # 要用户表态的东西必须看得见，不能指望他自己往下翻到面板
    app.call_after_refresh(conv.view.force_bottom)
    app.refresh_status()


# ---------- 调度与运行结束 ----------


@event("run.queued")
def _run_queued(app, conv, data) -> None:
    request_id = data.get("request_id")
    if request_id:
        conv.queued.add(str(request_id))
    conv.view.add(
        NoticeLine(f"已排队等待执行：{data.get('operation')}", "warn"),
    )
    app.refresh_status()


@event("run.finished")
def _run_finished(app, conv, data) -> None:
    request_id = data.get("request_id")
    if request_id:
        conv.pending.pop(str(request_id), None)
        conv.inflight.discard(str(request_id))
        conv.queued.discard(str(request_id))
        # 这一轮已经结束，下一轮里 Esc 可以再打断一次
        conv.stopping = False

    if data.get("stop_reason") == "error":
        conv.view.add(
            NoticeLine(f"执行出错：{data.get('error') or '未知错误'}", "error"),
        )
    # interrupted 只可能来自 Esc（见 app.action_escape），那一侧已经报过「已暂停」，
    # 这里再报一次就是同一件事说两遍

    conv.finalize_streams()
    conv.close_open_blocks()
    app.refresh_status()
