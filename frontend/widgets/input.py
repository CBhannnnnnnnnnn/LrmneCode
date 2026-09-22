"""输入区与内联命令 / 提及面板。

由 ``frontend/widgets.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any
from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widgets import (OptionList, TextArea)
from textual.widgets.option_list import Option
from .. import (mentions)
from ..commands import (SlashCommand)
from ..theme import (GLYPH_PANEL, S_DIM, S_FAINT, S_GHOST, S_REFERENCE, S_TEXT)


class InputArea(TextArea):
    """底部输入框：Enter 发送，Tab 补全/插入；面板可见时 ↑/↓ 导航。

    正文里的 ``@引用`` 会额外套一层底色（``S_REFERENCE``），让"我引用了哪些文件"在
    一行字里看得出来。实现只能落在 ``render_line`` 上：``TextArea`` 的着色入口是
    私有 highlighter，而它只认 tree-sitter 的语法规则，不是我们这种按 token 标。
    """

    class Submitted(Message):
        def __init__(self, area: "InputArea", text: str) -> None:
            super().__init__()
            self.area = area
            self.text = text

    class TabPressed(Message):
        pass

    class PaletteNavigate(Message):
        def __init__(self, direction: str) -> None:
            super().__init__()
            self.direction = direction

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    _REFERENCE_STYLE = Style.parse(S_REFERENCE)

    @property
    def palette_active(self) -> bool:
        """光标前有活着的触发词（``/`` 命令或 ``@`` 提及）时，↑↓ 归面板导航。"""
        text = self.text
        offset = mentions.cursor_offset(text, self.cursor_location)
        return mentions.triggered(text, offset) is not None

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        row = self.scroll_offset.y + y
        if row >= self.document.line_count:
            return strip
        line = self.document[row]
        spans: list[tuple[int, int]] = []
        for start, end in mentions.reference_spans(line):
            spans.extend(self._cells(line, start, end, row))
        if not spans:
            return strip
        return self._paint(strip, spans)

    def _cells(self, line: str, start: int, end: int, row: int) -> list[tuple[int, int]]:
        """字符区间 → 屏幕列区间（中文占两格、横向滚动要减掉）。

        光标正落在引用里时把它那一格切出去：底色盖住光标就看不见光标了。
        """
        scroll = self.scroll_offset.x
        left = max(0, cell_len(line[:start]) - scroll)
        right = cell_len(line[:end]) - scroll
        if row == self.cursor_location[0]:
            cursor = cell_len(line[: self.cursor_location[1]]) - scroll
            if left <= cursor < right:
                return [span for span in ((left, cursor), (cursor + 1, right)) if span[0] < span[1]]
        return [(left, right)] if left < right else []

    def _paint(self, strip: Strip, spans: list[tuple[int, int]]) -> Strip:
        """按引用边界把这一行切段，落在引用里的段换样式再拼回去。"""
        width = strip.cell_length
        cuts = sorted({cut for span in spans for cut in span if 0 < cut < width})
        cuts = [*cuts, width]
        pieces = list(strip.divide(cuts))
        painted: list[Strip] = []
        for index, piece in enumerate(pieces):
            start = cuts[index - 1] if index else 0
            if any(left <= start < right for left, right in spans):
                piece = self._chip(piece)
            painted.append(piece)
        return Strip.join(painted)

    def _chip(self, strip: Strip) -> Strip:
        """给一段套上引用样式。

        不能用 ``Strip.apply_style``：它是"传入样式在前、段自带样式在后"，而输入框的
        段自带底色（``$surface``）——引用那层底会被它盖掉，只剩加粗。这里在段级别把
        引用样式叠在**后**面。
        """
        segments = [
            Segment(
                segment.text,
                segment.style + self._REFERENCE_STYLE
                if segment.style
                else self._REFERENCE_STYLE,
                segment.control,
            )
            for segment in strip
        ]
        return Strip(segments, strip.cell_length)

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                # 不在这里清空：清空会立刻收起命令面板，而 _on_submit 还要读
                # 面板当前选中的命令，时序反过来 Enter 就等于没按面板走
                self.post_message(self.Submitted(self, text))
            return
        if key == "tab":
            event.stop()
            event.prevent_default()
            self.post_message(self.TabPressed())
            return
        if key in ("up", "down") and self.palette_active:
            event.stop()
            event.prevent_default()
            self.post_message(self.PaletteNavigate(key))
            return
        await super()._on_key(event)

    def set_text(self, text: str) -> None:
        self.clear()
        self.insert(text, location=(0, 0))


class InlinePalette(OptionList):
    """输入框上方的内联候选列表：``/`` 给命令，``@`` 给工作区文件与 skill。

    两种模式共用一套渲染，只是行文本与「选中后干什么」不同：命令是执行，提及是把
    ``@标签`` 插进正文。模式名交给 app 解释选中项（命令 → ``current_command``，
    提及 → ``current_mention``）。

    键盘焦点始终留在输入框（↑↓ 由输入框转成导航消息），所以高亮只能靠边框提示与行
    底色表达，面板自己不接受键盘——点一下也能选中（见 app 的 OptionSelected）。
    """

    HINT_COMMAND = "↑↓ 选择 · Enter 执行 · Tab 补全 · Esc 收起"
    HINT_MENTION = "↑↓ 选择 · Enter 进目录/引用 · Esc 收起"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mode = ""
        self._shown: list[Any] = []
        self.display = False
        self._header("命令", self.HINT_COMMAND)

    # -- 填充 --

    def show_commands(self, cmds: list[SlashCommand]) -> None:
        self.mode = "command"
        self._header("命令", self.HINT_COMMAND)
        self._fill(
            [Option(self._command_row(cmd), id=cmd.name) for cmd in cmds],
            cmds,
            "没有匹配的命令",
        )

    def show_mentions(self, items: list[mentions.Mention]) -> None:
        self.mode = "mention"
        self._header("提及", self.HINT_MENTION)
        self._fill(
            [
                Option(self._mention_row(item), id=str(index))
                for index, item in enumerate(items)
            ],
            items,
            "没有匹配的文件或 skill",
        )

    def _fill(self, options: list[Option], shown: list[Any], empty: str) -> None:
        """候选为空时留一行说明：什么都不显示会让人以为按键没生效。"""
        self.clear_options()
        self._shown = list(shown)
        for option in options:
            self.add_option(option)
        if not options:
            self.add_option(Option(Text(empty, style=S_FAINT), disabled=True))
        self.display = True
        self.highlighted = 0

    def hide(self) -> None:
        self.display = False

    def move_highlight(self, direction: str) -> None:
        count = self.option_count
        if not count:
            return
        current = self.highlighted if self.highlighted is not None else 0
        step = -1 if direction == "up" else 1
        self.highlighted = (current + step) % count

    # -- 当前项 --

    @property
    def current(self) -> Any:
        """高亮行的负载（命令或提及）；落在说明行上时为 None。"""
        index = self.highlighted
        if index is not None and 0 <= index < len(self._shown):
            return self._shown[index]
        return None

    @property
    def current_command(self) -> SlashCommand | None:
        item = self.current
        return item if isinstance(item, SlashCommand) else None

    @property
    def current_mention(self) -> mentions.Mention | None:
        item = self.current
        return item if isinstance(item, mentions.Mention) else None

    # -- 行渲染 --

    def _header(self, title: str, hint: str) -> None:
        self.border_title = Text(f"{GLYPH_PANEL} {title}")
        self.border_subtitle = hint

    @staticmethod
    def _command_row(cmd: SlashCommand) -> Text:
        row = Text()
        row.append(f"/{cmd.name}", style=S_TEXT)
        if cmd.aliases:
            row.append(f" (/{' /'.join(cmd.aliases)})", style=S_GHOST)
        row.append(f"  {cmd.description}", style=S_DIM)
        return row

    @staticmethod
    def _mention_row(item: mentions.Mention) -> Text:
        row = Text()
        # 目录行尾带斜杠：一眼分清"能钻进去的"和"能引用的"
        row.append(f"@{item.token}", style=S_TEXT)
        if item.note:
            row.append(f"  {item.note}", style=S_FAINT)
        return row
