"""斜杠命令注册表：一个 /命令 对应一条协议 operation（或纯本地动作）。

约定：
- 以 ``/`` 开头的输入不发给 chat.send，而是先解析成命令；
- 每个命令的处理器通过传入的 ``app``（LrmneAgentApp）发协议命令或做本地动作；
- **命令一律不带参数**。要在几个取值里挑一个就弹 ``Picker``，要自由文本就弹
  ``Prompt``（见 ``overlay``）。``/config key value`` 这类"命令 + 配置"的写法
  不对用户暴露：取值域、当前值和是否合法都由候选卡片兜住。
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .overlay import THINKING_LEVELS, Choice

if TYPE_CHECKING:
    from .app import LrmneAgentApp


# 普通文本输入走 chat.send；若后端日后改名只改这里
OP_CHAT_SEND = "chat.send"


# 权限模式的取值来自 agentscope 的 PermissionMode，说明写给用户看
PERMISSION_MODES: tuple[tuple[str, str], ...] = (
    ("default", "每次改动都询问"),
    ("accept_edits", "工作区内编辑自动允许"),
    ("explore", "只读，改动一律拒绝"),
    ("bypass", "跳过安全检查，谨慎使用"),
    ("dont_ask", "不询问，待处理一律拒绝"),
)

THINKING_NOTES: dict[str, str] = {
    "off": "不思考，响应最快",
    "low": "浅思考",
    "medium": "默认",
    "high": "深思考，最慢",
}


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    aliases: tuple[str, ...] = ()
    handler: Callable[["LrmneAgentApp"], None] | None = None


COMMANDS: list[SlashCommand] = []


def command(name: str, description: str, aliases: tuple[str, ...] = ()):
    def decorator(fn: Callable[["LrmneAgentApp"], None]):
        COMMANDS.append(SlashCommand(name, description, aliases, fn))
        return fn

    return decorator


def parse(text: str) -> tuple[str, list[str]] | None:
    """/name args... -> (name, args)；非命令返回 None。

    命令本身不吃参数，``args`` 只用来提示用户"这个值请在卡片里选"。
    """
    if not text.startswith("/"):
        return None
    parts = text[1:].split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def find(name: str) -> SlashCommand | None:
    for cmd in COMMANDS:
        if name == cmd.name or name in cmd.aliases:
            return cmd
    return None


def match_prefix(prefix: str) -> list[SlashCommand]:
    prefix = prefix.lower()
    return [
        cmd
        for cmd in COMMANDS
        if cmd.name.startswith(prefix)
        or any(alias.startswith(prefix) for alias in cmd.aliases)
    ]


# ---------- 会话与进程 ----------


@command("help", "显示所有可用命令", aliases=("?",))
def cmd_help(app: "LrmneAgentApp") -> None:
    app.show_command_help()


@command("clear", "清空当前会话的显示", aliases=("reset",))
def cmd_clear(app: "LrmneAgentApp") -> None:
    app.clear_transcript()


@command("new", "开新对话", aliases=("new-chat",))
def cmd_new(app: "LrmneAgentApp") -> None:
    app.new_conversation()


@command("quit", "退出 LrmneAgent", aliases=("exit",))
def cmd_quit(app: "LrmneAgentApp") -> None:
    app.exit()


@command("status", "查看当前配置与运行状态", aliases=("info",))
def cmd_status(app: "LrmneAgentApp") -> None:
    # 配置卡片由 config.get 回执渲染：值来自后端，而不是本地缓存
    app.request_status_card()
    app.send("config.get", {})


# ---------- 配置：一律弹卡片，不做"命令 + 值" ----------


@command("model", "打开模型配置窗口")
def cmd_model(app: "LrmneAgentApp") -> None:
    app.open_model_config()


@command("thinking", "选择思考级别")
def cmd_thinking(app: "LrmneAgentApp") -> None:
    app.open_picker(
        "thinking",
        "思考级别",
        choices=[
            Choice(level, THINKING_NOTES.get(level, ""), level)
            for level in THINKING_LEVELS
        ],
        current=app.thinking_level,
        on_select=lambda level: app.set_config_value(
            "thinking_level", level, f"思考级别已设为 {level}"
        ),
    )


@command("permission", "选择权限模式", aliases=("perm",))
def cmd_permission(app: "LrmneAgentApp") -> None:
    app.open_picker(
        "permission",
        "权限模式",
        choices=[Choice(mode, note, mode) for mode, note in PERMISSION_MODES],
        current=app.permission_mode,
        on_select=lambda mode: app.set_config_value(
            "mode", mode, f"权限模式已设为 {mode}"
        ),
    )


@command("cwd", "切换工作目录", aliases=("cd",))
def cmd_cwd(app: "LrmneAgentApp") -> None:
    app.open_prompt(
        "cwd",
        "工作目录",
        caption="切换会重建工作区（磁盘存档保留），输入绝对路径",
        value=app.workspace_root,
        placeholder="如 D:/code/project",
        on_submit=lambda root: app.set_config_value(
            "root", root, f"工作目录已切到 {root}"
        ),
    )


# ---------- 会话存档 ----------


@command("sessions", "切换会话 / 载入磁盘存档", aliases=("resume",))
def cmd_sessions(app: "LrmneAgentApp") -> None:
    app.open_sessions()


# ---------- 附件 ----------


@command("attach", "附加文件到下一条消息")
def cmd_attach(app: "LrmneAgentApp") -> None:
    app.open_prompt(
        "attach",
        "附加文件",
        caption="输入文件路径，随下一条消息一起发送",
        placeholder="如 D:/code/note.md",
        on_submit=lambda path: attach_file(app, path),
    )


@command("attachments", "查看待发送附件", aliases=("attlist",))
def cmd_attachments(app: "LrmneAgentApp") -> None:
    attachments = app.pending_attachments
    if not attachments:
        app.notify_line("暂无待发送附件", "info")
        return
    lines = [
        f"{index}. {item['name']} ({item['media_type']})"
        for index, item in enumerate(attachments, 1)
    ]
    app.notify_line("\n".join(lines), "info")


def attach_file(app: "LrmneAgentApp", raw_path: str) -> None:
    """读盘并登记为待发送附件（/attach 卡片提交后的动作）。"""
    path = os.path.abspath(raw_path)
    if not os.path.isfile(path):
        app.notify_line(f"文件不存在: {path}", "error")
        return
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        app.notify_line(f"读取失败: {exc}", "error")
        return
    app.add_attachment(
        {
            "name": os.path.basename(path),
            "media_type": media_type,
            "data": base64.b64encode(raw).decode("ascii"),
        },
    )
    app.notify_line(
        f"已附加 {os.path.basename(path)}（{media_type}, {len(raw)} bytes）",
        "success",
    )


# ---------- 能力清单 ----------


@command("skills", "浏览并执行 skill")
def cmd_skills(app: "LrmneAgentApp") -> None:
    app.open_picker(
        "skills",
        "Skills",
        on_select=lambda name: app.send_chat(f"请阅读并执行 skill: {name}"),
    )
    app.send("skill.list", {})


@command("mcp", "查看已连接的 MCP 服务器")
def cmd_mcp(app: "LrmneAgentApp") -> None:
    app.send("mcp.list", {})


# ---------- 文件改动 ----------


@command("diff", "查看某一轮的文件改动")
def cmd_diff(app: "LrmneAgentApp") -> None:
    app.open_picker(
        "diff",
        "改动轮次",
        on_select=lambda round_num: app.send("diff.show", {"round": round_num}),
    )
    app.send("diff.list", {})


@command("undo", "撤销某一轮的文件修改")
def cmd_undo(app: "LrmneAgentApp") -> None:
    app.open_picker(
        "undo",
        "撤销改动",
        on_select=lambda round_num: app.send("diff.undo", {"round": round_num}),
    )
    app.send("diff.list", {})
