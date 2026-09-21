from __future__ import annotations

from typing import Any

from agentscope.message import Msg, SystemMsg, UserMsg


# 一条消息里正文和工具块常常并存；工具调用与结果才是把窗口吃掉的大头，按块归类
_TOOL_BLOCKS = ("tool_call", "tool_result")


def _text_msgs(text: str) -> list[Msg]:
    """一段纯文本交给计数器：包成系统消息（计数器只读 content，不看 role）。"""
    return [SystemMsg(name="system", content=text)] if text else []


async def usage_breakdown(agent: Any) -> dict[str, Any]:
    """把「下一次要喂给模型的上下文」按类别估 token，供前端浮窗看构成。

    估算走模型自带的本地计数器（字节数 ÷ 4，不发请求、不计费），分段与真实调用
    同源：系统提示取 ``_get_system_prompt``（已含技能与 offloader 注入的那段），
    工具 schema 与对话历史各取各的。``total`` 是各类之和，只用来算占比。
    """
    model = agent.model
    groups = agent.state.tool_context.activated_groups
    prompt = await agent._get_system_prompt()
    skills = await agent.toolkit.get_skill_instructions(groups) or ""
    # 技能说明是拼在系统提示里下发的，不减掉就把同一段算两次
    base = prompt.replace(skills, "") if skills and skills in prompt else prompt
    schemas = await agent.toolkit.get_tool_schemas(groups)

    chat: list[Msg] = []
    calls: list[Msg] = []
    for msg in agent.state.context:
        tool, rest = [], []
        for block in msg.get_content_blocks():
            (tool if block.type in _TOOL_BLOCKS else rest).append(block)
        # 换掉 content 即可，role 保持原样：归类要的就是「这条消息里的那几块」
        if rest:
            chat.append(msg.model_copy(update={"content": rest}))
        if tool:
            calls.append(msg.model_copy(update={"content": tool}))

    async def _count(messages: list[Msg], tools: list[dict] | None = None) -> int:
        if not messages and not tools:
            return 0
        return await model.count_tokens(messages, tools)

    summary = agent.state.summary
    counted = [
        ("prompt", await _count(_text_msgs(base))),
        ("skills", await _count(_text_msgs(skills))),
        ("tools", await _count([], schemas)),
        ("messages", await _count(chat)),
        ("tool_calls", await _count(calls)),
        # 摘要按用户消息下发（里面可能带数据块），照原样喂给计数器
        ("summary", await _count([UserMsg(name="user", content=summary)] if summary else [])),
    ]
    segments = [{"key": key, "tokens": tokens} for key, tokens in counted if tokens]
    return {"total": sum(item["tokens"] for item in segments), "segments": segments}
