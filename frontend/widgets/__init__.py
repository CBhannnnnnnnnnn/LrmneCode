"""聊天区显示部件：协议事件 → 专属渲染。

视觉约定见 :mod:`frontend.widgets.base` 的模块文档；本包由单文件 ``widgets.py``
拆分而来，``__init__`` 原样转发全部顶层名字，使 ``from .widgets import X``
的既有调用点零改动。
"""

from .base import (
    _tail_path,
    StreamMarkdown,
    _one_line,
    _fmt_elapsed,
    _STATE_CLASSES,
    set_block_state,
    Block,
    _as_mapping,
)
from .usage import (
    _fmt_tokens,
    _CACHE_OUTSIDE_INPUT,
    prompt_total,
)
from .transcript import (
    _NOTICE_STYLE,
    AssistantBlock,
    UserMessage,
    NoticeLine,
    Card,
    ThinkingBlock,
    HintBlock,
)
from .tool_view import (
    ToolCallView,
    diff_fold,
    _call_parts,
)
from .approval import (
    ApprovalPanel,
)
from .panels import (
    _METER_CELLS,
    _SEPARATOR,
    _SEP_WIDTH,
    _TAIL_WIDTH,
    StatusBar,
    SidePanel,
)
from .input import (
    InputArea,
    InlinePalette,
)

__all__ = [
    '_NOTICE_STYLE',
    '_METER_CELLS',
    '_SEPARATOR',
    '_SEP_WIDTH',
    '_TAIL_WIDTH',
    '_tail_path',
    'StreamMarkdown',
    '_one_line',
    '_fmt_elapsed',
    '_STATE_CLASSES',
    'set_block_state',
    'Block',
    'AssistantBlock',
    'UserMessage',
    'NoticeLine',
    'Card',
    'ThinkingBlock',
    'HintBlock',
    '_as_mapping',
    'ToolCallView',
    'ApprovalPanel',
    'diff_fold',
    '_call_parts',
    '_fmt_tokens',
    '_CACHE_OUTSIDE_INPUT',
    'prompt_total',
    'StatusBar',
    'SidePanel',
    'InputArea',
    'InlinePalette',
]
