"""session 单元测试：只锁会话 id 的上下文传递。"""

import contextvars

from proxy_layer.session import conversation_id_var


def test_fresh_context_defaults_to_empty_string():
    assert contextvars.Context().run(conversation_id_var.get) == ""


def test_set_value_is_visible_until_reset():
    token = conversation_id_var.set("c-1")
    try:
        assert conversation_id_var.get() == "c-1"
    finally:
        conversation_id_var.reset(token)
