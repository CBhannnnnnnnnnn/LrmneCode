"""模型配置浮窗：Schema 驱动的表单与「是否写回凭证」的提交规则。

由 ``frontend/overlay.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

from typing import Any
from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option
from ..theme import (S_DIM, S_FAINT, S_TEXT)
from .base import Overlay



THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high")
CONTEXT_SIZES: tuple[int, ...] = (8192, 32768, 65536, 131072, 200_000)


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
        # 只回填本浮窗列出的档位：/context 可设 400k / 1M，而这里没有该选项，
        # 直接赋值会触发 Select 的 InvalidSelectValueError，故不认识的档位保持「不改动」
        if flat.get("context_size") in CONTEXT_SIZES:
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
