from proxy_layer.register import skill_register
from backend.adapter import list_skills

from pydantic import BaseModel


class SkillListParams(BaseModel):
    pass


@skill_register("list")
async def skill_list(params: SkillListParams):
    """列出当前工作区已加载的 skills。"""
    return await list_skills()
