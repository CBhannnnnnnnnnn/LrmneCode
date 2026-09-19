from __future__ import annotations

from typing import Any, Callable, ClassVar

from agentscope.permission import PermissionMode

PERMISSION_CONFIG_KEYS = frozenset({"mode"})
_MODE_VALUES = frozenset(m.value for m in PermissionMode)


class PermissionConfig:
    """权限模式（对齐 AgentScope PermissionContext.mode）。"""

    _UPDATE_HANDLERS: ClassVar[
        dict[str, Callable[["PermissionConfig", Any, Any], None]]
    ]

    def __init__(self) -> None:
        self._settings: dict[str, str] = {
            "mode": PermissionMode.DEFAULT.value,
        }

    def get(self) -> dict[str, str]:
        """公开视图副本。"""
        return {"mode": self._settings["mode"]}

    def update(self, agent: Any = None, **patch: Any) -> dict[str, str]:
        """更新权限；传入 agent 时同步挂到 PermissionContext。"""
        if patch:
            unknown = set(patch) - set(self._UPDATE_HANDLERS)
            if unknown:
                raise KeyError(
                    f"unknown permission config key(s): {sorted(unknown)}",
                )
            if "mode" in patch:
                self._set_mode(patch["mode"], agent)

        if agent is not None:
            self._mount(agent)

        return self.get()

    def _set_mode(self, value: Any, agent: Any) -> None:
        if isinstance(value, PermissionMode):
            mode_value = value.value
        elif isinstance(value, str):
            mode_value = value.strip().lower()
        else:
            raise TypeError(
                "mode must be a PermissionMode or str "
                f"in {sorted(_MODE_VALUES)}",
            )

        if mode_value not in _MODE_VALUES:
            raise ValueError(
                f"mode must be one of {sorted(_MODE_VALUES)}, "
                f"got {value!r}",
            )
        self._settings["mode"] = mode_value

    def _mount(self, agent: Any) -> None:
        agent.state.permission_context.mode = PermissionMode(
            self._settings["mode"],
        )


PermissionConfig._UPDATE_HANDLERS = {
    "mode": PermissionConfig._set_mode,
}

permission_config = PermissionConfig()

if __name__ == "__main__":

    print(permission_config.get())
