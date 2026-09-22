"""token 口径：prompt_total 是全站唯一的输入量口径。

由 ``frontend/widgets.py`` 拆分而来，只做搬运，未改任何实现。
"""

from __future__ import annotations


def _fmt_tokens(value: int) -> str:
    """token 数：一千以内原样，往上折成 k（状态栏与右侧栏同一套读法）。"""
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


# 这些提供方的 input_tokens 不含缓存那部分（Anthropic 系把读到与写入分开报）：
# 算占比要先把两块都加回分母，否则分母比真实提示词小，命中率虚高、占用也偏低
_CACHE_OUTSIDE_INPUT = frozenset({"anthropic_credential"})


def prompt_total(
    context_tokens: int,
    cache_tokens: int,
    provider: str,
    created_tokens: int = 0,
) -> int:
    """这次调用真正喂进去的提示词总量（含从缓存读到的与写进缓存的那两部分）。

    底部压力表与右侧栏的缓存命中都要这个量，口径只能有一份。
    """
    if provider in _CACHE_OUTSIDE_INPUT:
        return context_tokens + cache_tokens + created_tokens
    return context_tokens
