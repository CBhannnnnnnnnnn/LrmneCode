import difflib
import os
from pathlib import Path

from proxy_layer.register import diff_register
from proxy_layer.session import conversation_id_var
from backend.snapshot import SnapshotManager
from backend.workspace import workspace_manager

from pydantic import BaseModel

class DiffShowParams(BaseModel):
    round: int | None = None


class DiffUndoParams(BaseModel):
    round: int | None = None


class DiffListParams(BaseModel):
    pass


@diff_register("show")
async def diff_show(params: DiffShowParams):
    """对照快照与当前文件生成 unified diff；round 缺省为最新一轮。"""
    sm = _get_snapshot()
    rounds = sm.list_rounds()
    if not rounds:
        return {"rounds": [], "diffs": []}

    round_num = params.round or rounds[-1]["round"]
    files = sm.list_files(round_num)
    if not files:
        return {"round": round_num, "diffs": []}

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
    return {"round": round_num, "diffs": diffs}


@diff_register("undo")
async def diff_undo(params: DiffUndoParams):
    """按轮次把快照写回磁盘；round 缺省为最新一轮。"""
    sm = _get_snapshot()
    rounds = sm.list_rounds()
    if not rounds:
        return {"restored": [], "round": 0, "message": "没有可撤销的轮次"}

    round_num = params.round or rounds[-1]["round"]
    result = sm.undo_round(round_num)
    count = len(result["restored"])
    return {
        "restored": result["restored"],
        "round": round_num,
        "message": f"已恢复 {count} 个文件" if count else "该轮没有可恢复的文件",
    }


@diff_register("list")
async def diff_list(params: DiffListParams):
    """列出本会话有快照文件的轮次。"""
    sm = _get_snapshot()
    rounds = sm.list_rounds()
    result = []
    for entry in rounds:
        files = entry.get("files", [])
        if files:
            result.append({"round": entry["round"], "files": files})
    return {"rounds": result}


def _get_snapshot() -> SnapshotManager:
    cid = conversation_id_var.get() or "c-local"
    return SnapshotManager(workspace_manager.agent_home, cid)
