"""上下文构成的 token 估算：分类要不重不漏，浮窗里的占比才有意义。

用真 Agent + 真 Toolkit 跑，只把模型换成不联网的构造（``count_tokens`` 是本地
byte/4 估算，不发请求）；这里锁的是「分段求和 == 整段一次数」这条不变量。
"""

import asyncio

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import (
    AssistantMsg,
    SystemMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import OpenAIChatModel
from agentscope.state import AgentState
from agentscope.tool import Glob, Read, Toolkit

from backend.adapter.context import usage_breakdown


def _agent() -> Agent:
    state = AgentState(
        context=[
            UserMsg(name="u", content=[TextBlock(type="text", text="问" * 400)]),
            AssistantMsg(
                name="a",
                content=[
                    TextBlock(type="text", text="答" * 400),
                    ToolCallBlock(id="t1", name="Read", input='{"path":"a.py"}'),
                    ToolResultBlock(
                        id="t1", name="Read", output="结果" * 2000, state="success"
                    ),
                ],
            ),
        ],
        summary="摘要" * 100,
    )
    return Agent(
        name="a",
        system_prompt="提示词" * 500,
        model=OpenAIChatModel(
            credential=OpenAICredential(api_key="sk-test"),
            model="gpt-4o",
        ),
        toolkit=Toolkit(tools=[Read(), Glob()]),
        state=state,
    )


def _usage_and_whole():
    """同一份输入分别按「分类求和」与「整段一次数」算，交给测试比对。"""

    async def body():
        agent = _agent()
        usage = await usage_breakdown(agent)
        schemas = await agent.toolkit.get_tool_schemas(
            agent.state.tool_context.activated_groups,
        )
        whole = await agent.model.count_tokens(
            [
                SystemMsg(name="system", content=await agent._get_system_prompt()),
                UserMsg(name="user", content=agent.state.summary),
                *agent.state.context,
            ],
            schemas,
        )
        return usage, whole

    return asyncio.run(body())


def test_breakdown_splits_every_populated_category():
    usage, _ = _usage_and_whole()

    # 正文与工具块混在同一条助手消息里，也要各归各类
    assert [item["key"] for item in usage["segments"]] == [
        "prompt",
        "tools",
        "messages",
        "tool_calls",
        "summary",
    ]
    assert all(item["tokens"] > 0 for item in usage["segments"])


def test_breakdown_totals_match_one_pass_over_the_same_input():
    """分类求和与整段一次数只差各类各自取整：技能不会被算两次，也没有哪段漏掉。"""
    usage, whole = _usage_and_whole()

    assert usage["total"] != whole  # 取整必然有差，否则这条断言没在测东西
    assert abs(usage["total"] - whole) <= len(usage["segments"])


def test_tool_results_dominate_a_growing_context():
    """工具结果是大头这条要看得见：它比正文与提示词之和还多。"""
    usage, _ = _usage_and_whole()
    tokens = {item["key"]: item["tokens"] for item in usage["segments"]}

    assert tokens["tool_calls"] > tokens["prompt"] + tokens["messages"]
