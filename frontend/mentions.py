"""``@`` 提及：输入框里打 ``@`` 就能按名字指一个文件、一个目录或一个 skill。

选中后往正文里插 ``@标签``，这一行字本身就是提示词的一部分——模型据此知道要看哪些文件，
文件内容由模型自己用读文件的工具去取（不在这里读成附件，省 token 也省一次陈旧副本）。

- 文件 ``@backend/auth/session.py`` / 目录 ``@backend/adapter/``：都是相对工作目录的路径。
- ``@`` 后面什么都没打时列**当前这一层**（目录在前），Enter 落在目录上就往下钻一层；
  打了字则按关键字在**当前作用域之下递归搜**——两种模式都由正文本身决定，没有额外状态。
- skill ``@review``：skill 不是工具，是系统提示里列出的说明文档，由模型自己用
  skill_viewer 读（见 agentscope 的 skill 说明），所以这里只需把名字点出来。

本模块只有纯函数与本地扫描，不持有 widget，也不碰协议。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .datablocks import human_size

DIR_KIND = "dir"
FILE_KIND = "file"
SKILL_KIND = "skill"

# 不列进候选的目录：要么是产物，要么是依赖，扫了只会把真正的源码挤出列表
IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        "vendor",
        "venv",
    }
)

# 候选与附件各设一道上限：列表是给人挑的，附件是要塞进模型的
MAX_ENTRIES = 200
MAX_FILES = 400

# 正文里的 @路径 会被标点收尾，画高亮时要把尾标点排在外面。中西标点分开处理：
# 中文标点几乎不会出现在路径里，遇到就切断（"@a.py，然后…"）；西文标点可能本身就是
# 文件名的一部分，只在末尾削掉。
_CJK_STOP = "，。；：！？、（）【】《》“”‘’"
_TRAILING = ",.;:!?)\"'"


@dataclass(frozen=True)
class Mention:
    """候选行：``label`` 是插进正文的东西（目录不带尾斜杠，插入时由 kind 补上），
    ``note`` 是右手的说明。"""

    kind: str
    label: str
    note: str = ""

    @property
    def token(self) -> str:
        """插进正文的形态：目录带尾斜杠，其余原样。"""
        return f"{self.label}/" if self.kind == DIR_KIND else self.label


def scope_of(fragment: str) -> tuple[str, str]:
    """``@`` 后面的片段 → ``(作用域, 查询词)``。

    ``backend/adapter/`` → 作用域就是它本身、查询词空（在列这一层）；
    ``backend/work`` → 作用域 ``backend/``、查询词 ``work``（在该目录下搜）。
    """
    cut = fragment.rfind("/")
    if cut < 0:
        return "", fragment
    return fragment[: cut + 1], fragment[cut + 1 :]


def browse(root: str, scope: str = "", limit: int = MAX_ENTRIES) -> list[Mention]:
    """列 ``root/scope`` 这一层的目录与文件（相对路径，目录在前）。

    含空格的路径不列：``@my notes.md`` 会被读成两个词，回读时找不到这个文件；
    隐藏文件与产物目录同理不列，噪声大过用处，真要就直接手打路径。
    """
    if not root:
        return []
    base = os.path.join(root, *scope.split("/")) if scope else root
    if not os.path.isdir(base):
        return []
    try:
        names = os.listdir(base)
    except OSError:
        return []

    dirs: list[Mention] = []
    files: list[Mention] = []
    for name in sorted(names):
        if name.startswith("."):
            continue
        path = os.path.join(base, name)
        label = f"{scope}{name}"
        if any(char.isspace() for char in label):
            continue
        if os.path.isdir(path):
            if not _skipped_dir(name):
                dirs.append(Mention(DIR_KIND, label, "目录"))
        elif os.path.isfile(path):
            files.append(Mention(FILE_KIND, label, _size_note(path)))
    return [*dirs, *files][:limit]


def search(root: str, scope: str, query: str, limit: int = 8) -> list[Mention]:
    """在 ``root/scope`` 之下递归找匹配 ``query`` 的文件与目录。"""
    return match(walk(root, scope), query, limit)


def walk(root: str, scope: str = "", limit: int = MAX_FILES) -> list[Mention]:
    """``root/scope`` 之下所有目录与文件（相对路径），逐层向下、每层目录在前。

    关键字搜索的候选池。这是个同步 IO 的整树扫描，调用方负责缓存。
    """
    items: list[Mention] = []
    if not root:
        return items
    pending = [scope]
    while pending and len(items) < limit:
        for entry in browse(root, pending.pop(0), limit=MAX_ENTRIES):
            items.append(entry)
            if entry.kind == DIR_KIND:
                pending.append(f"{entry.label}/")
    return items[:limit]


def from_skills(skills: list) -> list[Mention]:
    """``skill.list`` 回执 → 候选（描述跟在名字后面，标出这是 skill）。"""
    items: list[Mention] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "")
        if not name:
            continue
        description = str(skill.get("description") or "")
        items.append(
            Mention(SKILL_KIND, name, f"skill · {description}" if description else "skill"),
        )
    return items


def match(items: list[Mention], query: str, limit: int = 8) -> list[Mention]:
    """按查询词挑候选：文件名前缀 > 路径前缀 > 文件名包含 > 路径包含，短的优先。"""
    needle = (query or "").lower().lstrip("@")
    if not needle:
        return list(items[:limit])
    ranked: list[tuple[int, int, str, Mention]] = []
    for item in items:
        label = item.label.lower()
        base = label.replace("\\", "/").rsplit("/", 1)[-1]
        if base.startswith(needle):
            rank = 0
        elif label.startswith(needle):
            rank = 1
        elif needle in base:
            rank = 2
        elif needle in label:
            rank = 3
        else:
            continue
        ranked.append((rank, len(label), label, item))
    ranked.sort(key=lambda row: row[:3])
    return [row[3] for row in ranked[:limit]]


def token_at(text: str, cursor: int | None = None) -> tuple[int, str] | None:
    """光标前最后一段 ``@`` 词 → ``(起点, 片段)``；没有正在输入的提及则 None。

    ``@`` 必须在词首（前面是空白或开头），所以 ``a@b.com`` 这类不会误判成提及。
    空白与中文标点都表示"这个词写完了"：前者是句子里的间隔，后者说明后面接的是中文
    解释（``@note.md，然后``）——两种都让面板收起，Enter 回到发送本身。词尾的西文标点
    削掉（``@note.md,`` 认的还是 ``note.md``，那是手滑多打的一个逗号）。
    """
    end = len(text) if cursor is None else max(0, min(cursor, len(text)))
    head = text[:end]
    start = head.rfind("@")
    if start < 0:
        return None
    if start and not head[start - 1].isspace():
        return None
    fragment = head[start + 1 :]
    if any(char.isspace() or char in _CJK_STOP for char in fragment):
        return None
    return start, fragment.rstrip(_TRAILING)


def triggered(text: str, cursor: int | None = None) -> tuple[str, str] | None:
    """当前触发词：``("command", 查询词)`` / ``("mention", 片段)`` / None。

    ``/`` 只在整段正文的开头才算命令（命令不吃参数）；``@`` 可以在句子中间。
    """
    if text.startswith("/") and " " not in text:
        return "command", text[1:]
    pair = token_at(text, cursor)
    return ("mention", pair[1]) if pair is not None else None


def reference_spans(line: str) -> list[tuple[int, int]]:
    """一行正文里所有 ``@词`` 的 ``[起, 止)`` 字符区间（含 ``@``，不含尾标点）。

    与 ``resolve`` 那套「什么算一个词」保持一致：``@`` 要在词首，词止于空白或中文
    标点，末尾的西文标点削掉——否则高亮会连逗号一起染上。
    """
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(line):
        if line[index] == "@" and (index == 0 or line[index - 1].isspace()):
            stop = index + 1
            while (
                stop < len(line)
                and not line[stop].isspace()
                and line[stop] not in _CJK_STOP
            ):
                stop += 1
            while stop > index + 1 and line[stop - 1] in _TRAILING:
                stop -= 1
            if stop > index + 1:
                spans.append((index, stop))
                index = stop
                continue
        index += 1
    return spans


def cursor_offset(text: str, location: tuple[int, int]) -> int:
    """TextArea 的 ``(行, 列)`` → 正文字符下标（替换要在整段正文上定位）。"""
    row, col = location
    lines = text.split("\n")
    row = max(0, min(row, len(lines) - 1))
    return sum(len(line) + 1 for line in lines[:row]) + min(col, len(lines[row]))


def location_of(text: str, offset: int) -> tuple[int, int]:
    """正文下标 → TextArea 的 ``(行, 列)``（``cursor_offset`` 的反向）。"""
    offset = max(0, min(offset, len(text)))
    row = text.count("\n", 0, offset)
    start = text.rfind("\n", 0, offset) + 1
    return row, offset - start


def accept(text: str, cursor: int, item: Mention) -> tuple[str, int] | None:
    """把光标前那个正在输入的 ``@词`` 换成这个候选；没有正在输入的提及则 None。

    只动 ``@词`` 本身：光标后面紧跟的文字（中文标点、接着写的解释）原样留下。
    目录补成 ``@backend/adapter/``（末尾不加空格、面板不收起，接着列下一层）；
    文件与 skill 补成 ``@标签 ``，光标落到空格后继续写正文。
    """
    pair = token_at(text, cursor)
    if pair is None:
        return None
    start, fragment = pair
    end = start + 1 + len(fragment)
    token = f"@{item.token}"
    if item.kind != DIR_KIND:
        token += " "
    return text[:start] + token + text[end:], start + len(token)


def _relative(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _skipped_dir(name: str) -> bool:
    # .egg-info 这类是打包产物，跟 __pycache__ 一样会把源码挤出列表
    return name.startswith(".") or name in IGNORED_DIRS or name.endswith(".egg-info")


def _size_note(path: str) -> str:
    try:
        return human_size(os.path.getsize(path))
    except OSError:
        return ""
