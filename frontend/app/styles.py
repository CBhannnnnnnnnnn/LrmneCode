"""主应用样式表。由类属性 ``CSS`` 改为模块常量 ``APP_CSS``，内容逐字未动。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

APP_CSS = """
Screen {
    background: $background;
    color: $text;
}
Header {
    background: $background;
    color: $text;
}
Footer {
    background: $surface;
    color: $text-dim;
}
#main-row {
    height: 1fr;
}
#chat-area {
    width: 1fr;
    height: 1fr;
}
/* 右侧栏：与底部状态栏同一档底色，读起来是同一层"边框外的 chrome"。
   三类信息各占一个小框，类别名写在框上；宽度按最长的一行（目录 + 会话号）
   留够，不做省略。配置框钉在底部：上面两框会随上下文分段数长高，窗口不够高
   时该长高的自己滚，不能把不常变的档位挤出可视区。 */
#side {
    width: 34;
    height: 1fr;
    background: $surface;
    padding: 1 1 0 1;
}
#side-scroll {
    height: 1fr;
    scrollbar-size-vertical: 1;
    scrollbar-background: $surface;
    scrollbar-color: $border;
}
.side-box {
    width: 100%;
    height: auto;
    border: round $border;
    background: $surface;
    padding: 0 1;
    margin: 0 0 1 0;
    color: $text-dim;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    border-title-color: $text-dim;
    border-title-align: left;
}
#side-config {
    dock: bottom;
}
ChatView {
    height: 1fr;
    padding: 0 2;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
    scrollbar-background: $background;
    scrollbar-color: $border;
}
.user-message {
    margin: 1 0 0 0;
}
.notice {
    margin: 0;
}
/* 转录区的「一块」：左上标题写这是什么，右下状态写进行到哪。边框只有状态语义色
   会变，其余一律最弱一档灰——转录里的亮度应该来自正文，而不是容器。
   标题本身走「次要」那一档（$text-dim）：它是块的名字，太浅就读不出结构，
   提到正文那一档又会跟正文抢注意力。 */
.block {
    height: auto;
    border: round $border;
    background: $surface;
    padding: 0 1;
    margin: 1 0;
    border-title-color: $text-dim;
    border-title-align: left;
    border-subtitle-align: right;
    border-subtitle-color: $text-faint;
}
.block.state-running {
    border-subtitle-color: $warn;
}
.block.state-ok {
    border-subtitle-color: $ok;
}
.block.state-err {
    border-subtitle-color: $err;
}
.block.state-warn {
    border-subtitle-color: $warn;
}
/* 分类型描边：一眼看出这块是助手、工具还是思考，色相只到「分得出来」为止。
   审批单独一档最暖的陶土——它是唯一要用户表态的块。 */
.assistant-block {
    border: round $tint-assistant;
}
.tool-call {
    border: round $tint-tool;
}
.thinking {
    border: round $tint-thinking;
}
.tool-detail {
    margin: 0;
    color: $text-dim;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
.assistant-text {
    margin: 0;
}
.thinking-body {
    color: $text-faint;
    text-style: italic;
}
/* 系统提示：一行弱标记，正文（运行时上下文）不进转录 */
.hint {
    margin: 0;
}
/* 块内的折叠块（思考正文 / 改动正文 / 命令输出）：块本身已经画了边框，
   里层不能再铺一层 hkey 顶线与底色，否则一个块里又套出一个格子。 */
.block CollapsibleTitle,
.diff-fold CollapsibleTitle,
.result-fold CollapsibleTitle {
    padding: 0;
    background: transparent;
    text-style: none;
    color: $text-faint;
}
/* 只有用户落到这一行（hover 或点开）才提亮。CollapsibleTitle 默认在 hover/focus
   时铺一块主题色实心底（石墨主题下是浅灰块），点一下标题就跳出一大块亮色，抹掉。 */
.block CollapsibleTitle:hover,
.block CollapsibleTitle:focus,
.diff-fold CollapsibleTitle:hover,
.diff-fold CollapsibleTitle:focus,
.result-fold CollapsibleTitle:hover,
.result-fold CollapsibleTitle:focus {
    background: transparent;
    color: $text-dim;
}
.block Collapsible Contents {
    padding: 0;
    background: transparent;
}
.diff-fold,
.result-fold {
    padding: 0 0 0 2;
    margin: 0;
    border: none;
    background: transparent;
}
.diff-fold Contents,
.result-fold Contents {
    padding: 0 0 0 2;
    background: transparent;
}
.diff-body {
    color: $text-dim;
}
.tool-result {
    color: $text-faint;
    padding-left: 2;
    margin: 0;
}
.tool-result-full {
    color: $text-dim;
    margin: 0;
}
.card {
    border: round $border;
    background: $surface;
    padding: 0 1;
    margin: 1 0;
    border-title-color: $text-dim;
    border-title-align: left;
}
.approval-panel {
    height: auto;
    border: round $tint-approval;
    background: $surface;
    padding: 0 1;
    margin: 1 0;
    border-title-color: $warn;
    border-title-align: left;
}
.approval-panel.resolved {
    border: round $border;
    opacity: 0.55;
}
.approval-item {
    margin: 0;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
/* 扁平文字按钮：不画边框不填色，只在悬停/聚焦时抬一层底色 */
.approval-buttons {
    height: auto;
    margin: 1 0 0 0;
}
.approval-buttons Button {
    margin-right: 1;
    min-width: 0;
    height: 1;
    padding: 0 1;
    border: none;
    background: $surface;
    color: $text-dim;
}
.approval-buttons Button:hover,
.approval-buttons Button:focus {
    background: $surface-alt;
    color: $text;
}
#palette {
    height: auto;
    max-height: 12;
    margin: 0 1;
    border: round $border-strong;
    background: $surface;
    scrollbar-size-vertical: 1;
    border-title-color: $text-faint;
    border-title-align: left;
    border-subtitle-align: right;
    border-subtitle-color: $text-faint;
}
/* 当前项：抬一层底色 + 提亮文字，而不是 Textual 默认的实心亮灰块。
   $surface-alt 在石墨底上还是偏沉，用边框那一档灰才看得出光标在哪一行。 */
#palette > .option-list--option-highlighted {
    background: $border-strong;
    color: $text;
    text-style: bold;
}
#status {
    height: 1;
    background: $surface;
    padding: 0 1;
}
.status-left {
    width: 1fr;
    color: $text-dim;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
#input {
    height: 5;
    border: round $border;
    background: $surface;
    padding: 0 1;
    scrollbar-size-vertical: 1;
}
#input:focus {
    border: round $border-focus;
}
"""
