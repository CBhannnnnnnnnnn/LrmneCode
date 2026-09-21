"""浮窗层：覆盖在聊天区之上的模态窗口。

``Overlay`` 负责面板骨架（圆角边框 + 标题 + 底部提示 + Esc 关闭），
具体浮窗只声明标题、主体部件与提交动作，视觉 token 全部取自 ``theme``。

首个使用者是模型配置浮窗：提供方列表与凭证字段完全由后端返回的
JSON Schema 生成（见 ``config.providers``），因此新增提供方无需改前端。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from .theme import (
    GLYPH_ACTIVE,
    GLYPH_PANEL,
    S_DIM,
    S_ERR,
    S_FAINT,
    S_LABEL,
    S_OK,
    S_TEXT,
    S_USER,
    S_WARN,
)

# 提示语配色：与 NoticeLine 的语义一致
_HINT_STYLE = {"info": S_FAINT, "success": S_OK, "error": S_ERR, "warn": S_WARN}

THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high")
CONTEXT_SIZES: tuple[int, ...] = (8192, 32768, 65536, 131072, 200_000)

# 浮窗样式随部件走（Textual 会收集部件类的 CSS，含继承），主题变量仍取自 theme
OVERLAY_CSS = """
Overlay {
    align: center middle;
    background: $background 70%;
}
.overlay-panel {
    width: 84;
    max-width: 92%;
    height: auto;
    max-height: 88%;
    border: round $border-strong;
    background: $surface;
    padding: 1 3;
}
.overlay-title {
    text-style: bold;
    color: $text;
    margin: 0 0 1 0;
}
.overlay-body {
    height: auto;
    max-height: 1fr;
}
.overlay-hint {
    width: 1fr;
    color: $text-faint;
    margin: 0;
    content-align-vertical: middle;
}
/* 主体固定高度：两侧子面板用 height: 100% 对齐，auto 会让它们无法计算 */
.model-overlay .overlay-body {
    height: 12;
}
#overlay-columns {
    height: 1fr;
}
.overlay-pane {
    height: 100%;
}
.provider-pane {
    width: 28;
    margin-right: 3;
}
.provider-pane OptionList {
    height: 1fr;
    background: $surface-sunk;
    border: round $border;
}
.provider-pane OptionList:focus {
    border: round $border-focus;
}
.form-pane {
    width: 1fr;
    scrollbar-size-vertical: 1;
}
/* Vertical 默认 height: 1fr，字段容器必须显式 auto，否则会被挤成 0 行 */
#credential-fields, .field {
    height: auto;
}
.field {
    margin: 0 0 1 0;
}
.field-label {
    margin: 0;
    color: $text-dim;
}
/* 表单里的分组标题：与上一组拉开一行，避免糊成一片 */
.form-pane > .group-label {
    margin: 1 0 0 0;
}
.field-note {
    color: $text-ghost;
    margin: 0;
}
/* 紧凑单行字段（compact=True）：浮窗里信息密度优先，焦点用底色区分 */
.form-pane Input, .form-pane SelectCurrent {
    background: $surface-sunk;
}
.form-pane Input:focus, .form-pane Select:focus > SelectCurrent {
    background: $surface-alt;
}
.overlay-footer {
    height: auto;
    margin: 1 0 0 0;
}
/* 底部动作键：与审批面板同一套扁平写法——不画框不填亮色，只在悬停/聚焦时抬一层 */
.overlay-footer Button {
    min-width: 0;
    height: 1;
    padding: 0 2;
    border: none;
    background: $surface-alt;
    color: $text-dim;
}
.overlay-footer Button:hover,
.overlay-footer Button:focus {
    background: $border-strong;
    color: $text;
}
/* 会话浮窗：单列列表，高度固定后由列表自己滚动 */
.session-overlay .overlay-body {
    height: 14;
}
.session-overlay OptionList {
    height: 1fr;
    background: $surface-sunk;
    border: round $border;
}
.session-overlay OptionList:focus {
    border: round $border-focus;
}
/* 选择 / 输入浮窗：单列且随内容伸缩，列表过长自己滚，不占死高度 */
.picker-overlay .overlay-body,
.prompt-overlay .overlay-body {
    height: auto;
}
.picker-overlay OptionList {
    height: auto;
    max-height: 12;
    background: transparent;
    border: none;
}
.picker-overlay OptionList:focus {
    border: none;
}
.prompt-caption {
    color: $text-dim;
    margin: 0 0 1 0;
}
.prompt-overlay Input {
    background: $surface-sunk;
    border: round $border;
}
.prompt-overlay Input:focus {
    border: round $border-focus;
}
"""


class Overlay(ModalScreen):
    """模态浮窗基类：居中面板 + 标题 + 主体 + 提示行。"""

    CSS = OVERLAY_CSS
    # 关掉作用域：Textual 默认把部件类 CSS 的每条规则前面再缀上**子类自己**的类型名
    # （``ModelConfigOverlay.CSS`` 里的 ``Overlay {…}`` 会被改写成
    # ``ModelConfigOverlay Overlay {…}``，于是永远匹配不到浮窗自己）。
    # 本模块的规则本来就都带着 overlay 专属选择器，全局注册即可。
    SCOPED_CSS = False
    # priority：浮窗作为模态必须独占 Esc，否则会被 App 的 Esc 抢走
    BINDINGS = [Binding("escape", "close_overlay", "关闭", priority=True)]

    TITLE_TEXT = ""
    HINT_TEXT = "Esc 关闭"
    PANEL_CLASS = ""

    def compose(self) -> ComposeResult:
        classes = "overlay-panel" + (f" {self.PANEL_CLASS}" if self.PANEL_CLASS else "")
        with Vertical(classes=classes):
            yield Static(
                Text(f"{GLYPH_PANEL} {self.TITLE_TEXT}", style=S_TEXT),
                classes="overlay-title",
            )
            with VerticalScroll(classes="overlay-body"):
                for widget in self.build_body():
                    yield widget
            with Horizontal(classes="overlay-footer"):
                yield Static(Text(self.HINT_TEXT, style=S_FAINT), classes="overlay-hint")
                for widget in self.build_footer():
                    yield widget

    def build_body(self) -> list[Any]:
        """子类返回主体部件列表（可滚动区域）。"""
        return []

    def build_footer(self) -> list[Any]:
        """子类返回底部动作部件（常驻可见，不随主体滚动）。"""
        return []

    def on_mount(self) -> None:
        # compose 与 mount 是两条独立消息：on_mount 触发时子部件可能尚未生成，
        # 需要查询 DOM 的初始化因此放在 mount_composed_widgets 之后。
        pass

    async def mount_composed_widgets(self, widgets: list[Any]) -> None:
        await super().mount_composed_widgets(widgets)
        self.on_body_ready()

    def on_body_ready(self) -> None:
        """主体部件挂载完成后的初始化钩子（子类覆写）。"""

    def query_body(self, selector: str, expect: type | None = None) -> Any:
        """查询主体部件；组合尚未完成时返回 None，而不是抛 NoMatches。"""
        try:
            return self.query_one(selector, expect)
        except NoMatches:
            return None

    def set_hint(self, text: str, level: str = "info") -> None:
        hint = self.query_body(".overlay-hint", Static)
        if hint is None:
            return
        hint.update(Text(text, style=_HINT_STYLE.get(level, S_FAINT)))

    def action_close_overlay(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True)
class Choice:
    """Picker 里的一行：展示文本 + 弱化说明 + 选中后回传的值。"""

    label: str
    note: str = ""
    value: Any = None


class Picker(Overlay):
    """选择浮窗：↑↓ 挑一项，回车确认。

    凡是「在几个取值里选一个」的命令都走这里，命令本身一律不带参数。
    候选项可以异步填充（列表往往来自后端回执）。
    """

    HINT_TEXT = "↑↓ 选择 · Enter 确认 · Esc 取消"
    PANEL_CLASS = "picker-overlay"

    def __init__(
        self,
        kind: str,
        title: str,
        on_select: Callable[[Any], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.TITLE_TEXT = title
        self.kind = kind
        self._on_select = on_select
        self._choices: list[Choice] = []
        self._current: Any = None
        self._mounted = False

    def build_body(self) -> list[Any]:
        return [OptionList(id="picker-list")]

    def on_body_ready(self) -> None:
        self._mounted = True
        self._render_choices()

    def set_choices(
        self, choices: list[Choice], current: Any = None
    ) -> None:
        """填充或更新候选项；主体还没就绪就先存着，就绪那一遍统一渲染。"""
        self._choices = list(choices)
        self._current = current
        if self._mounted:
            self._render_choices()

    def _render_choices(self) -> None:
        listing = self.query_body("#picker-list", OptionList)
        if listing is None:
            return
        listing.clear_options()
        # id 用下标而不是值：值可能带空格或符号，不是合法的查询标识
        for index, choice in enumerate(self._choices):
            listing.add_option(Option(self._label(choice), id=str(index)))
        if not listing.options:
            listing.add_option(Option(Text("暂无可选项", style=S_FAINT), disabled=True))
            self.set_hint("没有可选项", "info")
            return
        listing.highlighted = self._index_of_current()
        listing.focus()

    def _label(self, choice: Choice) -> Text:
        active = choice.value == self._current
        row = Text()
        row.append(f"{GLYPH_ACTIVE} " if active else "  ")
        row.append(choice.label, style=S_USER if active else S_TEXT)
        if choice.note:
            row.append(f"  {choice.note}", style=S_FAINT)
        return row

    def _index_of_current(self) -> int:
        for index, choice in enumerate(self._choices):
            if choice.value == self._current:
                return index
        return 0

    @on(OptionList.OptionSelected, "#picker-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        try:
            index = int(str(event.option_id))
        except (TypeError, ValueError):
            return
        if not 0 <= index < len(self._choices):
            return
        value = self._choices[index].value
        self.dismiss(value)
        if self._on_select is not None:
            self._on_select(value)


class Prompt(Overlay):
    """单行输入浮窗：路径这类自由文本用它，替代「/命令 参数」的写法。"""

    HINT_TEXT = "回车确认 · Esc 取消"
    PANEL_CLASS = "prompt-overlay"

    def __init__(
        self,
        kind: str,
        title: str,
        caption: str = "",
        value: str = "",
        placeholder: str = "",
        on_submit: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.TITLE_TEXT = title
        self.kind = kind
        self._caption = caption
        self._value = value
        self._placeholder = placeholder
        self._on_submit = on_submit

    def build_body(self) -> list[Any]:
        return [
            Static(Text(self._caption, style=S_DIM), classes="prompt-caption")
            if self._caption
            else Static("", classes="prompt-caption"),
            Input(placeholder=self._placeholder, id="prompt-input"),
        ]

    def on_body_ready(self) -> None:
        field = self.query_body("#prompt-input", Input)
        if field is None:
            return
        field.value = self._value
        field.focus()

    @on(Input.Submitted, "#prompt-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit(event.value.strip())

    def _submit(self, value: str) -> None:
        if not value:
            self.set_hint("不能留空", "warn")
            return
        self.dismiss(value)
        if self._on_submit is not None:
            self._on_submit(value)


class ModelConfigOverlay(Overlay):
    """模型配置浮窗：选提供方 → 填凭证 → 填模型名，一次提交。

    凭证字段由 Schema 生成；提交规则（避免「只改模型名要重填 key」）：
    仅当换了提供方、或用户确实填了凭证字段时，才把 provider 一并写回，
    否则只提交模型名 / 思考级别 / 上下文窗口，沿用已配置的凭证。
    """

    TITLE_TEXT = "模型配置"
    HINT_TEXT = "输入框回车 或 保存 提交 · Esc 关闭"
    PANEL_CLASS = "model-overlay"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._schemas: list[dict] = []
        self._current: dict = {}
        self._provider_types: list[str] = []
        self._inputs: dict[str, Input] = {}
        self._initial: dict[str, str] = {}
        self._required: set[str] = set()
        self._mounted = False

    # ---------- 骨架 ----------

    def build_body(self) -> list[Any]:
        return [
            Horizontal(
                Vertical(
                    Static(Text("提供方 · ↑↓ 选择", style=S_DIM), classes="field-label"),
                    OptionList(id="provider-list"),
                    classes="overlay-pane provider-pane",
                ),
                VerticalScroll(
                    Static(
                        Text("凭证 · 等待后端返回提供方列表…", style=S_FAINT),
                        id="form-caption",
                        classes="field-label",
                    ),
                    Vertical(id="credential-fields"),
                    Static(
                        Text("模型名", style=S_DIM),
                        classes="field-label group-label",
                    ),
                    Input(
                        placeholder="如 claude-sonnet-4-5 / gpt-4o",
                        compact=True,
                        id="model-name",
                    ),
                    Static(
                        Text("思考级别", style=S_DIM),
                        classes="field-label group-label",
                    ),
                    Select(
                        [(level, level) for level in THINKING_LEVELS],
                        allow_blank=True,
                        prompt="不改动",
                        compact=True,
                        id="thinking-level",
                    ),
                    Static(
                        Text("上下文窗口", style=S_DIM),
                        classes="field-label group-label",
                    ),
                    Select(
                        [(f"{size // 1024}k", size) for size in CONTEXT_SIZES],
                        allow_blank=True,
                        prompt="不改动",
                        compact=True,
                        id="context-size",
                    ),
                    classes="overlay-pane form-pane",
                ),
                id="overlay-columns",
            ),
        ]

    def build_footer(self) -> list[Any]:
        return [Button("保存", compact=True, id="model-save")]

    def on_body_ready(self) -> None:
        self._mounted = True
        app = self.app
        app.send("config.providers", {})
        app.send("config.get", {})
        self._render_providers()
        self._apply_config()

    # ---------- 数据入口（由 results.py 的回执渲染器调用） ----------

    def load_providers(self, schemas: list[dict]) -> None:
        """存下 Schema 并重建列表；主体未就绪时留给 on_body_ready 那一遍。"""
        self._schemas = [s for s in schemas if isinstance(s, dict)]
        self._provider_types = [self._type_of(s) for s in self._schemas]
        if self._mounted:
            self._render_providers()
            self._apply_config()

    def load_config(self, flat: dict) -> None:
        self._current = dict(flat)
        if self._mounted:
            self._apply_config()

    def _apply_config(self) -> None:
        """把当前配置回填到表单。"""
        listing = self.query_body("#provider-list", OptionList)
        if listing is None:
            return

        provider_type = str(self._current.get("provider_type") or "")
        if provider_type in self._provider_types:
            listing.highlighted = self._provider_types.index(provider_type)
            self._rebuild_credential_fields(provider_type)
        # 当前提供方不在可选列表里（首次配置 / 已废弃的提供方）：保留默认选中的那个

        model_input = self.query_body("#model-name", Input)
        if model_input is not None:
            model_input.value = (
                str(self._current["model"]) if self._current.get("model") else ""
            )

        flat = self._current
        if flat.get("thinking_level") in THINKING_LEVELS:
            self.query_one("#thinking-level", Select).value = flat["thinking_level"]
        if isinstance(flat.get("context_size"), int):
            self.query_one("#context-size", Select).value = flat["context_size"]

    # ---------- 提供方列表 ----------

    @staticmethod
    def _type_of(schema: dict) -> str:
        return str(schema.get("properties", {}).get("type", {}).get("const") or "")

    def _render_providers(self) -> None:
        if not self._mounted:
            return
        listing = self.query_body("#provider-list", OptionList)
        if listing is None:
            return
        listing.clear_options()
        for schema in self._schemas:
            provider_type = self._type_of(schema)
            # 只放展示名：30 列窄栏里带 provider_type 会折行，类型在右侧表单标题里可见
            title = str(schema.get("title") or provider_type)
            listing.add_option(Option(Text(title, style=S_TEXT), id=provider_type))
        if self._provider_types:
            listing.highlighted = 0
            self._rebuild_credential_fields(self._provider_types[0])

    @on(OptionList.OptionHighlighted, "#provider-list")
    def _on_provider_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._rebuild_credential_fields(str(event.option_id or ""))

    @on(OptionList.OptionSelected, "#provider-list")
    def _on_provider_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._rebuild_credential_fields(str(event.option_id or ""))
        # Enter 落在「选提供方」上：顺势把焦点交给第一个待填字段
        if self._inputs:
            next(iter(self._inputs.values())).focus()
        else:
            self.query_one("#model-name", Input).focus()

    # ---------- 凭证字段 ----------

    def _rebuild_credential_fields(self, provider_type: str) -> None:
        """按选中提供方的 Schema 重建字段；字段值随 schema 默认值预填。"""
        if not self._mounted:
            return
        container = self.query_body("#credential-fields", Vertical)
        if container is None:
            return
        container.remove_children()
        self._inputs = {}
        self._initial = {}
        self._required = set()

        index = self._provider_types.index(provider_type) if provider_type in self._provider_types else -1
        caption = self.query_one("#form-caption", Static)
        if index < 0:
            caption.update(Text("凭证 · 未选择提供方（将沿用当前配置）", style=S_FAINT))
            return

        schema = self._schemas[index]
        self._required = set(schema.get("required") or [])
        properties: dict = schema.get("properties") or {}
        configured = self._configured_suffix()

        for name, spec in properties.items():
            if name in ("id", "type") or not isinstance(spec, dict):
                continue
            container.mount(self._credential_field(name, spec, configured))
        container.mount(
            Static(Text("留空的密码项表示沿用已配置的值", style=S_FAINT), classes="field-note"),
        )
        caption.update(
            Text(f"凭证 · {schema.get('title') or provider_type}", style=S_DIM),
        )

    def _credential_field(self, name: str, spec: dict, configured: str) -> Vertical:
        secret = spec.get("format") == "password"
        label = str(spec.get("title") or name)
        if name in self._required:
            label += " *"

        default = spec.get("default")
        placeholder = str(spec.get("description") or name)
        if secret and configured:
            placeholder = f"已配置 ****{configured}（留空则沿用）"
            default = ""
        elif default in (None, ""):
            default = ""

        field = Input(
            value=str(default),
            placeholder=placeholder[:72],
            password=secret,
            compact=True,
            id=f"cred-{name}",
        )
        self._inputs[name] = field
        # 记下预填值：提交时用「与预填不同」判断用户是否真的改过这一项
        self._initial[name] = str(default)
        return Vertical(
            Static(Text(label, style=S_DIM), classes="field-label"),
            field,
            classes="field",
        )

    def _configured_suffix(self) -> str:
        credential = self._current.get("credential")
        if not isinstance(credential, dict):
            return ""
        api_key = credential.get("api_key")
        if not isinstance(api_key, dict) or not api_key.get("configured"):
            return ""
        return str(api_key.get("suffix") or "")

    # ---------- 提交 ----------

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit()

    @on(Button.Pressed, "#model-save")
    def _on_save_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.submit()

    def submit(self) -> None:
        """组装一次 config.apply_model。

        后端的 provider 写入是**整体替换** credential，且密钥不会回传到前端，
        因此这里只在能凑出完整凭证时才写回 provider；否则宁可只提交模型名。
        """
        model = self.query_one("#model-name", Input).value.strip()
        if not model:
            self.set_hint("请填写模型名", "error")
            self.query_one("#model-name", Input).focus()
            return

        listing = self.query_one("#provider-list", OptionList)
        index = listing.highlighted
        provider_type = (
            self._provider_types[index]
            if index is not None and 0 <= index < len(self._provider_types)
            else ""
        )
        current_type = str(self._current.get("provider_type") or "")

        values = {name: w.value.strip() for name, w in self._inputs.items()}
        # 只有「与预填值不同」才算用户动过，否则 schema 默认值会被误判成改动
        changed = {
            name for name, value in values.items()
            if value != self._initial.get(name, "")
        }
        filled = {name for name, value in values.items() if value}
        complete = self._required <= filled

        params: dict[str, Any] = {"model": model}
        thinking = self.query_one("#thinking-level", Select).value
        if thinking in THINKING_LEVELS:
            params["thinking_level"] = thinking
        context = self.query_one("#context-size", Select).value
        if isinstance(context, int):
            params["context_size"] = context

        if provider_type and provider_type != current_type:
            if not complete:
                self.set_hint(
                    f"缺少必填项：{', '.join(sorted(self._required - filled))}", "error"
                )
                return
            params["provider_type"] = provider_type
            params["credential"] = {
                name: value for name, value in values.items() if value
            }
        elif changed:
            if not complete:
                self.set_hint(
                    "修改凭证字段需同时重填 API Key（密钥不回传前端）", "error"
                )
                return
            params["provider_type"] = provider_type
            params["credential"] = {
                name: value for name, value in values.items() if value
            }
        # 其余情况：沿用当前提供方与凭证，只提交模型相关字段

        self.app.send("config.apply_model", params)
        self.set_hint("正在写入配置…", "warn")


# ---------- 会话切换 ----------

# 选项 id 前缀：区分「本进程已打开」与「磁盘存档」，两类动作不同
_OPEN_PREFIX = "open:"
_DISK_PREFIX = "disk:"

_HINT = "↑↓ 选择 · Enter 切换 / 载入 · x 关闭本进程会话 · d 删除存档 · Esc 关闭"


def _format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _entry_index(listing: OptionList, current: str) -> int:
    """光标优先落在当前会话那一行；没有则落在第一个可选行。"""
    first_enabled: int | None = None
    for index, option in enumerate(listing.options):
        if option.disabled:
            continue
        if option.id == _OPEN_PREFIX + current:
            return index
        if first_enabled is None:
            first_enabled = index
    return first_enabled if first_enabled is not None else 0


class SessionSwitcherOverlay(Overlay):
    """会话切换浮窗：上半是本进程已打开的会话，下半是磁盘存档。

    两者的「进入」语义不同，所以分成两组：
    - 本进程会话：直接切前台，草稿与滚动位置都还在。
    - 磁盘存档：后端 ``session.resume`` 固定写进**当前** cid，因此先开一个空会话
      再把存档读进去，不会覆盖用户正开着的会话。
    """

    TITLE_TEXT = "会话"
    HINT_TEXT = _HINT
    PANEL_CLASS = "session-overlay"

    BINDINGS = [
        # 基类靠 Overlay.CSS + 继承拿到弹窗样式，但 BINDINGS 是整体覆盖，Esc 要重申
        Binding("escape", "close_overlay", "关闭", priority=True),
        Binding("x", "close_session", "关闭会话"),
        Binding("d", "delete_disk", "删除存档"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._open: list[Any] = []
        self._disk: list[dict] = []
        self._confirm = ""
        self._mounted = False

    # ---------- 骨架 ----------

    def build_body(self) -> list[Any]:
        return [OptionList(id="session-list")]

    def on_body_ready(self) -> None:
        self._mounted = True
        self.app.send("session.list", {})
        self._render_sessions()

    # ---------- 数据入口（由 results.py 的回执渲染器调用） ----------

    def load_sessions(self, items: list) -> None:
        self._disk = [item for item in items if isinstance(item, dict)]
        if self._mounted:
            self._render_sessions()

    def forget_disk(self, cid: str) -> None:
        """删除成功后立刻摘掉该行，不再往返一次 session.list（两者都是 PLAIN 模式）。"""
        before = len(self._disk)
        self._disk = [item for item in self._disk if item.get("cid") != cid]
        if self._mounted and len(self._disk) != before:
            self._render_sessions()

    # ---------- 渲染 ----------

    def _render_sessions(self) -> None:
        if not self._mounted:
            return
        listing = self.query_body("#session-list", OptionList)
        if listing is None:
            return
        listing.clear_options()
        self._open = list(self.app.store.all())
        open_cids = {conv.cid for conv in self._open}
        current = self.app.store.current_cid

        if self._open:
            listing.add_option(Option(Text("本进程", style=S_LABEL), disabled=True))
            for conv in self._open:
                listing.add_option(
                    Option(self._open_label(conv, current), id=_OPEN_PREFIX + conv.cid),
                )
        if self._disk:
            listing.add_option(Option(Text("磁盘存档", style=S_LABEL), disabled=True))
            for item in self._disk:
                cid = str(item.get("cid") or "")
                if cid:
                    listing.add_option(
                        Option(self._disk_label(item, open_cids), id=_DISK_PREFIX + cid),
                    )

        if not listing.options:
            listing.add_option(Option(Text("暂无会话", style=S_FAINT), disabled=True))
            self.set_hint("暂无可切换的会话", "info")
            return

        listing.highlighted = _entry_index(listing, current)
        listing.focus()

    def _open_label(self, conv: Any, current: str) -> Text:
        active = conv.cid == current
        row = Text(f"{GLYPH_ACTIVE} " if active else "  ")
        row.append(conv.cid, style=S_USER if active else S_TEXT)
        if active:
            row.append("  当前", style=S_FAINT)
        state = conv.run_state
        if state != "idle":
            row.append(f"  {state}", style=S_WARN if state == "working" else S_FAINT)
        if conv.tokens_in or conv.tokens_out:
            row.append(
                f"  ↑{_format_tokens(conv.tokens_in)} ↓{_format_tokens(conv.tokens_out)}",
                style=S_FAINT,
            )
        return row

    def _disk_label(self, item: dict, open_cids: set[str]) -> Text:
        cid = str(item.get("cid") or "?")
        row = Text("  ")
        row.append(cid, style=S_TEXT)
        if cid in open_cids:
            row.append("  已打开", style=S_FAINT)
        summary = str(item.get("summary") or "")
        if summary:
            row.append(f"  {_shorten(summary, 36)}", style=S_DIM)
        modified = item.get("modified")
        if isinstance(modified, (int, float)):
            stamp = datetime.fromtimestamp(modified).strftime("%m-%d %H:%M")
            row.append(f"  {stamp}", style=S_FAINT)
        return row

    # ---------- 动作 ----------

    def _highlighted_id(self) -> str:
        listing = self.query_body("#session-list", OptionList)
        option = listing.highlighted_option if listing is not None else None
        return str(option.id or "") if option is not None else ""

    @on(OptionList.OptionHighlighted, "#session-list")
    def _on_highlight(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if self._confirm:
            self._confirm = ""
            self.set_hint(self.HINT_TEXT)

    @on(OptionList.OptionSelected, "#session-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        target = str(event.option_id or "")
        if target.startswith(_OPEN_PREFIX):
            cid = target[len(_OPEN_PREFIX) :]
            if self.app.store.get(cid) is not None:
                self.app.activate(cid)
            self.dismiss(None)
        elif target.startswith(_DISK_PREFIX):
            self.app.resume_conversation(target[len(_DISK_PREFIX) :])
            self.dismiss(None)

    def action_close_session(self) -> None:
        target = self._highlighted_id()
        if not target.startswith(_OPEN_PREFIX):
            self.set_hint("只有本进程已打开的会话可以关闭", "warn")
            return
        cid = target[len(_OPEN_PREFIX) :]
        self._confirm = ""
        self.app.close_conversation(cid)
        self._render_sessions()
        self.set_hint(f"已关闭 {cid}（磁盘存档未删除）", "success")

    def action_delete_disk(self) -> None:
        target = self._highlighted_id()
        if not target.startswith(_DISK_PREFIX):
            self.set_hint("只有磁盘存档可以删除", "warn")
            return
        cid = target[len(_DISK_PREFIX) :]
        if self._confirm != cid:
            # 删存档不可恢复，所以要求再按一次同一个键确认
            self._confirm = cid
            self.set_hint(f"再按一次 d 确认删除 {cid} 的磁盘存档（不可恢复）", "warn")
            return
        self._confirm = ""
        self.app.delete_session(cid)
        self.set_hint(f"正在删除 {cid}…", "warn")
