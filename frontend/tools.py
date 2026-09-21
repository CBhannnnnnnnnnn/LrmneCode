"""工具调用的展示层：tool.call 的参数 JSON → 人读摘要与正文。

只做「参数 → rich 文本」的纯转换，不持有任何部件状态：``ToolCallView``（执行中）
与 ``ApprovalPanel``（待审批）共用同一套渲染，同一件事在两处长得一样。

参数是流式 JSON 片段，完整解析之前只能捞到已经闭合的字符串字段；摘要因此按
「解析出多少就显示多少」渐进呈现，而正文（彩色 diff）只在参数完整时才给。
"""

from __future__ import annotations

import json
import re
from typing import Any

from rich.text import Text

from .theme import (
    GLYPH_FILE,
    GLYPH_SHELL,
    GLYPH_TOOL,
    S_DIFF_ADD,
    S_DIFF_DEL,
    S_DIFF_META,
    S_DIM,
    S_FAINT,
    S_TEXT,
)

# 正文最多展示的行数：超长时首尾各留一半，中间标注省了多少行
MAX_DIFF_LINES = 12
# 结果折叠标题里的预览长度：一行放得下，又不会把标题挤成一堵墙
RESULT_PREVIEW = 48
# 参数缓冲上限：足够容纳完整 diff 的 JSON，又不至于被超大 content 撑爆
ARG_BUFFER = 40_000

_SHELL_TOOLS = frozenset({"bash", "powershell", "sh", "shell", "zsh", "cmd"})

# 读写与检索文件的工具：标题前给文件图标，而不是通用点
_FILE_TOOLS = frozenset({"read", "write", "edit", "glob", "grep", "ls"})

# 流式片段里可安全捞取的字段（字符串值必须已闭合，否则匹配不到）
_TEXT_KEYS = (
    "file_path",
    "path",
    "command",
    "pattern",
    "description",
    "old_string",
    "new_string",
    "content",
)
_STRING_FIELD = re.compile(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def display_name(name: str) -> str:
    return str(name or "tool")


def icon_of(name: str) -> str:
    """工具块标题前的图标：文件类给文件、终端类给指针，其余（如 MCP）给中性点。"""
    key = display_name(name).lower()
    if key in _SHELL_TOOLS:
        return GLYPH_SHELL
    if key in _FILE_TOOLS:
        return GLYPH_FILE
    return GLYPH_TOOL


def load_args(raw: str) -> dict[str, Any] | None:
    """完整 JSON 参数；仍在流入或不是 JSON 时返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_args(raw: str) -> dict[str, Any]:
    """参数的最佳可用形态：完整 JSON 优先，否则捞出已到达的字符串字段。"""
    parsed = load_args(raw)
    if parsed is not None:
        return parsed
    found: dict[str, Any] = {}
    for key, value in _STRING_FIELD.findall(raw or ""):
        if key in _TEXT_KEYS and key not in found:
            found[key] = _unescape(value)
    return found


def coerce_args(value: Any) -> dict[str, Any]:
    """参数可能是 JSON 字符串（协议的 ToolCallBlock.input）或已是 dict。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return parse_args(value)
    return {}


# ---------- 一行摘要 ----------


def summary(name: str, args: dict[str, Any]) -> Text:
    """状态行摘要：工具名 + 该工具最关键的那几个参数。"""
    text = Text()
    text.append(display_name(name), style=S_TEXT)
    detail = detail_of(name, args)
    if detail:
        text.append(f"  {detail}", style=S_DIM)
    return text


def detail_of(name: str, args: dict[str, Any]) -> str:
    """该工具最关键的那几个参数，压成一行。

    转录里的工具块把「工具名 + 这一步在干什么」写在边框标题上（见
    ``widgets.ToolCallView``），那里只要这一行正文，不需要摘要的分色。
    """
    if not args:
        return ""
    key = display_name(name).lower()
    if key in _SHELL_TOOLS:
        return _shell_detail(args)

    path = _path(args)
    parts = [path] if path else []
    if key == "edit":
        parts.append(_edit_shape(args))
    elif key == "write":
        parts.append(_write_shape(args))
    elif key == "read":
        parts.append(_read_shape(args))
    elif key == "grep":
        parts.append(_grep_shape(args))
    elif key == "glob":
        parts.append(_glob_shape(args))
    parts = [part for part in parts if part]
    return " · ".join(parts) if parts else _compact(args)


def _shell_detail(args: dict[str, Any]) -> str:
    """shell 只给一行命令：多行命令压平后截断，不铺正文。"""
    command = _one_line(str(args.get("command") or ""), 160)
    return f"$ {command}" if command else ""


def _edit_shape(args: dict[str, Any]) -> str:
    old, new = args.get("old_string"), args.get("new_string")
    parts = []
    if isinstance(old, str) and isinstance(new, str):
        parts.append(f"−{len(old.splitlines())} +{len(new.splitlines())} 行")
    if args.get("replace_all"):
        parts.append("全部替换")
    return " · ".join(parts)


def _write_shape(args: dict[str, Any]) -> str:
    content = args.get("content")
    if not isinstance(content, str):
        return ""
    return f"{len(content.splitlines())} 行 / {len(content.encode('utf-8'))} 字节"


def _read_shape(args: dict[str, Any]) -> str:
    offset, limit, pages = args.get("offset"), args.get("limit"), args.get("pages")
    if pages:
        return f"第 {pages} 页"
    if offset is not None or limit is not None:
        start = offset if isinstance(offset, int) else 1
        if isinstance(limit, int):
            return f"第 {start}–{start + limit - 1} 行"
        return f"从第 {start} 行起"
    return ""


def _glob_shape(args: dict[str, Any]) -> str:
    """glob 只给模式：搜索根目录已经由 _path 摆在前面，别再吐一坨 JSON。"""
    return _one_line(str(args.get("pattern") or ""), 60)


def _grep_shape(args: dict[str, Any]) -> str:
    parts = []
    pattern = _one_line(str(args.get("pattern") or ""), 60)
    if pattern:
        parts.append(f"/{pattern}/")
    if args.get("glob"):
        parts.append(str(args["glob"]))
    if args.get("type"):
        parts.append(f"type={args['type']}")
    if args.get("case_insensitive") or args.get("i"):
        parts.append("-i")
    for flag in ("-A", "-B", "-C"):
        value = args.get(flag)
        if isinstance(value, int):
            parts.append(f"{flag}{value}")
    if args.get("multiline"):
        parts.append("-U")
    return " · ".join(parts)


def _path(args: dict[str, Any]) -> str:
    for key in ("file_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _compact(args: dict[str, Any]) -> str:
    try:
        raw = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(args)
    return _one_line(raw, 96)


# ---------- 结果标题 ----------


def result_label(raw: str) -> str:
    """折叠结果的标题：多大 + 开头是什么。

    收起时标题是唯一可见的一行，所以既要说明规模（多少行），也要给一段开头
    预览——只报行数看不出内容，只给预览又不知道有多长。
    """
    text = (raw or "").strip()
    lines = len(text.splitlines())
    size = f"输出 {lines} 行" if lines > 1 else "输出"
    head = _one_line(text, RESULT_PREVIEW)
    return f"{size} · {head}" if head else size


# ---------- 多行正文 ----------


def body(name: str, args: dict[str, Any]) -> Text | None:
    """可折叠的正文：编辑给彩色 diff，写文件给新增块。

    shell 命令没有正文——一行摘要就是它该有的全部展示，多行原样铺开只会淹没聊天区。
    """
    key = display_name(name).lower()
    if key == "edit":
        return _edit_body(args)
    if key == "write":
        return _write_body(args)
    return None


def body_label(name: str, args: dict[str, Any]) -> str:
    """折叠块的标题：收起时也能知道里面是什么、有多大。"""
    key = display_name(name).lower()
    if key == "edit":
        old, new = args.get("old_string"), args.get("new_string")
        if isinstance(old, str) and isinstance(new, str):
            return f"改动 {len(old.splitlines())} → {len(new.splitlines())} 行"
        return "改动预览"
    if key == "write":
        content = args.get("content")
        if isinstance(content, str):
            return f"写入 {len(content.splitlines())} 行"
    return "详情"


def _edit_body(args: dict[str, Any]) -> Text | None:
    old, new = args.get("old_string"), args.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or not (old or new):
        return None
    out = Text()
    head = f"@@ {_path(args) or '文件'}"
    if args.get("replace_all"):
        head += "  · 替换全部匹配"
    out.append(head, style=S_DIFF_META)
    _append_block(out, old.splitlines(), prefix="- ", style=S_DIFF_DEL)
    _append_block(out, new.splitlines(), prefix="+ ", style=S_DIFF_ADD)
    return out


def _write_body(args: dict[str, Any]) -> Text | None:
    content = args.get("content")
    if not isinstance(content, str) or not content:
        return None
    out = Text()
    lines = content.splitlines()
    out.append(
        f"@@ {_path(args) or '文件'}  ·  "
        f"{len(lines)} 行 / {len(content.encode('utf-8'))} 字节",
        style=S_DIFF_META,
    )
    _append_block(out, lines, prefix="+ ", style=S_DIFF_ADD)
    return out


def _append_block(out: Text, lines: list[str], prefix: str, style: str) -> None:
    """逐行追加：换行不带样式，每个 span 恰好对应一行内容，便于着色与断言。"""
    head, omitted, tail = _clip(lines, MAX_DIFF_LINES)
    for line in head:
        _append_line(out, prefix + line, style)
    if omitted:
        _append_line(out, f"  …… 省略 {omitted} 行 ……", S_FAINT)
    for line in tail:
        _append_line(out, prefix + line, style)


def _append_line(out: Text, text: str, style: str) -> None:
    if out.plain:
        out.append("\n")
    out.append(text, style=style)


def _clip(lines: list[str], limit: int) -> tuple[list[str], int, list[str]]:
    """超长时分作首尾两段，中间省掉的行数交给调用方标注。"""
    if len(lines) <= limit:
        return lines, 0, []
    head = (limit + 1) // 2
    return lines[:head], len(lines) - limit, lines[len(lines) - (limit - head):]


def _one_line(text: str, limit: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return value
