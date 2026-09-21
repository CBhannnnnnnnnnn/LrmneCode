"""把浮窗渲染成纯文本，人工核对视觉与信息密度（非断言型脚本）。"""

import asyncio
import sys

sys.path.insert(0, "test/test_frontend")

from conftest import FakeClient, show_config, show_providers  # noqa: E402

from frontend import app as app_module  # noqa: E402


async def main() -> None:
    app_module.BackendClient = FakeClient
    app = app_module.LrmneAgentApp()

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        conv = app.store.current
        app.open_model_config()
        await pilot.pause()

        show_providers(app, conv, [
            {
                "title": "Anthropic API",
                "required": ["api_key"],
                "properties": {
                    "type": {"const": "anthropic_credential"},
                    "name": {"type": "string", "default": ""},
                    "api_key": {"format": "password", "title": "Api Key"},
                    "base_url": {"title": "Base Url", "default": "https://api.anthropic.com"},
                },
            },
            {
                "title": "OpenAI API",
                "required": ["api_key"],
                "properties": {
                    "type": {"const": "openai_credential"},
                    "api_key": {"format": "password", "title": "Api Key"},
                    "base_url": {"title": "Base Url", "default": "https://api.openai.com/v1"},
                },
            },
            {"title": "Ollama API", "required": [], "properties": {
                "type": {"const": "ollama_credential"},
                "host": {"title": "Host", "default": "http://localhost:11434"},
            }},
        ])
        show_config(app, conv, {
            "model": {
                "provider_type": "openai_credential",
                "credential": {"api_key": {"configured": True, "suffix": "7f3a"}},
                "model": "gpt-4o",
                "thinking_level": "medium",
                "context_size": 32768,
            },
            "permission": {"mode": "default"},
            "workspace": {"root": "/tmp/project"},
        })
        await pilot.pause()

        strips = app.screen._compositor.render_strips()
        for strip in strips:
            print("".join(segment.text for segment in strip).rstrip())


asyncio.run(main())
