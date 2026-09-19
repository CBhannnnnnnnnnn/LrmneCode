"""config handler 单元测试：只锁 root 会先清场，其它键走普通写入。"""

import asyncio

from handlers.config import ConfigSetParams, config_set
import handlers.config as config_handlers


def test_non_root_key_does_not_cancel(monkeypatch):
    seen = {}

    def set_config(key, value):
        seen["set"] = (key, value)

    monkeypatch.setattr(config_handlers, "set_config", set_config)
    monkeypatch.setattr(config_handlers, "cancel_all", lambda: seen.setdefault("cancel", True))

    result = asyncio.run(config_set(ConfigSetParams(key="mode", value="explore")))

    assert result == {"key": "mode", "value": "explore"}
    assert "cancel" not in seen


def test_root_key_cancels_before_switching_workspace(monkeypatch):
    order = []

    monkeypatch.setattr(config_handlers, "cancel_all", lambda: order.append("cancel"))

    async def switch_workspace(root):
        order.append(("switch", root))
        return {"root": root, "agent_home": root + "/.lrmneagent"}

    monkeypatch.setattr(config_handlers, "switch_workspace", switch_workspace)

    result = asyncio.run(config_set(ConfigSetParams(key="root", value="D:/work")))

    assert order == ["cancel", ("switch", "D:/work")]
    assert result["root"] == "D:/work"
