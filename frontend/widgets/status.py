"""底部状态栏：只留一眼要看到的三样——运行态（含审批数）、模型、上下文压力表。

由 ``frontend/widgets/panels.py`` 继续拆分而来，只做搬运，未改任何实现。
"""


from typing import Any
from rich.cells import cell_len
from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static
from ..theme import GLYPH_ACTIVE, GLYPH_METER_EMPTY, GLYPH_METER_FULL, GLYPH_ELLIPSIS, GLYPH_NOTICE, GLYPH_QUEUED, GLYPH_SPINNER, S_ERR, S_GHOST, S_OK, S_TEXT, S_WARN
from .base import _tail_path
from .usage import prompt_total



# 上下文压力表的格子数：4 格足够看出「还早 / 过半 / 快满」
_METER_CELLS = 4

# 状态栏分段之间的分隔符（宽度固定，用于窄屏裁剪时预估）
_SEPARATOR = "  │  "
_SEP_WIDTH = cell_len(_SEPARATOR)
# 被裁掉时补的省略号（含前面的空格）
_TAIL_WIDTH = cell_len(f" {GLYPH_ELLIPSIS}")



class StatusBar(Horizontal):
    """底部状态栏：只留一眼要看到的三样——运行态（含审批数）、模型、上下文占用。

    这行只有一格高，随后续开发一定塞不下更多东西，硬挤只会把要紧的三项挤到看不
    见；其余量化信息在右侧栏 ``SidePanel``。ctx 表留在底部，因为它是"还能不能继续
    跑"的即时信号，模型名是"在跟谁说话"的即时信号。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._left = Static("", classes="status-left")
        self._state = "idle"  # idle | working | queued
        self._approvals = 0
        self._model = ""
        self._config_model = ""
        self._context_size = 0
        self._context_tokens = 0
        self._cache_tokens = 0
        self._cache_created = 0
        self._provider = ""
        self._frame = 0
        self._timer = None

    def compose(self):
        yield self._left

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.15, self._tick)
        self._refresh()

    def _tick(self) -> None:
        if self._state == "working":
            self._frame += 1
            self._refresh()

    # -- 对外接口 --

    def set_state(self, state: str) -> None:
        self._state = state
        self._refresh()

    def set_approvals(self, count: int) -> None:
        self._approvals = count
        self._refresh()

    def set_model(self, name: str) -> None:
        self._model = name or ""
        self._refresh()

    def set_usage(
        self, context_tokens: int, cache_tokens: int, cache_created: int = 0
    ) -> None:
        """压力表只要最近一次调用的量；其余计数归右侧栏。"""
        self._context_tokens = context_tokens
        self._cache_tokens = cache_tokens
        self._cache_created = cache_created
        self._refresh()

    def apply_config(self, flat: dict) -> None:
        """这里只取影响压力表的两项：窗口大小与提供方口径。

        字段缺席表示这次回执没说这件事，保留原值。
        """
        if flat.get("model"):
            self._config_model = str(flat["model"])
        if "provider_type" in flat:
            self._provider = str(flat["provider_type"] or "")
        if isinstance(flat.get("context_size"), int):
            self._context_size = flat["context_size"]
        self._refresh()

    # -- 渲染 --

    def _pressure(self) -> tuple[str, str] | None:
        """上下文占用：(文本, 样式)；不知道窗口大小时不给这一段。"""
        total = prompt_total(
            self._context_tokens,
            self._cache_tokens,
            self._provider,
            self._cache_created,
        )
        if not total or not self._context_size:
            return None
        ratio = total / self._context_size
        if ratio < 0.6:
            style = S_OK
        elif ratio < 0.85:
            style = S_WARN
        else:
            style = S_ERR
        filled = max(1, min(_METER_CELLS, round(ratio * _METER_CELLS)))
        bar = GLYPH_METER_FULL * filled + GLYPH_METER_EMPTY * (_METER_CELLS - filled)
        return f"ctx {bar} {ratio * 100:.0f}%", style

    def _segments(self) -> list[tuple[str, str]]:
        """模型名 + 上下文占用，就这两段；其余量化信息在右侧栏。"""
        segments: list[tuple[str, str]] = [
            (self._model or self._config_model or "—", S_TEXT)
        ]
        pressure = self._pressure()
        if pressure is not None:
            segments.append(pressure)
        return segments

    def _refresh(self) -> None:
        left = Text()
        if self._state == "working":
            left.append(
                f"{GLYPH_SPINNER[self._frame % len(GLYPH_SPINNER)]} 运行中",
                style=S_WARN,
            )
        elif self._state == "queued":
            left.append(f"{GLYPH_QUEUED} 排队中", style=S_WARN)
        else:
            left.append(f"{GLYPH_ACTIVE} 就绪", style=S_OK)
        if self._approvals:
            left.append(
                f"  {GLYPH_NOTICE['warn']} 审批×{self._approvals}", style=S_WARN
            )

        # 窄屏时从左往右保留，尾段先丢。宽度不能用左块的 size —— 它由 1fr 算出来，
        # on_resize 时会晚一拍，所以自己从整行扣，末尾留一列不顶到边界。
        available = self.content_size.width - 1
        for index, (text, style) in enumerate(self._segments()):
            if (
                index
                and available > 0
                and left.cell_len + _SEP_WIDTH + cell_len(text) > available
            ):
                if left.cell_len + _TAIL_WIDTH <= available:
                    left.append(f" {GLYPH_ELLIPSIS}", style=S_GHOST)
                break
            left.append(_SEPARATOR, style=S_GHOST)
            left.append(text, style=style)

        self._left.update(left)

    def on_resize(self) -> None:
        self._refresh()


