"""前端侧的分发注册表：operation → 结果呈现，event → 事件渲染。

镜像后端 ``proxy_layer/register.py`` 的思路：后端把 operation 绑到 handler，
前端把 operation 绑到「成功回执的 result 怎么呈现」，把 event 绑到「事件怎么渲染」。
UI 壳只做查表分发，不再出现 ``kind`` 之类的字符串分支。

- 结果渲染器写在 ``results.py``，用 ``@operation`` 注册。
- 事件渲染器写在 ``events.py``，用 ``@event`` 注册。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .app import LrmneAgentApp
    from .conversation import Conversation

# 成功回执携带的 result → 展示
ResultRenderer = Callable[["LrmneAgentApp", "Conversation", Any], None]
# 后端单向推送的事件 data → 展示
EventHandler = Callable[["LrmneAgentApp", "Conversation", dict], None]


@dataclass(frozen=True)
class OperationSpec:
    """前端已知的一个 operation。"""

    operation: str
    label: str  # 人类可读说明，用于提示与状态
    stream: bool = False  # 回执后还有事件流：需要登记在途状态直到 run.finished
    render_result: ResultRenderer | None = None


_operations: dict[str, OperationSpec] = {}
_events: dict[str, EventHandler] = {}


def operation(operation: str, *, label: str = ""):
    """把结果渲染器注册到 operation 名下（与后端 register_command 对称）。"""

    def decorator(func: ResultRenderer) -> ResultRenderer:
        _operations[operation] = OperationSpec(
            operation=operation,
            label=label or operation,
            render_result=func,
        )
        return func

    return decorator


def declare_operation(operation: str, *, label: str = "", stream: bool = False) -> None:
    """登记没有结果渲染器的 operation（如 chat.send、approval.respond）。"""
    _operations[operation] = OperationSpec(
        operation=operation,
        label=label or operation,
        stream=stream,
    )


def event(name: str):
    """注册一个事件渲染器。"""

    def decorator(func: EventHandler) -> EventHandler:
        _events[name] = func
        return func

    return decorator


def lookup_operation(operation: str) -> OperationSpec | None:
    return _operations.get(operation)


def lookup_event(name: str) -> EventHandler | None:
    return _events.get(name)


def known_operations() -> list[str]:
    return sorted(_operations)


def known_events() -> list[str]:
    return sorted(_events)
