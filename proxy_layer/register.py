import inspect
import re

from typing import Callable, Type, TypedDict
from pydantic import BaseModel
from .schema import CommandMode


class Operation(TypedDict): 
    handler : Callable
    params_model : Type[BaseModel]
    is_async: bool
    mode : CommandMode

_registry: dict[str, dict[str, Operation]] = {}


def register_command(namespace: str, operation: str, mode: CommandMode):
    """注册操作。注册时只验签名；参数值在执行时由 params_model 校验。

    handler 必须恰好一个参数，且名为 params，类型为 BaseModel 子类。
    """

    def decorator(func: Callable):

        ope : Operation = {}

        if not mode in [m for m in CommandMode]:
            raise TypeError("传入的mode参数必须是'CommandMode'类型的")

        ope["mode"] = mode

        if inspect.iscoroutinefunction(func):
            ope["is_async"] = True
        else:
            ope["is_async"] = False

        params_model = func.__annotations__

        if len(inspect.signature(func).parameters) != 1:
            raise TypeError("被注册的函数只能接受一个参数")

        elif not params_model.get("params"):
            raise TypeError("被注册的函数期望接受的参数名称为 'params' ")

        elif not issubclass(params_model.get("params"), BaseModel):
            raise TypeError("params 应该是 BaseModel 的子类")

        else:
            ope["params_model"] = params_model.get("params")
            ope["handler"] = func

        _registry.setdefault(namespace, {})[operation] = ope
        return func

    return decorator


# 各业务命名空间的注册入口；默认 mode=PLAIN
chat_register = lambda operation, mode=CommandMode.PLAIN: register_command("chat", operation, mode)
config_register = lambda operation, mode=CommandMode.PLAIN: register_command("config", operation, mode)
approval_register = lambda operation, mode=CommandMode.PLAIN: register_command("approval", operation, mode)
session_register = lambda operation, mode=CommandMode.PLAIN: register_command("session", operation, mode)
diff_register = lambda operation, mode=CommandMode.PLAIN: register_command("diff", operation, mode)
skill_register = lambda operation, mode=CommandMode.PLAIN: register_command("skill", operation, mode)
mcp_register = lambda operation, mode=CommandMode.PLAIN: register_command("mcp", operation, mode)


def lookup(namespace_operation: str) -> Operation | None:
    """按 ``namespace.operation`` 查找；格式非法或未注册返回 None。"""

    pattern = r'^[^.]+[.].+$'
    if not re.match(pattern, namespace_operation):
        return None
    namespace, operation = namespace_operation.split(".", 1)
    return _registry.get(namespace, {}).get(operation)

def show_registry() -> list[str]:
    return [f"{ns}.{op}" for ns in _registry for op in _registry[ns]]
