from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any


MAX_ROUNDS = 20  # 每会话最多保留的快照轮次
SKIP_DIRS = {
    ".git", ".lrmneagent", "__pycache__", "node_modules",
    ".venv", "venv", ".env", ".idea", ".vscode",
}


class SnapshotManager:
    """按 conversation 轮次快照项目文本文件，供 diff/undo；跳过二进制与 SKIP_DIRS。"""

    def __init__(self, agent_home: str, cid: str, project_root: str = ""):
        self._dir = Path(agent_home) / "snapshots" / cid
        self._meta_path = self._dir / "meta.json"
        self._project_root = os.path.abspath(project_root) if project_root else ""
        self._round = 0
        self._meta: dict[str, Any] = {"rounds": []}
        self._ensure()

    def _ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if self._meta_path.is_file():
            try:
                self._meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._meta = {"rounds": []}
        if self._meta.get("rounds"):
            self._round = self._meta["rounds"][-1]["round"]

    def _save_meta(self) -> None:
        self._meta_path.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _round_dir(self, r: int | None = None) -> Path:
        return self._dir / f"r{r or self._round}"

    def _file_key(self, file_path: str) -> str:
        return hashlib.md5(file_path.encode()).hexdigest()[:12]

    def begin_round(self) -> int:
        """开启新一轮并快照当前工作区；超出 MAX_ROUNDS 时丢掉最旧轮。"""
        self._round += 1
        self._round_dir().mkdir(parents=True, exist_ok=True)
        self._meta["rounds"].append({"round": self._round, "files": []})
        self._save_meta()
        self.cleanup_old()
        self._snapshot_workspace()
        return self._round

    def _snapshot_workspace(self) -> None:
        if not self._project_root or not os.path.isdir(self._project_root):
            return
        r_dir = self._round_dir()
        for dirpath, dirnames, filenames in os.walk(self._project_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                abs_path = os.path.join(dirpath, fname)
                try:
                    content = Path(abs_path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                key = self._file_key(abs_path)
                snapshot_file = r_dir / f"{key}.json"
                snapshot_file.write_text(
                    json.dumps({"path": abs_path, "content": content}, ensure_ascii=False),
                    encoding="utf-8",
                )
                for entry in self._meta["rounds"]:
                    if entry["round"] == self._round:
                        entry["files"].append(abs_path)
                        break
        self._save_meta()

    def get_original(self, file_path: str) -> str | None:
        """当前轮快照中的原始内容；无记录返回 None。"""
        key = self._file_key(file_path)
        snapshot_file = self._round_dir() / f"{key}.json"
        if not snapshot_file.is_file():
            return None
        try:
            data = json.loads(snapshot_file.read_text(encoding="utf-8"))
            return data.get("content")
        except (OSError, ValueError):
            return None

    def list_rounds(self) -> list[dict[str, Any]]:
        return self._meta.get("rounds", [])

    def list_files(self, round_num: int) -> list[str]:
        for entry in self._meta.get("rounds", []):
            if entry["round"] == round_num:
                return entry.get("files", [])
        return []

    def undo_round(self, round_num: int) -> dict[str, Any]:
        """把指定轮快照写回磁盘；无该轮目录则 restored 为空。"""
        restored: list[str] = []
        r_dir = self._dir / f"r{round_num}"
        if not r_dir.is_dir():
            return {"restored": restored, "round": round_num}

        files = self.list_files(round_num)
        for file_path in files:
            key = self._file_key(file_path)
            snapshot_file = r_dir / f"{key}.json"
            if not snapshot_file.is_file():
                continue
            try:
                data = json.loads(snapshot_file.read_text(encoding="utf-8"))
                original = data.get("content", "")
                Path(file_path).parent.mkdir(parents=True, exist_ok=True)
                Path(file_path).write_text(original, encoding="utf-8")
                restored.append(file_path)
            except (OSError, ValueError):
                continue
        return {"restored": restored, "round": round_num}

    def cleanup_old(self) -> None:
        rounds = self._meta.get("rounds", [])
        if len(rounds) <= MAX_ROUNDS:
            return
        to_remove = rounds[:-MAX_ROUNDS]
        self._meta["rounds"] = rounds[-MAX_ROUNDS:]
        for entry in to_remove:
            r_dir = self._dir / f"r{entry['round']}"
            if r_dir.is_dir():
                import shutil
                shutil.rmtree(r_dir, ignore_errors=True)
        self._save_meta()
