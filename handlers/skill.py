from proxy_layer.register import skill_register
from backend.adapter import list_skills

from pydantic import BaseModel
from typing import Any


class SkillListParams(BaseModel):
    pass


@skill_register("list")
async def skill_list(params: SkillListParams) -> list[dict[str, Any]]:
    """列出当前工作区已加载的 skills。"""
    return await list_skills()
