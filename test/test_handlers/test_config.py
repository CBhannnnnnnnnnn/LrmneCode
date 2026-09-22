"""config handler 单元测试：只锁 root 会先清场，其它键走普通写入。"""

import asyncio

from handlers.config import (
    ConfigApplyModelParams,
    ConfigProvidersParams,
    ConfigSetParams,
    config_apply_model,
    config_providers,
    config_set,
)
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
        return {"root": root, "agent_home": root + "/.lrmnecode"}

    monkeypatch.setattr(config_handlers, "switch_workspace", switch_workspace)

    result = asyncio.run(config_set(ConfigSetParams(key="root", value="D:/work")))

    assert order == ["cancel", ("switch", "D:/work")]
    assert result["root"] == "D:/work"


def test_providers_handler_returns_adapter_listing(monkeypatch):
    monkeypatch.setattr(config_handlers, "list_providers", lambda: [{"title": "X"}])

    result = asyncio.run(config_providers(ConfigProvidersParams()))

    assert result == [{"title": "X"}]


def test_apply_model_passes_every_field_through(monkeypatch):
    seen = {}

    def apply_model(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(config_handlers, "apply_model", apply_model)

    result = asyncio.run(config_apply_model(ConfigApplyModelParams(model="gpt-4o")))

    assert result == {"ok": True}
    # provider_type 缺省为 None：adapter 据此判断「沿用现有凭证」
    assert seen == {
        "model": "gpt-4o",
        "provider_type": None,
        "credential": {},
        "thinking_level": None,
        "context_size": None,
    }
