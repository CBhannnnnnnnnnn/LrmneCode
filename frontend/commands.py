"""斜杠命令注册表：一个 /命令 对应一条协议 command（或纯本地动作）。

约定：
- 以 ``/`` 开头的输入不发给 chat.send，而是先解析成命令；
- 每个命令的处理器通过传入的 ``app``（CodeAgentApp）发协议命令或做本地展示；
- 需要参数的命令以空 usage 标记为"可补全但不自动执行"。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CodeAgentApp


# 普通文本输入走 chat.send；若后端日后改名只改这里
OP_CHAT_SEND = "chat.send"


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    usage: str = ""
    aliases: tuple[str, ...] = ()
    handler: Callable[["CodeAgentApp", list[str]], None] | None = None


COMMANDS: list[SlashCommand] = []


def command(
    name: str,
    description: str,
    usage: str = "",
    aliases: tuple[str, ...] = (),
):
    def decorator(fn: Callable[["CodeAgentApp", list[str]], None]):
        COMMANDS.append(SlashCommand(name, description, usage, aliases, fn))
        return fn

    return decorator


def parse(text: str) -> tuple[str, list[str]] | None:
    """/name args... -> (name, args)；非命令返回 None。"""
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


# ---------- 本地命令 ----------


@command("help", "显示所有可用命令", aliases=("?",))
def cmd_help(app: "CodeAgentApp", args: list[str]) -> None:
    app.show_command_help()


@command("clear", "清空显示", aliases=("reset",))
def cmd_clear(app: "CodeAgentApp", args: list[str]) -> None:
    app.clear_transcript()


@command("interrupt", "中断当前生成", aliases=("stop",))
def cmd_interrupt(app: "CodeAgentApp", args: list[str]) -> None:
    if not app._chat_inflight:
        app.show_notice("当前没有在途对话可中断", "info")
        return
    app.show_notice("发送中断请求 (chat.interrupt)…", "warn")
    app.send_command(
        "chat.interrupt", {}, kind="clear", label="chat.interrupt"
    )


@command("new", "开新对话", aliases=("new-chat",))
def cmd_new(app: "CodeAgentApp", args: list[str]) -> None:
    app.new_conversation()


@command("quit", "退出 CodeAgent", aliases=("exit",))
def cmd_quit(app: "CodeAgentApp", args: list[str]) -> None:
    app.exit()


# ---------- config.* 命令（一个 /命令 对应一条协议 command） ----------


@command("model", "查看或切换模型", usage="[模型名]")
def cmd_model(app: "CodeAgentApp", args: list[str]) -> None:
    if args:
        app.send_command(
            "config.set",
            {"key": "model", "value": args[0]},
            kind="config",
            label=f"config.set model={args[0]}",
        )
    else:
        app.send_command(
            "config.get", {"key": "model"}, kind="config", label="config.get model"
        )


@command("thinking", "查看或设置思考级别", usage="[off|low|medium|high]")
def cmd_thinking(app: "CodeAgentApp", args: list[str]) -> None:
    if args:
        app.send_command(
            "config.set",
            {"key": "thinking_level", "value": args[0].lower()},
            kind="config",
            label=f"config.set thinking_level={args[0]}",
        )
    else:
        app.send_command(
            "config.get",
            {"key": "thinking_level"},
            kind="config",
            label="config.get thinking_level",
        )


@command(
    "permission",
    "查看或设置权限模式",
    usage="[default|accept_edits|explore|bypass|dont_ask]",
)
def cmd_permission(app: "CodeAgentApp", args: list[str]) -> None:
    if args:
        app.send_command(
            "config.set",
            {"key": "mode", "value": args[0].lower()},
            kind="config",
            label=f"config.set mode={args[0]}",
        )
    else:
        app.send_command(
            "config.get", {"key": "mode"}, kind="config", label="config.get mode"
        )


@command("cwd", "查看或设置工作目录", usage="[路径]")
def cmd_cwd(app: "CodeAgentApp", args: list[str]) -> None:
    if args:
        app.send_command(
            "config.set",
            {"key": "root", "value": args[0]},
            kind="config",
            label=f"config.set root={args[0]}",
        )
    else:
        app.send_command(
            "config.get", {"key": "root"}, kind="config", label="config.get root"
        )


@command("config", "通用配置读写", usage="[key] [value]")
def cmd_config(app: "CodeAgentApp", args: list[str]) -> None:
    if not args:
        app.send_command("config.get", {}, kind="config", label="config.get")
    elif len(args) == 1:
        app.send_command(
            "config.get", {"key": args[0]}, kind="config", label=f"config.get {args[0]}"
        )
    else:
        raw = " ".join(args[1:])
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        app.send_command(
            "config.set",
            {"key": args[0], "value": value},
            kind="config",
            label=f"config.set {args[0]}",
        )


@command("status", "查看会话与配置状态", aliases=("info",))
def cmd_status(app: "CodeAgentApp", args: list[str]) -> None:
    app.show_status_card()
    app.send_command("config.get", {}, kind="config", label="config.get")


# ---------- session.* 命令 ----------


@command("sessions", "查看所有历史会话", aliases=("resumelist",))
def cmd_sessions(app: "CodeAgentApp", args: list[str]) -> None:
    app.send_command("session.list", {}, kind="session", label="session.list")


@command("resume", "恢复指定历史会话", usage="<cid>")
def cmd_resume(app: "CodeAgentApp", args: list[str]) -> None:
    if not args:
        app.show_notice("用法：/resume <cid>，先用 /sessions 查看会话列表", "warn")
        return
    app.send_command(
        "session.resume",
        {"source_cid": args[0]},
        kind="session-resume",
        label=f"session.resume {args[0]}",
    )


@command("delete", "删除指定历史会话", usage="<cid>", aliases=("rm",))
def cmd_delete(app: "CodeAgentApp", args: list[str]) -> None:
    if not args:
        app.show_notice("用法：/delete <cid>，先用 /sessions 查看会话列表", "warn")
        return
    app.send_command(
        "session.delete",
        {"cid": args[0]},
        kind="session-delete",
        label=f"session.delete {args[0]}",
    )


# ---------- 附件命令 ----------


@command("attach", "附加文件到下一条消息", usage="<文件路径>")
def cmd_attach(app: "CodeAgentApp", args: list[str]) -> None:
    if not args:
        app.show_notice("用法：/attach <文件路径>", "warn")
        return
    path = os.path.abspath(args[0])
    if not os.path.isfile(path):
        app.show_notice(f"文件不存在: {path}", "error")
        return
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        app.show_notice(f"读取失败: {e}", "error")
        return
    app.add_attachment(
        {
            "name": os.path.basename(path),
            "media_type": media_type,
            "data": base64.b64encode(raw).decode("ascii"),
        }
    )
    size = len(raw)
    app.show_notice(f"已附加 {os.path.basename(path)} ({media_type}, {size} bytes)", "success")


@command("attachments", "查看待发送附件", aliases=("attlist",))
def cmd_attachments(app: "CodeAgentApp", args: list[str]) -> None:
    atts = app.pending_attachments
    if not atts:
        app.show_notice("暂无待发送附件", "info")
        return
    lines = [f"{i+1}. {a['name']} ({a['media_type']})" for i, a in enumerate(atts)]
    app.show_notice("\n".join(lines), "info")


# ---------- skill 命令 ----------


@command("skills", "查看可用 skills")
def cmd_skills(app: "CodeAgentApp", args: list[str]) -> None:
    app.send_command("skill.list", {}, kind="skill-list", label="skill.list")


@command("use-skill", "使用指定 skill", usage="<名称或编号>")
def cmd_use_skill(app: "CodeAgentApp", args: list[str]) -> None:
    if not args:
        app.show_notice("用法：/use-skill <名称或编号>，先用 /skills 查看列表", "warn")
        return
    name = app.resolve_skill_name(args[0])
    if name is None:
        app.show_notice(f"未找到 skill: {args[0]}", "error")
        return
    app.send_command(
        "chat.send",
        {"text": f"请阅读并执行 skill: {name}"},
        kind="chat",
        label="chat.send",
    )


# ---------- mcp 命令 ----------


@command("mcp", "查看已连接的 MCP 服务器")
def cmd_mcp(app: "CodeAgentApp", args: list[str]) -> None:
    app.send_command("mcp.list", {}, kind="mcp-list", label="mcp.list")


# ---------- diff.* 命令 ----------


@command("diff", "查看本轮文件改动", usage="[round]")
def cmd_diff(app: "CodeAgentApp", args: list[str]) -> None:
    params = {"round": int(args[0])} if args else {}
    app.send_command("diff.show", params, kind="diff", label="diff.show")


@command("undo", "撤销指定轮次的文件修改", usage="[round]")
def cmd_undo(app: "CodeAgentApp", args: list[str]) -> None:
    params = {"round": int(args[0])} if args else {}
    app.send_command("diff.undo", params, kind="undo", label="diff.undo")
