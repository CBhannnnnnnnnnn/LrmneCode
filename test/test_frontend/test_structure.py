"""结构守卫：把这次治理的成果钉住，防止回潮。

见 docs/specs/2026-09-22-前端结构治理.md 与 docs/adr/0003。
"""

from __future__ import annotations

from pathlib import Path

from frontend.app import LrmneAgentApp

FRONTEND = Path(__file__).parents[2] / "frontend"
MAX_LINES = 400


def _py_files() -> list[Path]:
    return sorted(p for p in FRONTEND.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_exceeds_line_cap():
    """前端不得再出现巨型模块。

    上限 400 行是这次治理的收口标准：它恰好只放过原有的中号文件。
    超了就按职责继续拆，不要抬高这个数字。
    """
    oversized = {
        str(p.relative_to(FRONTEND)): len(p.read_text(encoding="utf-8").splitlines())
        for p in _py_files()
    }
    broken = {name: n for name, n in oversized.items() if n > MAX_LINES}
    assert not broken, f"以下模块超过 {MAX_LINES} 行，按职责继续拆而不是抬高上限：{broken}"


def test_app_class_still_owns_its_on_handlers():
    """`@on` 处理器必须留在 App 类自身，不能搬进 mixin。

    Textual 的 `_MessagePumpMeta` 只从类**自身**的 `__dict__` 收集装饰过的处理器，
    mixin 里的一律静默不注册——搬走不会有报错，只有"点了没反应"。
    """
    collected = getattr(LrmneAgentApp, "_decorated_handlers", {})
    kinds = {key.__name__ for key in collected}
    assert {"Submitted", "TabPressed", "PaletteNavigate", "Changed", "Decision",
            "OptionSelected"} <= kinds, f"@on 入口被搬离 App 类，只剩 {kinds}"
    keys = {b.key for b in LrmneAgentApp.BINDINGS}
    assert {"escape", "f2", "f3", "ctrl+q"} <= keys, f"键位丢失：{keys}"
