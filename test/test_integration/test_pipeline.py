"""集成测试：真实 handler 穿过 register、scheduler、executor。不调用模型。"""

import asyncio
import json

import pytest
from pydantic import BaseModel

import handlers  # noqa: F401  确保真实操作已注册
from proxy_layer.register import register_command
from proxy_layer.schema import CommandMode, ErrorCode, make_command
from proxy_layer.scheduler import submit
import proxy_layer.scheduler as scheduler


class _Params(BaseModel):
    pass


@pytest.fixture(autouse=True)
def clean_scheduler():
    import proxy_layer.register as register

    scheduler._queues.clear()
    register._registry.pop("unit", None)
    yield
    for queue in list(scheduler._queues.values()):
        for task in queue:
            if not task.done():
                task.cancel()
    scheduler._queues.clear()
    register._registry.pop("unit", None)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """把模型配置的落盘位置改到 tmp，避免污染用户的 ~/.lrmnecode。"""
    from backend.user import model as model_module

    monkeypatch.setattr(model_module, "_GLOBAL_HOME", tmp_path)
    monkeypatch.setattr(model_module, "_SETTINGS_PATH", tmp_path / "settings.json")
    snapshot = dict(model_module.model_config._settings)
    yield tmp_path / "settings.json"
    model_module.model_config._settings.clear()
    model_module.model_config._settings.update(snapshot)


def _drive(operation, parameters=None, conversation_id="c-int", request_id="req-1"):
    command = make_command(request_id, conversation_id, operation, parameters or {})

    async def collect():
        return [chunk async for chunk in submit(command)]

    return asyncio.run(collect())


def test_config_get_returns_permission_mode():
    chunks = _drive("config.get", {"key": "mode"})

    assert len(chunks) == 1
    assert chunks[0].accepted is True
    assert "mode" in chunks[0].result


def test_config_set_rejects_missing_and_unknown_keys():
    missing = _drive("config.set", {})
    unknown = _drive("config.set", {"key": "nope", "value": 1})

    assert missing[0].error_code == ErrorCode.INVALID_PARAMETERS
    assert unknown[0].error_code == ErrorCode.INTERNAL_ERROR
    assert "unknown config key" in unknown[0].error_message


def test_config_providers_lists_credential_schemas():
    chunks = _drive("config.providers", {})

    assert chunks[0].accepted is True
    providers = chunks[0].result
    types = [p["properties"]["type"]["const"] for p in providers]

    assert "openai_credential" in types
    # 前端据此生成表单：字段、必填、密码格式都要在
    openai = next(p for p in providers if p["properties"]["type"]["const"] == "openai_credential")
    assert openai["required"] == ["api_key"]
    assert openai["properties"]["api_key"]["format"] == "password"


def test_apply_model_writes_provider_and_model_in_one_step(settings_file):
    chunks = _drive(
        "config.apply_model",
        {
            "model": "gpt-4o-mini",
            "provider_type": "openai_credential",
            "credential": {"api_key": "sk-test"},
        },
    )

    result = chunks[0].result
    assert chunks[0].accepted is True
    assert result["model"] == "gpt-4o-mini"
    assert result["provider_type"] == "openai_credential"
    assert result["configured"] is True
    # 密钥只以掩码形式回传
    assert result["credential"]["api_key"]["configured"] is True
    assert result["credential"]["api_key"]["suffix"] == "test"

    # 落盘一次到位：分步写入会在 provider/model 缺一时丢掉整段 model 配置
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["model"]["model"] == "gpt-4o-mini"
    assert saved["model"]["provider_type"] == "openai_credential"
    assert saved["model"]["credential"]["api_key"] == "sk-test"


def test_apply_model_without_provider_keeps_existing_credential(settings_file):
    _drive(
        "config.apply_model",
        {
            "model": "gpt-4o",
            "provider_type": "openai_credential",
            "credential": {"api_key": "sk-keep"},
        },
    )

    chunks = _drive("config.apply_model", {"model": "gpt-4o-mini", "thinking_level": "high"})

    result = chunks[0].result
    assert chunks[0].accepted is True
    assert result["model"] == "gpt-4o-mini"
    assert result["thinking_level"] == "high"
    # 提供方与凭证沿用，未被清空
    assert result["provider_type"] == "openai_credential"
    assert result["credential"]["api_key"]["configured"] is True
    assert result["credential"]["api_key"]["suffix"] == "keep"


def test_apply_model_validates_parameters(settings_file):
    missing_model = _drive("config.apply_model", {})
    assert missing_model[0].error_code == ErrorCode.INVALID_PARAMETERS

    bad_provider = _drive("config.apply_model", {"model": "gpt-4o", "provider_type": "nope"})
    assert bad_provider[0].accepted is False

    bad_level = _drive(
        "config.apply_model", {"model": "gpt-4o", "thinking_level": "extreme"}
    )
    assert bad_level[0].accepted is False


def test_unknown_operation_is_rejected_by_the_pipeline():
    chunks = _drive("unit.missing")

    assert chunks[0].error_code == ErrorCode.UNKNOWN_OPERATION


def test_chat_interrupt_cancels_a_waiting_command():
    started = asyncio.Event()

    async def blocked(params: _Params):
        started.set()
        await asyncio.Event().wait()

    register_command("unit", "blocked", CommandMode.WAIT)(blocked)

    async def collect(operation, request_id):
        command = make_command(request_id, "c-int", operation)
        return [chunk async for chunk in submit(command)]

    async def body():
        waiting = asyncio.create_task(collect("unit.blocked", "req-wait"))
        await started.wait()
        stopped = await collect("chat.interrupt", "req-stop")
        with pytest.raises(asyncio.CancelledError):
            await waiting
        return stopped

    stopped = asyncio.run(body())

    assert stopped[0].accepted is True
    assert stopped[0].result["interrupted"] is True
    assert stopped[0].result["conversation_id"] == "c-int"
