"""成功回执的 result → 展示。

与 ``events.py`` 对称：那边处理单向事件，这边处理命令的同步返回值。
每个 operation 的呈现方式只在这里定义一次，``app`` 与 ``commands`` 都不再重复判断。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from rich.cells import cell_len as _cell_len
from rich.text import Text

from .overlay import Choice
from .registry import declare_operation, operation
from .theme import GLYPH_PANEL, S_DIM, S_ERR, S_FAINT, S_OK, S_TEXT
from .widgets import Card, NoticeLine

# ---------- 无同步返回值的 operation ----------

declare_operation("chat.send", label="发送消息", stream=True)
declare_operation("chat.interrupt", label="中断")
declare_operation("approval.respond", label="审批应答")


# ---------- 配置 ----------


def _flatten(result: Any) -> dict[str, Any]:
    """把 config.* 的分段视图压成一层：{section: {k: v}} → {k: v}。"""
    if not isinstance(result, dict):
        return {"result": result}
    flat: dict[str, Any] = {}
    for section, value in result.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[section] = value
    return flat


@operation("config.get", label="读取配置")
@operation("config.set", label="写入配置")
def _render_config(app, conv, result: Any) -> None:
    """config.get / config.set 的回执：只刷状态栏，不往转录里倒字段。

    启动时也会发一次 config.get，它的回执若渲染成卡片，用户一进来就先看到
    ``provider_type`` / ``agent_home`` 这类开发信息；只有 /status 明确要卡片时
    才出一张汇总视图。
    """
    flat = _flatten(result)
    # config.set 的回执是 {"key": …, "value": …}（见 handlers/config.py）：不翻成
    # {key: value} 的话，状态栏拿不到 thinking_level / mode，会一直显示默认值。
    if set(flat) == {"key", "value"}:
        flat = {str(flat["key"]): flat["value"]}
    app.apply_config(flat)
    # 缓存给选择卡片标「当前项」用（/thinking、/permission、/cwd）
    app.remember_config(flat)

    overlay = app.model_config_overlay
    if overlay is not None:
        # 浮窗打开时，配置视图归浮窗，不在聊天区再出一张卡片
        overlay.load_config(flat)
        return

    # 卡片里选出来的单个值：回一行确认即可。原始字段（provider_type /
    # max_retries / credential …）是开发信息，不进用户的转录。
    confirmation = app.consume_pending_set()
    if confirmation:
        conv.view.add(NoticeLine(confirmation, "success"))
        return

    if app.consume_status_card():
        conv.view.add(Card("配置", _config_body(flat)))


def _config_body(flat: dict[str, Any]) -> Text:
    """把配置视图整理成用户关心的一小块：模型 / 思考 / 权限 / 工作区。"""
    rows: list[tuple[str, str]] = []
    model = flat.get("model")
    if model:
        rows.append(("模型", str(model)))
    if "thinking_level" in flat:
        rows.append(("思考级别", str(flat["thinking_level"] or "off")))
    if "mode" in flat:
        rows.append(("权限模式", str(flat["mode"] or "default")))
    if flat.get("context_size"):
        rows.append(("上下文窗口", f"{flat['context_size']:,}"))
    if flat.get("root"):
        rows.append(("工作区", str(flat["root"])))

    # 凭证只报「配没配」，不回传密钥本身
    credential = flat.get("credential")
    if isinstance(credential, dict):
        api_key = credential.get("api_key")
        if not isinstance(api_key, dict):
            api_key = credential
        if api_key.get("configured"):
            rows.append(("API Key", f"已配置 (****{api_key.get('suffix', '')})"))
        else:
            rows.append(("API Key", "未配置"))

    body = Text()
    for index, (label, value) in enumerate(rows):
        if index:
            body.append("\n")
        # 中文标签占两列，按显示宽度补空格才对得齐
        body.append(f"{label}{' ' * max(0, 12 - _cell_len(label))}", style=S_FAINT)
        body.append(value, style=S_TEXT)
    return body


@operation("config.providers", label="提供方列表")
def _render_providers(app, conv, result: Any) -> None:
    """凭证提供方 Schema：投给模型配置浮窗生成表单。"""
    overlay = app.model_config_overlay
    if overlay is not None:
        overlay.load_providers(result if isinstance(result, list) else [])


@operation("config.apply_model", label="配置模型")
def _render_apply_model(app, conv, result: Any) -> None:
    """模型配置写入成功：刷新状态栏、关掉浮窗、提示生效。"""
    flat = _flatten(result)
    app.apply_config(flat)

    overlay = app.model_config_overlay
    if overlay is not None:
        overlay.dismiss(None)

    model = flat.get("model") or "?"
    provider = flat.get("provider_type")
    suffix = f"（{provider}）" if provider else ""
    conv.view.add(NoticeLine(f"模型已切换为 {model}{suffix}", "success"))
    if app.model_config_overlay is None:
        app.input_area.focus()


# ---------- 会话 ----------


@operation("session.list", label="历史会话")
def _render_session_list(app, conv, result: Any) -> None:
    sessions = result if isinstance(result, list) else []
    overlay = app.session_overlay
    if overlay is not None:
        # 浮窗打开时列表归浮窗，不在聊天区再出一张卡片
        overlay.load_sessions(sessions)
        return

    body = Text()
    if not sessions:
        body.append("暂无历史会话", style=S_FAINT)
    else:
        for item in sessions:
            cid = item.get("cid", "?")
            summary = item.get("summary") or ""
            modified = item.get("modified")
            body.append(f"  {cid}", style=S_TEXT)
            if summary:
                body.append(f"  {summary}", style=S_DIM)
            if modified:
                ts = datetime.fromtimestamp(modified).strftime("%m-%d %H:%M")
                body.append(f"  {ts}", style=S_FAINT)
            body.append("\n")
        body.append("用 /sessions 打开会话卡片切换或载入", style=S_FAINT)
    conv.view.add(Card("历史会话", body))


@operation("session.resume", label="恢复会话")
def _render_session_resume(app, conv, result: Any) -> None:
    if isinstance(result, dict) and result.get("resumed"):
        conv.view.add(
            NoticeLine(
                f"已载入存档 {result.get('source_cid', '?')}，发送消息即可继续",
                "success",
            ),
        )
    else:
        conv.view.add(NoticeLine(f"载入失败：{result}", "error"))


@operation("session.delete", label="删除会话")
def _render_session_delete(app, conv, result: Any) -> None:
    if isinstance(result, dict) and result.get("deleted"):
        cid = str(result.get("cid", "?"))
        overlay = app.session_overlay
        if overlay is not None:
            overlay.forget_disk(cid)
        conv.view.add(NoticeLine(f"已删除会话 {cid}", "success"))
    else:
        conv.view.add(NoticeLine(f"删除失败：{result}", "error"))


# ---------- 文件改动 ----------


def _round_note(files: list) -> str:
    """轮次行的说明：文件数与文件名，够长就截断。"""
    names = [os.path.basename(str(item)) for item in files if item]
    if not names:
        return ""
    shown = "、".join(names[:3])
    if len(names) > 3:
        shown += "…"
    return f"{len(names)} 个文件 · {shown}"


@operation("diff.list", label="改动轮次")
def _render_rounds(app, conv, result: Any) -> None:
    """改动轮次：有选择卡片就投给它，否则退回卡片列表。"""
    rounds = result.get("rounds", []) if isinstance(result, dict) else []
    rounds = [item for item in rounds if isinstance(item, dict)]

    picker = app.picker_overlay
    if picker is not None and picker.kind in ("diff", "undo"):
        picker.set_choices(
            [
                Choice(
                    f"第 {item.get('round', '?')} 轮",
                    _round_note(item.get("files") or []),
                    item.get("round"),
                )
                for item in rounds
            ],
        )
        return

    body = Text()
    if not rounds:
        body.append("当前会话还没有文件改动记录", style=S_FAINT)
    else:
        for item in rounds:
            body.append(f"  第 {item.get('round', '?')} 轮", style=S_TEXT)
            body.append(f"  {_round_note(item.get('files') or [])}\n", style=S_DIM)
        body.append("用 /diff 或 /undo 打开轮次卡片选择", style=S_FAINT)
    conv.view.add(Card("改动轮次", body))


@operation("diff.show", label="文件改动")
def _render_diff(app, conv, result: Any) -> None:
    if not isinstance(result, dict):
        conv.view.add(NoticeLine("无改动记录", "info"))
        return
    diffs = result.get("diffs", [])
    round_num = result.get("round", "?")
    if not diffs:
        conv.view.add(NoticeLine(f"第 {round_num} 轮无文件改动", "info"))
        return
    body = Text()
    body.append(f"第 {round_num} 轮，共 {len(diffs)} 个文件\n\n", style=S_FAINT)
    for item in diffs:
        body.append(
            f"{GLYPH_PANEL} {item.get('file', '?')}\n", style=S_TEXT
        )
        for line in str(item.get("diff", "")).splitlines():
            if line.startswith("+++") or line.startswith("---"):
                body.append(line + "\n", style=S_FAINT)
            elif line.startswith("+"):
                body.append(line + "\n", style=S_OK)
            elif line.startswith("-"):
                body.append(line + "\n", style=S_ERR)
            elif line.startswith("@@"):
                body.append(line + "\n", style=S_FAINT)
            else:
                body.append(line + "\n", style=S_DIM)
        body.append("\n")
    conv.view.add(Card("文件改动", body))


@operation("diff.undo", label="撤销改动")
def _render_undo(app, conv, result: Any) -> None:
    if isinstance(result, dict):
        message = result.get("message", "")
        conv.view.add(
            NoticeLine(message, "success" if result.get("restored") else "warn"),
        )
    else:
        conv.view.add(NoticeLine("撤销操作完成", "success"))


# ---------- 能力清单 ----------


@operation("skill.list", label="Skills")
def _render_skills(app, conv, result: Any) -> None:
    """skills：有选择卡片就投给它，@ 面板发起的只填候选，否则退回卡片列表。"""
    skills = [item for item in (result if isinstance(result, list) else []) if isinstance(item, dict)]
    app.remember_skills(skills)

    picker = app.picker_overlay
    if picker is not None and picker.kind == "skills":
        picker.set_choices(
            [
                Choice(
                    str(item.get("name") or "?"),
                    str(item.get("description") or ""),
                    str(item.get("name") or ""),
                )
                for item in skills
            ],
        )
        return
    if app.consume_mention_skills():
        return

    body = Text()
    if not skills:
        body.append("暂无可用 skills", style=S_FAINT)
    else:
        for index, item in enumerate(skills, 1):
            body.append(f"  {index}. ", style=S_FAINT)
            body.append(item.get("name", "?"), style=S_TEXT)
            description = item.get("description", "")
            if description:
                body.append(f"  {description}", style=S_DIM)
            body.append("\n")
        body.append("用 /skills 打开列表卡片选择", style=S_FAINT)
    conv.view.add(Card("Skills", body))


@operation("mcp.list", label="MCP 服务器")
def _render_mcp(app, conv, result: Any) -> None:
    servers = result if isinstance(result, list) else []
    body = Text()
    if not servers:
        body.append("暂无已连接的 MCP 服务器", style=S_FAINT)
    else:
        for index, item in enumerate(servers, 1):
            body.append(f"  {index}. ", style=S_FAINT)
            body.append(item.get("name", "?"), style=S_TEXT)
            body.append(f"  {item.get('type', '?')}", style=S_DIM)
            body.append(f"  {item.get('tool_count', 0)} tools", style=S_OK)
            body.append("\n")
    conv.view.add(Card("MCP 服务器", body))
