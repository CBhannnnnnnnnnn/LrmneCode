"""浮窗样式表。注意 Overlay 基类上的 SCOPED_CSS = False，它保住类型选择器不被改写。

由 ``frontend/overlay.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations


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
