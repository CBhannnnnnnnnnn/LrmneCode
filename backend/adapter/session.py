from __future__ import annotations

import json
import os
import shutil
from typing import Any

from proxy_layer.session import conversation_id_var

from . import _state


def list_sessions() -> list[dict[str, Any]]:
    """列出所有已持久化的会话，返回元数据列表。"""
    sessions_dir = _state.workspace_manager.session_manager._dir
    result: list[dict[str, Any]] = []
    if not sessions_dir.is_dir():
        return result
    for child in sorted(sessions_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        state_path = child / "state.json"
        meta: dict[str, Any] = {"cid": child.name}
        if state_path.is_file():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                meta["summary"] = data.get("summary") or ""
                meta["session_id"] = data.get("session_id") or ""
            except (OSError, ValueError):
                meta["summary"] = ""
                meta["session_id"] = ""
            meta["modified"] = os.path.getmtime(state_path)
        result.append(meta)
    return result


def resume_session(source_cid: str) -> dict[str, Any]:
    """将会话状态从 source_cid 恢复到当前 conversation_id。"""
    cid = conversation_id_var.get() or "c-local"
    session = _state.workspace_manager.session_manager

    source_state = session.load_state(source_cid)
    if source_state is None:
        raise KeyError(f"会话 {source_cid} 不存在")

    session.save_state(cid, source_state)

    live = session.get(cid)
    if live is not None:
        live.state = source_state

    return {"source_cid": source_cid, "target_cid": cid, "resumed": True}


def delete_session(cid: str) -> dict[str, Any]:
    """删除指定会话的持久化文件。"""
    sessions_dir = _state.workspace_manager.session_manager._dir
    target = sessions_dir / cid
    if not target.is_dir():
        raise KeyError(f"会话 {cid} 不存在")
    shutil.rmtree(target)
    return {"cid": cid, "deleted": True}
