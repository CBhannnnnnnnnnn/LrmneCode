from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from agentscope.credential import CredentialFactory
from agentscope.model import ChatModelBase

ThinkingLevel = Literal["off", "low", "medium", "high"]
_THINKING_LEVELS = frozenset({"off", "low", "medium", "high"})

# 用户主目录全局配置：~/.lrmneagent/settings.json（与项目 cwd 下的 .lrmneagent/ 分离）
_GLOBAL_HOME = Path.home() / ".lrmneagent"
_SETTINGS_PATH = _GLOBAL_HOME / "settings.json"

# config.set / get 路由到本模块的 key
MODEL_CONFIG_KEYS = frozenset(
    {
        "provider",
        "model",
        "thinking_level",
        "context_size",
    },
)


def _empty_settings() -> dict[str, Any]:
    return {
        "provider_type": None,
        "credential": {},
        "model": None,
        "thinking_level": "medium",
        "stream": True,
        "max_retries": 3,
        "context_size": 200_000,
    }


class ModelConfig:
    """模型 / 凭证 / 思考等级 / 上下文窗口。"""

    _UPDATE_HANDLERS: ClassVar[
        dict[str, Callable[["ModelConfig", Any, Any], None]]
    ]

    def __init__(self) -> None:
        self._settings: dict[str, Any] = _empty_settings()
        # 上次加载失败的原因（None 表示正常）；随公开视图带出，不再静默吞掉
        self._settings_error: str | None = None
        self._load()

    def get(self) -> dict[str, Any]:
        """取出当前配置的公开视图（副本）。"""
        return self._public_view()

    def update(self, agent: Any = None, **patch: Any) -> dict[str, Any]:
        """统一更新入口：只做分支，业务在私有方法。"""
        if not patch:
            return self._public_view()

        unknown = set(patch) - set(self._UPDATE_HANDLERS)
        if unknown:
            raise KeyError(f"unknown model config key(s): {sorted(unknown)}")

        # provider 须先于 model / thinking / context，避免半成品 build
        order = ("provider", "model", "thinking_level", "context_size")
        for key in order:
            if key not in patch:
                continue
            self._UPDATE_HANDLERS[key](self, patch[key], agent)

        if agent is not None and self._ready_to_build():
            self._mount(agent)

        self._save()
        return self._public_view()

    def _set_provider(self, value: Any, agent: Any) -> None:
        """provider_type 与 credential 一次写入。"""
        if not isinstance(value, dict):
            raise TypeError(
                "provider must be a dict like "
                '{"type": "openai_credential", "api_key": "...", ...}',
            )
        payload = dict(value)
        provider_type = payload.pop("type", None)
        if not provider_type:
            raise ValueError("provider requires key 'type' (credential type)")

        CredentialFactory.from_dict({"type": provider_type, **payload})

        self._settings["provider_type"] = provider_type
        self._settings["credential"] = payload

    def _set_model(self, value: Any, agent: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            raise TypeError("model must be a non-empty str")
        self._settings["model"] = value.strip()

    def _set_thinking_level(self, value: Any, agent: Any) -> None:
        if value not in _THINKING_LEVELS:
            raise ValueError(
                f"thinking_level must be one of {sorted(_THINKING_LEVELS)}, "
                f"got {value!r}",
            )
        self._settings["thinking_level"] = value

    def _set_context_size(self, value: Any, agent: Any) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TypeError("context_size must be a positive int")
        self._settings["context_size"] = value

    def _load(self) -> None:
        """从 ``~/.lrmneagent/settings.json`` 读取；缺文件或还没配过则保持空默认。

        失败原因记进 ``_settings_error`` 而不是静默吞掉：用户手写配置写错时，
        至少有个说法（随公开视图带出，前端会提示）。
        """
        self._settings_error = None
        if not _SETTINGS_PATH.is_file():
            return

        try:
            raw = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        except OSError as exc:
            self._settings_error = f"读取失败：{exc}"
            return
        except json.JSONDecodeError as exc:
            self._settings_error = f"不是合法 JSON：{exc}"
            return

        if not isinstance(raw, dict):
            self._settings_error = "顶层必须是 JSON 对象"
            return

        # 没有 model 段＝还没配过，保持空默认，不算错误
        section = raw.get("model")
        if section is None:
            return
        if not isinstance(section, dict):
            self._settings_error = "model 段必须是 JSON 对象"
            return

        provider_type = section.get("provider_type")
        credential = section.get("credential")
        model_id = section.get("model")

        if not isinstance(provider_type, str) or not provider_type:
            self._settings_error = "缺少 model.provider_type"
            return
        if not isinstance(model_id, str) or not model_id.strip():
            self._settings_error = "缺少 model.model"
            return
        if not isinstance(credential, dict):
            credential = {}

        try:
            CredentialFactory.from_dict({"type": provider_type, **credential})
        except Exception as exc:
            self._settings_error = f"凭证字段不被接受：{exc}"
            return

        self._settings["provider_type"] = provider_type
        self._settings["credential"] = dict(credential)
        self._settings["model"] = model_id.strip()

    def _save(self) -> None:
        """将提供方 / API / 默认模型写回 ~/.lrmneagent/settings.json（合并其它段）。"""
        path = _SETTINGS_PATH
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}

        provider_type = self._settings.get("provider_type")
        model_id = self._settings.get("model")
        if not provider_type or not model_id:
            # 未配齐不写 model 段，其它配置段原样保留
            data.pop("model", None)
        else:
            data["model"] = {
                "provider_type": provider_type,
                "credential": deepcopy(self._settings.get("credential") or {}),
                "model": model_id,
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _ready_to_build(self) -> bool:
        return bool(
            self._settings.get("provider_type")
            and self._settings.get("model"),
        )

    def _build_model(self) -> ChatModelBase:
        if not self._ready_to_build():
            raise RuntimeError(
                "model is not ready: set provider (with type+credential) "
                "and model first",
            )

        provider_type = self._settings["provider_type"]
        cred = CredentialFactory.from_dict(
            {"type": provider_type, **self._settings["credential"]},
        )
        model_cls = cred.get_chat_model_class()
        parameters = model_cls.Parameters(
            **self._parameters_kwargs(model_cls),
        )
        return model_cls(
            credential=cred,
            model=self._settings["model"],
            parameters=parameters,
            stream=self._settings["stream"],
            max_retries=self._settings["max_retries"],
            context_size=self._settings["context_size"],
        )

    def _parameters_kwargs(self, model_cls: type[ChatModelBase]) -> dict[str, Any]:
        """thinking_level → 该模型 Parameters 能接受的字段。"""
        level: ThinkingLevel = self._settings["thinking_level"]
        fields = model_cls.Parameters.model_fields
        kwargs: dict[str, Any] = {}

        if "thinking_enable" in fields:
            kwargs["thinking_enable"] = level != "off"

        if level == "off":
            return kwargs

        if "reasoning_effort" in fields:
            kwargs["reasoning_effort"] = level
        elif "thinking_budget" in fields:
            kwargs["thinking_budget"] = {
                "low": 1024,
                "medium": 8192,
                "high": 32768,
            }[level]

        return kwargs

    def _mount(self, agent: Any) -> None:
        agent.model = self._build_model()

    def _public_view(self) -> dict[str, Any]:
        cred = deepcopy(self._settings["credential"])
        if "api_key" in cred:
            raw = cred["api_key"]
            if hasattr(raw, "get_secret_value"):
                raw = raw.get_secret_value()
            text = str(raw) if raw is not None else ""
            cred["api_key"] = {
                "configured": bool(text),
                "suffix": text[-4:] if len(text) >= 4 else "",
            }

        return {
            "configured": self._ready_to_build(),
            "provider_type": self._settings["provider_type"],
            "credential": cred,
            "model": self._settings["model"],
            "thinking_level": self._settings["thinking_level"],
            "stream": self._settings["stream"],
            "max_retries": self._settings["max_retries"],
            "context_size": self._settings["context_size"],
            "settings_error": self._settings_error,
        }

ModelConfig._UPDATE_HANDLERS = {
    "provider": ModelConfig._set_provider,
    "model": ModelConfig._set_model,
    "thinking_level": ModelConfig._set_thinking_level,
    "context_size": ModelConfig._set_context_size,
}

model_config = ModelConfig()

if __name__ == "__main__":
    print(model_config.get())