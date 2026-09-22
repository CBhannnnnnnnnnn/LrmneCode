"""右侧栏：这次会话跑出来的量化信息，一类信息一个小框。

由 ``frontend/widgets/panels.py`` 继续拆分而来，只做搬运，未改任何实现。
"""


from typing import Any
from rich.cells import cell_len
from rich.text import Text
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from ..theme import GLYPH_ELLIPSIS, S_FAINT, S_TEXT, S_USER
from .base import _tail_path
from .usage import _fmt_tokens, prompt_total



# 上下文压力表的格子数：4 格足够看出「还早 / 过半 / 快满」
_METER_CELLS = 4

# 状态栏分段之间的分隔符（宽度固定，用于窄屏裁剪时预估）
_SEPARATOR = "  │  "
_SEP_WIDTH = cell_len(_SEPARATOR)
# 被裁掉时补的省略号（含前面的空格）
_TAIL_WIDTH = cell_len(f" {GLYPH_ELLIPSIS}")


def _brand_mark() -> Text:
    """侧栏底部的品牌字：产品名（加粗、字母留白）+ 副标题，弱色不抢正文。"""
    mark = Text()
    mark.append(" ".join("LrmneAgent") + "\n", style=S_USER)
    mark.append("coding agent", style=S_FAINT)
    return mark


class SidePanel(Vertical):
    """右侧栏：这次会话跑出来的量化信息，一类一个小框。

    底部那一行放不下的都在这里：上下文构成（谁把上下文撑起来的）、token 计数与
    吞吐、缓存命中、思考与权限档位、工作目录、会话号。

    三类各占一个带边框的小框，框标题写类别名，框内只放数值——这样"这是一组"
    不靠空行去暗示。三框同处一个可滚动容器，按上下文 / 用量 / 配置依次排列；
    面板最底部钉一块品牌区（产品名字标），不随上面的框滚动：转录区长出来的
    内容不该把侧栏的落款挤走。
    """

    # 上下文构成的展示顺序与中文名；key 由后端 model.end 的 context 段给出
    _CONTEXT_LABELS = {
        "prompt": "提示词",
        "skills": "技能",
        "tools": "工具定义",
        "messages": "对话",
        "tool_calls": "工具调用",
        "summary": "摘要",
    }
    # 三列：标签（左对齐）/ 数值（右对齐）/ 占比（右对齐）。数值右对齐才看得出谁在涨
    _LABEL_WIDTH = 10
    _VALUE_WIDTH = 11
    _NOTE_WIDTH = 4

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._context_box = Static("", classes="side-box", id="side-context")
        self._metric_box = Static("", classes="side-box", id="side-metric")
        self._config_box = Static("", classes="side-box", id="side-config")
        self._brand_box = Static(_brand_mark(), classes="side-brand", id="side-brand")
        self._tokens_in = 0
        self._tokens_out = 0
        self._context_tokens = 0
        self._cache_tokens = 0
        self._cache_created = 0
        self._gen_seconds = 0.0
        # 在途调用的实时产出：估算 token、字数、已耗时（0 表示当前没有在途调用）
        self._pending_out = 0
        self._pending_chars = 0
        self._pending_seconds = 0.0
        self._context_size = 0
        self._provider = ""
        self._thinking: str | None = None  # None = 还没读到配置
        self._permission: str | None = None
        self._root = ""
        self._conversation = ""
        self._usage: dict | None = None

    def compose(self):
        yield VerticalScroll(
            self._context_box, self._metric_box, self._config_box, id="side-scroll"
        )
        yield self._brand_box

    # -- 对外接口 --

    def set_usage(
        self,
        tokens_in: int,
        tokens_out: int,
        context_tokens: int,
        cache_tokens: int,
        cache_created: int = 0,
        gen_seconds: float = 0.0,
        pending_out: int = 0,
        pending_chars: int = 0,
        pending_seconds: float = 0.0,
    ) -> None:
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._context_tokens = context_tokens
        self._cache_tokens = cache_tokens
        self._cache_created = cache_created
        self._gen_seconds = gen_seconds
        self._pending_out = pending_out
        self._pending_chars = pending_chars
        self._pending_seconds = pending_seconds
        self._refresh()

    def set_context(self, usage: dict | None) -> None:
        """上下文构成：后端随 model.end 估好的分段量；没有最近一次调用就没有这一段。"""
        if usage == self._usage:
            return
        self._usage = usage
        self._refresh()

    def set_conversation(self, cid: str) -> None:
        self._conversation = cid
        self._refresh()

    def apply_config(self, flat: dict) -> None:
        """配置里的运行档位与工作区；字段缺席保留原值，空串表示后端确实没设。"""
        if "thinking_level" in flat:
            self._thinking = str(flat["thinking_level"] or "")
        if "mode" in flat:
            self._permission = str(flat["mode"] or "")
        if "provider_type" in flat:
            self._provider = str(flat["provider_type"] or "")
        if "root" in flat:
            self._root = str(flat["root"] or "")
        if isinstance(flat.get("context_size"), int):
            self._context_size = flat["context_size"]
        self._refresh()

    # -- 渲染 --

    def _row(self, label: str, value: str, note: str = "") -> Text:
        """一行「标签 …… 数值 …… 占比」：数值右对齐，两列之间留够空隙。

        左边距交给小框自己的 padding，行内不再补空格；中文标签占两列，按显示
        宽度补齐才对得上数值列。
        """
        pad = " " * max(1, self._LABEL_WIDTH - cell_len(label))
        out = Text(f"{label}{pad}", style=S_FAINT)
        out.append(f"{value:>{self._VALUE_WIDTH}}", style=S_TEXT)
        if note:
            out.append(f" {note:>{self._NOTE_WIDTH}}", style=S_FAINT)
        return out

    def _context_section(self) -> tuple[str, list[Text]]:
        """上下文构成：百分比对着「这次上下文的总量」算——看的是谁把它撑起来的。

        总量写在框标题上而不单占一行：窗口不够高时这一栏要能整个露出来，少一行
        就少一行滚动。

        分段是后端用本地计数器（字节 ÷ 4）估的，窗口占用却是 provider 实报的
        ``input_tokens``；两者不该各说各话，所以估算只用来定「谁占几成」，量级
        一律按这个比例摊到实报总量上——框里的数与底部压力表因此始终同源。
        """
        if not self._usage:
            return "上下文", []
        items = [
            (
                self._CONTEXT_LABELS.get(str(item.get("key")), str(item.get("key"))),
                int(item.get("tokens") or 0),
            )
            for item in self._usage.get("segments") or []
        ]
        estimated = int(self._usage.get("total") or 0) or sum(
            tokens for _, tokens in items
        )
        if not items or estimated <= 0:
            return "上下文", []
        # 新一轮开始时用量归零而构成还在（上一次调用的），此时回落到估算值
        total = prompt_total(
            self._context_tokens,
            self._cache_tokens,
            self._provider,
            self._cache_created,
        ) or estimated
        scale = total / estimated
        title = f"上下文 {_fmt_tokens(total)}"
        if self._context_size:
            title += f"/{_fmt_tokens(self._context_size)}"
        rows = [
            self._row(
                label,
                _fmt_tokens(int(tokens * scale + 0.5)),
                f"{tokens / estimated * 100:.0f}%",
            )
            for label, tokens in items
        ]
        return title, rows

    def _metric_section(self) -> tuple[str, list[Text]]:
        rows = []
        pending = self._pending_rows()
        if self._tokens_in or self._tokens_out:
            rows.append(
                self._row(
                    "输入输出",
                    f"↑{_fmt_tokens(self._tokens_in)} ↓{_fmt_tokens(self._tokens_out)}",
                )
            )
        # 真值吞吐与在途吞吐是同一个量的两个时刻，同时出现就成了两行"吞吐"
        if self._gen_seconds and self._tokens_out and not pending:
            # 吞吐取整场累计：单次调用的量抖得厉害，看不出趋势
            rows.append(
                self._row("吞吐", f"{self._tokens_out / self._gen_seconds:.0f} tok/s")
            )
        hit = self._cache_hit()
        if hit:
            rows.append(self._row("缓存命中", hit))
        rows.extend(pending)
        return "用量", rows

    def _pending_rows(self) -> list[Text]:
        """在途调用的实时产出。

        provider 只在 ``model.end`` 报一次量，所以这次调用跑到一半时上面那些真值行
        全是空的（发送时 ``reset_tokens`` 归零）——不补这一组，整框会被收掉。
        估算值一律带 ``≈``，没校准过时干脆只报字数，不假装知道 token。
        """
        if not (self._pending_out or self._pending_chars):
            return []
        rows = []
        if self._pending_out:
            rows.append(self._row("输出", f"≈{_fmt_tokens(self._pending_out)}"))
        else:
            rows.append(self._row("输出", f"{self._pending_chars} 字"))
        spent = self._pending_seconds
        if spent > 0.5:
            if self._pending_out:
                rows.append(
                    self._row("吞吐", f"≈{self._pending_out / spent:.0f} tok/s")
                )
            elif self._pending_chars:
                rows.append(
                    self._row("吞吐", f"≈{self._pending_chars / spent:.0f} 字/s")
                )
        return rows

    def _cache_hit(self) -> str:
        """缓存命中率：这次调用从提示缓存读到的量 ÷ 提示词总量。

        取最近一次调用而不是整场累计：累计值把每轮重复发送的前缀一直摊进分母，
        越跑越低，看不出此刻的命中情况。
        """
        total = prompt_total(
            self._context_tokens,
            self._cache_tokens,
            self._provider,
            self._cache_created,
        )
        if not total or not self._cache_tokens:
            return ""
        return f"{self._cache_tokens / total * 100:.0f}%"

    def _config_section(self) -> tuple[str, list[Text]]:
        rows = [
            # 没读到配置时按引擎默认值显示，不留一串看不懂的破折号
            self._row("思考", self._thinking or "off"),
            self._row("权限", self._permission or "default"),
        ]
        root = _tail_path(self._root)
        if root:
            rows.append(self._row("目录", root))
        if self._conversation:
            rows.append(self._row("会话", self._conversation))
        return "配置", rows

    def _fill(self, box: Static, title: str, rows: list[Text]) -> None:
        """一个小框：类别名写在边框上，没有内容就连框一起收起来。"""
        box.display = bool(rows)
        box.border_title = title
        body = Text()
        for index, row in enumerate(rows):
            if index:
                body.append("\n")
            body.append(row)
        box.update(body)

    def _refresh(self) -> None:
        for box, section in (
            (self._context_box, self._context_section),
            (self._metric_box, self._metric_section),
            (self._config_box, self._config_section),
        ):
            self._fill(box, *section())
