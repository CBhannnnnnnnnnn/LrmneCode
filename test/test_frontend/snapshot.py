"""渲染快照基线：把"外观没变"变成可逐字比对的东西。

见 `docs/adr/0002`。基线在重构开始前生成一次，之后每个切片都拿它当真值——
本项目要防的那类回归（CSS 作用域被改写、挂载顺序错乱、字形退化、私有属性撞名顶掉钩子）
都不会让行为断言变红，只有渲染结果能抓到。

用 `LRMNE_SNAPSHOT_UPDATE=1` 重新生成全部基线。
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Any

from textual.widget import Widget

from frontend.theme import GLYPH_SPINNER

SIZE = (120, 40)
UPDATE_ENV = "LRMNE_SNAPSHOT_UPDATE"
SNAP_DIR = Path(__file__).parent / "snapshots"

# 每次渲染都随机、或每次启动都重生的东西，与代码结构无关，比对前先剔掉。
_SUBSTITUTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"terminal-\d+"), "terminal-N"),  # SVG 里随机的样式类名前缀
    (re.compile(r"c-[0-9a-f]{8}"), "c-CID"),  # 每次启动新建的会话号
    (re.compile(r"\b[0-9a-f]{8}\b"), "CID"),  # 会话号被截断后裸显示的样子
    (re.compile(r"\d+(?:\.\d+)?ms"), "Nms"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"), "DATETIME"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "DATE"),
    (re.compile(r"\d+\s*(?:秒|分钟|小时|天|周)前"), "N前"),
    (re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"), "TIME"),
]

# 转圈动画的字符按帧轮换，与结构无关；逐个换成同一个占位符。
_SPINNER_CHARS = "".join(str(ch) for ch in GLYPH_SPINNER)


def normalize(svg: str) -> str:
    """把易变片段折成占位符，只留下结构性的渲染结果。

    只折三类：SVG 随机样式类名、每次启动重生的会话号、耗时与转圈动画。
    坐标、宽度、样式类索引、可见字符**全部保留**——耗时的字符数已由
    `test_snapshots` 的定宽夹具钉住，不需要再靠折几何来换取稳定。
    """
    for pattern, placeholder in _SUBSTITUTIONS:
        svg = pattern.sub(placeholder, svg)
    for ch in set(_SPINNER_CHARS):
        svg = svg.replace(ch, "·")
    return svg


def stop_timers(app: Any) -> int:
    """停掉所有部件的定时器。

    Textual 8.2.8 没有 `App.freeze()`，而应用自己挂着周期任务（贴底兜底 tick、
    spinner、思考块耗时），实测会让同一屏在两帧之间反复切换——不断言停表，
    基线就永远复现不出来。
    """
    stopped = 0
    nodes = [app, app.screen, *app.screen.walk_children(Widget)]
    for node in nodes:
        for timer in list(getattr(node, "_timers", ()) or ()):
            timer.stop()
            stopped += 1
    return stopped


def visible_texts(svg: str) -> list[str]:
    return re.findall(r">([^<>]*)</text>", svg)


def assert_matches(name: str, svg: str) -> None:
    """与基线逐字比对；不一致时给出可读的可见文本差异。"""
    if os.environ.get(UPDATE_ENV):
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        (SNAP_DIR / f"{name}.svg").write_text(svg, encoding="utf-8")
        return

    path = SNAP_DIR / f"{name}.svg"
    if not path.exists():
        raise AssertionError(
            f"缺少快照基线 {path.name}；用 {UPDATE_ENV}=1 跑一遍生成"
        )

    expected = path.read_text(encoding="utf-8")
    if svg == expected:
        return

    (SNAP_DIR / f"{name}.actual.svg").write_text(svg, encoding="utf-8")
    diff = "\n".join(
        difflib.unified_diff(
            visible_texts(expected),
            visible_texts(svg),
            "基线",
            "当前",
            lineterm="",
            n=1,
        )
    )
    raise AssertionError(
        f"渲染与基线不一致：{name}\n"
        f"（完整差异见 {path.name} vs {name}.actual.svg）\n"
        f"可见文本差异：\n{diff or '（文本相同，差异在样式/坐标）'}"
    )


async def snap(app: Any, pilot: Any, name: str) -> None:
    """停表 → 让最后一轮布局落地 → 与基线比对。"""
    await pilot.pause()
    stop_timers(app)
    for _ in range(3):
        await pilot.pause()
    assert_matches(name, normalize(app.export_screenshot()))
