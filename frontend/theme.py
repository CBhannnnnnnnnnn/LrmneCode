"""集中式视觉设计系统：石墨中性。

设计原则
- 正文与结构全部走中性灰阶；彩色只承担状态语义（成功 / 失败 / 等待），且低饱和。
- 分块描边各带一档复古色相，只到「看得出类别」为止，不当装饰用。
- 层级靠字重、缩进、竖线、留白与边框表达，不靠颜色堆叠。
- 代码高亮允许保留少量彩度，但收敛在同一色族内，避免与状态色抢注意力。

约定
- 组件与 CSS 一律引用本模块的 token：Python 侧用常量，CSS 侧用 ``$变量``。
- 禁止在组件或 CSS 里再写裸 hex。

本模块只提供颜色 / 字形 / 间距 / 现成样式，不持有任何 widget 状态。
"""

from __future__ import annotations

from textual.theme import Theme

# ---------- 底色层 ----------

BG = "#0d0d0f"  # 屏幕底
SURFACE = "#16161a"  # 面板 / 卡片 / 输入框
SURFACE_ALT = "#1c1c21"  # 抬升层：悬停、选中、浮窗标题栏
SURFACE_SUNK = "#0a0a0c"  # 下沉层：代码块、工具结果

# ---------- 边框 ----------

BORDER = "#2a2a30"
BORDER_STRONG = "#3a3a42"
BORDER_FOCUS = "#5a5a66"

# ---------- 分块描边（复古低饱和） ----------
# 与 $border 同一明度档，只带一点色相：让「这是什么块」从边框就看得出来，
# 又不把注意力从正文抢走。转录里的亮度始终留给正文。

TINT_ASSISTANT = "#3d4a55"  # 靛灰：助手回复
TINT_TOOL = "#48493a"  # 橄榄灰：工具调用
TINT_THINKING = "#4a3f52"  # 紫灰：思考
TINT_APPROVAL = "#54453a"  # 陶土：审批（要用户表态，最暖的一档）

# ---------- 文字灰阶 ----------

TEXT = "#e6e6e9"  # 正文
TEXT_DIM = "#8a8a93"  # 次要信息
TEXT_FAINT = "#5a5a63"  # 弱化：元信息、提示
TEXT_GHOST = "#3a3a42"  # 占位符、失活

# ---------- 状态语义（唯一允许的彩色，低饱和） ----------

OK = "#7fb069"
ERR = "#c05c5c"
WARN = "#b8925a"

# ---------- 代码高亮（与石墨同族，仅保留可辨识度） ----------

CODE_COMMENT = TEXT_FAINT
CODE_KEYWORD = "#8f9aab"
CODE_STRING = "#9aab8f"
CODE_NUMBER = "#b0a08f"

# ---------- 间距 / 结构 ----------

GUTTER = 2  # 聊天流左侧缩进，结果行的对齐基准
GAP_BLOCK = 1  # 块与块之间的空行
PAD_PANEL = 1  # 面板 / 卡片内边距
INDENT_RESULT = 2  # 工具结果相对工具调用行的缩进

# ---------- 字形 ----------
# 统一用等宽安全的几何符号；不用 emoji（终端里宽度与配色都不可控）

GLYPH_USER = "❯"
# 工具块标题前的图标按类别分：文件类给「一页带横线」，终端类给指针，其余中性点。
# 别用 ⏺（U+23FA）——它是 emoji 呈现，终端会画成彩色圆块，和整体灰阶观感直接打架。
GLYPH_TOOL = "●"
GLYPH_FILE = "▤"
GLYPH_SHELL = "▸"
# 结果行前缀：用盒描线字符，各终端字体都覆盖得到（⎿ 一类会退化成竖线）
GLYPH_RESULT = "└"
# 被用户中断的调用：与「已暂停」同一语义，状态行前缀
GLYPH_INTERRUPT = "⊘"
GLYPH_BULLET = "·"
GLYPH_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

GLYPH_NOTICE = {
    "info": GLYPH_BULLET,
    "success": "✓",
    "error": "✗",
    "warn": "!",
    "busy": "◐",
}

GLYPH_THINKING = "◇"
GLYPH_HINT = "▪"
GLYPH_PANEL = "◆"
GLYPH_QUEUED = "⋯"
GLYPH_ACTIVE = "●"
GLYPH_ELLIPSIS = "…"
# 进度条（上下文占用）：实心/空心格，等宽安全
GLYPH_METER_FULL = "█"
GLYPH_METER_EMPTY = "░"

# ---------- 现成样式（供 rich.Text 使用） ----------

S_TEXT = TEXT
S_DIM = TEXT_DIM
S_FAINT = TEXT_FAINT
S_GHOST = TEXT_GHOST

S_USER = f"bold {TEXT}"
S_SLASH = f"bold {WARN}"
S_TOOL = f"bold {TEXT}"
S_LABEL = f"bold {TEXT_DIM}"
S_OK = OK
S_ERR = ERR
S_WARN = WARN
S_META = TEXT_FAINT
S_CODE = f"{TEXT} on {SURFACE_SUNK}"

# 内部过程（工具调用行、结果、系统提示）：一律最浅一档，弱到不抢正文的注意力；
# 只有用户点开折叠块，正文才提亮到 S_INTERNAL_OPEN。两档都不带颜色，保持中性。
S_INTERNAL = TEXT_FAINT
S_INTERNAL_OPEN = TEXT_DIM

S_DIFF_ADD = OK
S_DIFF_DEL = ERR
S_DIFF_META = TEXT_FAINT

# 输入框里的 @引用：抬一层底 + 加粗，像一枚内嵌的筹码——与面板高亮同一档底色，
# 不引入新颜色，但一眼能把它和旁边手打的正文分开。
S_REFERENCE = f"bold {TEXT} on {BORDER_STRONG}"


# ---------- Textual 主题 ----------

GRAPHITE = Theme(
    name="graphite",
    primary="#a0a0a8",  # 焦点边框 / 选中态：中性亮灰，不用彩色
    secondary="#6a6a74",
    accent="#cfcfd6",  # 强调：近白，仅用于需要跳出的少量元素
    foreground=TEXT,
    background=BG,
    surface=SURFACE,
    panel=SURFACE_ALT,
    success=OK,
    warning=WARN,
    error=ERR,
    dark=True,
    text_alpha=0.95,
    variables={
        # 只暴露 Textual 内置变量没有的灰阶 / 语义档位，避免覆盖 $text、$border、
        # $background、$surface 这些主题自带变量而造成全局副作用。
        "surface-alt": SURFACE_ALT,
        "surface-sunk": SURFACE_SUNK,
        "border-strong": BORDER_STRONG,
        "border-focus": BORDER_FOCUS,
        "tint-assistant": TINT_ASSISTANT,
        "tint-tool": TINT_TOOL,
        "tint-thinking": TINT_THINKING,
        "tint-approval": TINT_APPROVAL,
        "text-dim": TEXT_DIM,
        "text-faint": TEXT_FAINT,
        "text-ghost": TEXT_GHOST,
        "ok": OK,
        "err": ERR,
        "warn": WARN,
        "code-keyword": CODE_KEYWORD,
        "code-string": CODE_STRING,
        "code-number": CODE_NUMBER,
        "code-comment": CODE_COMMENT,
    },
)
