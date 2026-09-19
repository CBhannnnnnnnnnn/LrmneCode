"""permission 单元测试：只锁权限模式的读写和拒绝规则。"""

import pytest
from agentscope.permission import PermissionMode

from backend.user.permission import PermissionConfig


class _Context:
    def __init__(self):
        self.mode = None


class _Agent:
    def __init__(self):
        self.state = type("State", (), {"permission_context": _Context()})()


def test_default_mode_is_default():
    assert PermissionConfig().get() == {"mode": "default"}


def test_update_accepts_mode_text_and_enum():
    config = PermissionConfig()

    assert config.update(mode=" EXPLORE ")["mode"] == "explore"
    assert config.update(mode=PermissionMode.BYPASS)["mode"] == "bypass"


def test_update_mounts_mode_onto_agent():
    config = PermissionConfig()
    agent = _Agent()

    config.update(agent, mode="dont_ask")

    assert agent.state.permission_context.mode == PermissionMode.DONT_ASK


def test_update_rejects_unknown_key_and_bad_mode():
    config = PermissionConfig()

    with pytest.raises(KeyError, match="unknown permission"):
        config.update(root="D:/tmp")
    with pytest.raises(ValueError, match="mode must be one of"):
        config.update(mode="admin")
    with pytest.raises(TypeError, match="PermissionMode or str"):
        config.update(mode=1)

    assert config.get()["mode"] == "default"
