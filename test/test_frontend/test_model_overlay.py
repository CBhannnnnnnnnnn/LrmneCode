"""模型配置浮窗：Schema 驱动的表单与「是否写回凭证」的提交规则。"""

from __future__ import annotations

import pytest
from textual.widgets import Input, OptionList, Select

from conftest import emit, plain, show_config, show_providers

OPENAI = {
    "title": "OpenAI API",
    "type": "object",
    "required": ["api_key"],
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "const": "openai_credential"},
        "name": {"type": "string", "default": ""},
        "api_key": {"type": "string", "format": "password", "title": "Api Key"},
        "base_url": {
            "type": "string",
            "title": "Base Url",
            "default": "https://api.openai.com/v1",
        },
    },
}

OLLAMA = {
    "title": "Ollama API",
    "type": "object",
    "required": [],
    "properties": {
        "type": {"type": "string", "const": "ollama_credential"},
        "name": {"type": "string", "default": ""},
        "host": {"type": "string", "title": "Host", "default": "http://localhost:11434"},
    },
}

PROVIDERS = [OPENAI, OLLAMA]

CONFIGURED = {
    "model": {
        "configured": True,
        "provider_type": "openai_credential",
        "credential": {"api_key": {"configured": True, "suffix": "abcd"}},
        "model": "gpt-4o",
        "thinking_level": "medium",
        "context_size": 32768,
    },
    "permission": {"mode": "default"},
    "workspace": {"root": "/tmp/project"},
}


async def _open(app, pilot):
    conv = app.store.current
    app.open_model_config()
    await pilot.pause()
    return conv


@pytest.mark.anyio
async def test_opening_window_pulls_providers_and_config(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)

        assert app.model_config_overlay is not None
        assert app.client.last("config.providers") is not None
        assert conv.cid

        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        listing = app.model_config_overlay.query_one("#provider-list", OptionList)
        assert listing.option_count == 2
        # 当前提供方被预选中（列表第 0 项是 OpenAI）
        assert listing.highlighted == 0


@pytest.mark.anyio
async def test_provider_selection_builds_credential_fields_from_schema(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        await pilot.pause()

        overlay = app.model_config_overlay
        # OpenAI 的 api_key 是密码字段，base_url 带默认值可直接沿用
        api_key = overlay.query_one("#cred-api_key", Input)
        base_url = overlay.query_one("#cred-base_url", Input)
        assert api_key.password is True
        assert base_url.value == "https://api.openai.com/v1"
        # id / type 是元字段，不生成输入框
        assert not overlay.query("#cred-type")

        # 切到 Ollama：无必填项，字段换成 host
        listing = overlay.query_one("#provider-list", OptionList)
        listing.highlighted = 1
        # 重建是「高亮消息 → remove_children + mount」，都是下一拍才落地的，
        # 单次 pause 会随机跑在旧字段上
        await pilot.pause()
        await pilot.pause()
        assert not overlay.query("#cred-api_key")
        assert overlay.query_one("#cred-host", Input)


@pytest.mark.anyio
async def test_masked_key_shows_configured_hint_and_model_prefilled(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        overlay = app.model_config_overlay
        api_key = overlay.query_one("#cred-api_key", Input)
        assert api_key.value == ""
        assert "abcd" in api_key.placeholder

        assert overlay.query_one("#model-name", Input).value == "gpt-4o"
        assert overlay.query_one("#thinking-level", Select).value == "medium"
        assert overlay.query_one("#context-size", Select).value == 32768


@pytest.mark.anyio
async def test_submit_omits_provider_when_credential_untouched(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        overlay = app.model_config_overlay
        overlay.query_one("#model-name", Input).value = "gpt-4o-mini"
        await pilot.pause()
        overlay.submit()
        await pilot.pause()

        command = app.client.last("config.apply_model")
        assert command is not None
        # 只改模型名：不带 provider，沿用已配置的 API key
        assert command.parameters == {"model": "gpt-4o-mini", "thinking_level": "medium", "context_size": 32768}


@pytest.mark.anyio
async def test_submit_includes_provider_when_key_entered(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        overlay = app.model_config_overlay
        overlay.query_one("#cred-api_key", Input).value = "sk-new"
        await pilot.pause()
        overlay.submit()
        await pilot.pause()

        params = app.client.last("config.apply_model").parameters
        assert params["provider_type"] == "openai_credential"
        assert params["credential"]["api_key"] == "sk-new"
        assert params["credential"]["base_url"] == "https://api.openai.com/v1"


@pytest.mark.anyio
async def test_submit_requires_model_name(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        overlay = app.model_config_overlay
        overlay.query_one("#model-name", Input).value = ""
        overlay.submit()
        await pilot.pause()

        assert app.client.last("config.apply_model") is None
        assert "模型名" in plain(overlay.query_one(".overlay-hint"))


@pytest.mark.anyio
async def test_editing_credential_without_key_is_refused(make_app):
    """后端整体替换 credential，部分凭证写回会丢掉已存的 key，必须拦下。"""
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        overlay = app.model_config_overlay
        overlay.query_one("#model-name", Input).value = "gpt-4o"
        # 只改 base_url、不重填 key
        overlay.query_one("#cred-base_url", Input).value = "https://proxy.internal/v1"
        await pilot.pause()
        overlay.submit()
        await pilot.pause()

        assert app.client.last("config.apply_model") is None
        assert "API Key" in plain(overlay.query_one(".overlay-hint"))


@pytest.mark.anyio
async def test_first_time_setup_requires_api_key(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(
            app, conv, {"model": {"provider_type": None, "credential": {}, "model": None}}
        )
        await pilot.pause()

        overlay = app.model_config_overlay
        overlay.query_one("#model-name", Input).value = "gpt-4o"
        await pilot.pause()
        overlay.submit()
        await pilot.pause()

        # 首次配置必须带 key
        assert app.client.last("config.apply_model") is None
        assert "api_key" in plain(overlay.query_one(".overlay-hint"))

        overlay.query_one("#cred-api_key", Input).value = "sk-first"
        await pilot.pause()
        overlay.submit()
        await pilot.pause()

        params = app.client.last("config.apply_model").parameters
        assert params["provider_type"] == "openai_credential"
        assert params["credential"]["api_key"] == "sk-first"


@pytest.mark.anyio
async def test_successful_apply_closes_window_and_updates_status(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        overlay = app.model_config_overlay
        overlay.query_one("#model-name", Input).value = "gpt-4o-mini"
        await pilot.pause()
        overlay.submit()
        await pilot.pause()

        from proxy_layer.schema import make_receipt_success

        command = app.client.last("config.apply_model")
        app._handle_receipt(
            make_receipt_success(
                command.request_id,
                conv.cid,
                {"model": "gpt-4o-mini", "provider_type": "openai_credential", "thinking_level": "medium"},
            )
        )
        await pilot.pause()

        assert app.model_config_overlay is None
        assert app.status._config_model == "gpt-4o-mini"
        assert app.input_area.has_focus


@pytest.mark.anyio
async def test_escape_closes_window(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await _open(app, pilot)
        assert app.model_config_overlay is not None

        await pilot.press("escape")
        await pilot.pause()

        assert app.model_config_overlay is None
        assert app.input_area.has_focus


@pytest.mark.anyio
async def test_f2_opens_window(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()

        assert app.model_config_overlay is not None


@pytest.mark.anyio
async def test_config_reads_go_to_window_instead_of_transcript(make_app):
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.store.current
        before = len(conv.view.children)
        await _open(app, pilot)
        show_providers(app, conv, PROVIDERS)
        show_config(app, conv, CONFIGURED)
        await pilot.pause()

        # 浮窗打开时配置回执不出卡片，但状态栏照常更新
        assert len(conv.view.children) == before
        assert app.status._config_model == "gpt-4o"


@pytest.mark.anyio
async def test_form_builds_for_every_real_provider_schema(make_app):
    """契约测试：后端 list_schemas() 的每个提供方都要能生成可提交的表单。

    前端不 import backend，这里只为锁住「schema 形状 → 表单」的假设。
    """
    from backend.adapter import list_providers

    schemas = list_providers()
    app = make_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        conv = await _open(app, pilot)
        show_providers(app, conv, schemas)
        await pilot.pause()

        overlay = app.model_config_overlay
        listing = overlay.query_one("#provider-list", OptionList)
        assert listing.option_count == len(schemas)

        for index, schema in enumerate(schemas):
            listing.highlighted = index
            await pilot.pause()

            provider_type = schema["properties"]["type"]["const"]
            built = set(overlay._inputs)
            assert built, provider_type
            # 元字段不生成输入框
            assert "type" not in built and "id" not in built
            # 必填项必须有对应输入框，否则永远提交不出去
            assert set(schema.get("required") or []) <= built, provider_type

            for name, spec in schema["properties"].items():
                if spec.get("format") == "password":
                    assert overlay.query_one(f"#cred-{name}", Input).password is True
