from __future__ import annotations

from typing import Any


def _dump(obj: Any) -> Any:
    """Pydantic 模型转 JSON 可序列化结构；其它对象原样返回。"""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj
