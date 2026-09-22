from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any

from proxy_layer.session import conversation_id_var

from backend.snapshot import SnapshotManager

from . import _state


def _get_snapshot() -> SnapshotManager:
    """按当前会话取快照管理器；工作区取自 _state，避免切换后指向旧单例。"""
    cid = conversation_id_var.get() or "c-local"
    return SnapshotManager(
        _state.workspace_manager.code_home,
        cid,
        _state.workspace_manager.project_root,
    )


def diff_show(round_num: int | None = None) -> dict[str, Any]:
    """对照快照与当前文件生成 unified diff；round 缺省为最新一轮。"""
    sm = _get_snapshot()
    rounds = sm.list_rounds()
    if not rounds:
        return {"rounds": [], "diffs": []}

    rnd = round_num or rounds[-1]["round"]
    files = sm.list_files(rnd)
    if not files:
        return {"round": rnd, "diffs": []}

    diffs = []
    for file_path in files:
        original = sm.get_original(file_path)
        if original is None:
            continue
        abs_path = os.path.abspath(file_path)
        try:
            current = Path(abs_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = ""
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        ))
        if diff_lines:
            diffs.append({"file": file_path, "diff": "".join(diff_lines)})
    return {"round": rnd, "diffs": diffs}


def diff_undo(round_num: int | None = None) -> dict[str, Any]:
    """按轮次把快照写回磁盘；round 缺省为最新一轮。"""
    sm = _get_snapshot()
    rounds = sm.list_rounds()
    if not rounds:
        return {"restored": [], "round": 0, "message": "没有可撤销的轮次"}

    rnd = round_num or rounds[-1]["round"]
    result = sm.undo_round(rnd)
    count = len(result["restored"])
    return {
        "restored": result["restored"],
        "round": rnd,
        "message": f"已恢复 {count} 个文件" if count else "该轮没有可恢复的文件",
    }


def diff_list() -> dict[str, Any]:
    """列出本会话有快照文件的轮次。"""
    sm = _get_snapshot()
    rounds = sm.list_rounds()
    result = []
    for entry in rounds:
        files = entry.get("files", [])
        if files:
            result.append({"round": entry["round"], "files": files})
    return {"rounds": result}
