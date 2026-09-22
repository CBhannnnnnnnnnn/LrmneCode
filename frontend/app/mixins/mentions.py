"""@ 提及候选：工作区文件与 skill 的列举和记忆。

由 ``frontend/app.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations

import os
from ... import (mentions)
from ...mentions import (Mention)


class MentionsMixin:
    """@ 提及候选：工作区文件与 skill 的列举和记忆。"""

    def mention_candidates(self, fragment: str) -> list[Mention]:
        """提及候选：skill 在前（数量少、语义强），其后是路径。

        ``@`` 空着就列**当前那一层**（目录在前、能接着往下钻）；打了字则在当前目录
        之下**递归搜关键字**——不然 ``@work`` 找不到深处的 ``backend/workspace.py``。
        当前在哪一层由正文本身决定：``@backend/adapter/`` 的作用域就是它自己。
        """
        self._load_skills_once()
        root = self.workspace_root or os.getcwd()
        scope, query = mentions.scope_of(fragment)
        if not query:
            return [*self.skills, *mentions.browse(root, scope)]
        pool = self.workspace_files()
        if scope:
            # 已经钻进某一层了：只在它下面搜（缓存里就是带前缀的相对路径，过滤即可）
            pool = [item for item in pool if item.label.startswith(scope)]
        return mentions.match([*self.skills, *pool], query)

    def workspace_files(self) -> list[Mention]:
        """整个工作区的文件与目录：按工作目录缓存一次，扫描是同步 IO，别每次按键都走。"""
        # 启动时 config.get 还没回来，root 是空的；此时按进程 cwd 列（前端就是从
        # 项目根启动的，见 README 的启动方式），等配置到了再按真正的 root 重扫
        root = self.workspace_root or os.getcwd()
        if self._files_cache is None or self._files_cache[0] != root:
            self._files_cache = (root, mentions.walk(root))
        return self._files_cache[1]

    def _load_skills_once(self) -> None:
        if self.skills or self._skills_pending:
            return
        self._skills_pending = True
        self.send("skill.list", {})

    def remember_skills(self, skills: list) -> None:
        """``skill.list`` 回执落进提及候选；@ 面板正开着就顺手重排一遍。"""
        self.skills = mentions.from_skills(skills)
        if self.palette.display and self.palette.mode == "mention":
            self.sync_palette()

    def consume_mention_skills(self) -> bool:
        """取走「这次 skill.list 是 @ 面板发起的」标记（读一次即清）。

        这种请求只是为了填候选，不能在转录里出卡片。
        """
        pending, self._skills_pending = self._skills_pending, False
        return pending
