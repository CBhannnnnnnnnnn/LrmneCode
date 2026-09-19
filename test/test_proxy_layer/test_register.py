"""register 单元测试：只锁注册约定和查找结果，不执行 handler。"""

import pytest
from pydantic import BaseModel

from proxy_layer.register import (
    approval_register,
    chat_register,
    config_register,
    diff_register,
    lookup,
    mcp_register,
    register_command,
    session_register,
    show_registry,
    skill_register,
)
from proxy_layer.schema import CommandMode


class EchoParams(BaseModel):
    text: str = ""


@pytest.fixture(autouse=True)
def isolated_registry():
    import proxy_layer.register as register

    saved = {namespace: dict(ops) for namespace, ops in register._registry.items()}
    register._registry.clear()
    try:
        yield
    finally:
        register._registry.clear()
        register._registry.update(saved)


def test_register_sync_handler_and_lookup():
    def echo(params: EchoParams):
        return params.text

    assert register_command("unit", "echo", CommandMode.PLAIN)(echo) is echo

    found = lookup("unit.echo")

    assert found["handler"] is echo
    assert found["params_model"] is EchoParams
    assert found["is_async"] is False
    assert found["mode"] == CommandMode.PLAIN
    assert show_registry() == ["unit.echo"]


def test_register_async_handler_marks_is_async():
    async def echo(params: EchoParams):
        return params.text

    register_command("unit", "echo", CommandMode.WAIT)(echo)

    found = lookup("unit.echo")
    assert found["is_async"] is True
    assert found["mode"] == CommandMode.WAIT


@pytest.mark.parametrize(
    ("helper", "namespace"),
    [
        (chat_register, "chat"),
        (config_register, "config"),
        (approval_register, "approval"),
        (session_register, "session"),
        (diff_register, "diff"),
        (skill_register, "skill"),
        (mcp_register, "mcp"),
    ],
)
def test_namespace_helper_defaults_to_plain(helper, namespace):
    def ping(params: EchoParams):
        return None

    helper("ping")(ping)

    found = lookup(f"{namespace}.ping")
    assert found["handler"] is ping
    assert found["mode"] == CommandMode.PLAIN


def test_lookup_supports_dotted_operation_name():
    def ping(params: EchoParams):
        return None

    register_command("unit", "op.extra", CommandMode.CONTROL)(ping)

    assert lookup("unit.op.extra")["mode"] == CommandMode.CONTROL


@pytest.mark.parametrize("name", ["missing", "unit.missing", "noseparator", "", ".", "unit."])
def test_lookup_unknown_or_malformed_name_returns_none(name):
    def ping(params: EchoParams):
        return None

    register_command("unit", "ping", CommandMode.PLAIN)(ping)

    assert lookup(name) is None


def test_later_registration_replaces_same_operation():
    def first(params: EchoParams):
        return 1

    def second(params: EchoParams):
        return 2

    register_command("unit", "echo", CommandMode.PLAIN)(first)
    register_command("unit", "echo", CommandMode.WAIT)(second)

    assert lookup("unit.echo")["handler"] is second
    assert show_registry() == ["unit.echo"]


def test_register_rejects_bad_mode():
    def ping(params: EchoParams):
        return None

    with pytest.raises(TypeError, match="CommandMode"):
        register_command("unit", "ping", "nope")(ping)

    assert lookup("unit.ping") is None


def test_register_rejects_function_without_exactly_one_parameter():
    def zero():
        return None

    def two(params: EchoParams, extra: int):
        return None

    with pytest.raises(TypeError, match="一个参数"):
        register_command("unit", "zero", CommandMode.PLAIN)(zero)
    with pytest.raises(TypeError, match="一个参数"):
        register_command("unit", "two", CommandMode.PLAIN)(two)

    assert lookup("unit.zero") is None
    assert lookup("unit.two") is None


def test_register_rejects_parameter_not_named_params():
    def ping(payload: EchoParams):
        return None

    with pytest.raises(TypeError, match="params"):
        register_command("unit", "ping", CommandMode.PLAIN)(ping)

    assert lookup("unit.ping") is None


def test_register_rejects_params_that_are_not_a_model():
    def ping(params: int):
        return None

    with pytest.raises(TypeError, match="BaseModel"):
        register_command("unit", "ping", CommandMode.PLAIN)(ping)

    assert lookup("unit.ping") is None
