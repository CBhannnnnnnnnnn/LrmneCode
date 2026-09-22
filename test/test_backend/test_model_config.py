"""后端模型配置的出厂默认：进入系统即上下文 200k、思考 medium。

默认值住在 ``ModelConfig._empty_settings``（``_load`` 不还原这两项，所以每次启动
都回到这里）。这里既要锁住默认视图，也要证明默认值**真的进了模型构建链路**
（context_size 传给模型、thinking_level 映射成 Parameters），而不只是前端显示。
"""

from backend.user import model as model_module


def test_fresh_config_defaults_to_200k_context_and_medium_thinking():
    view = model_module.ModelConfig().get()
    assert view["context_size"] == 200_000
    assert view["thinking_level"] == "medium"


def test_defaults_reach_the_built_model(monkeypatch):
    """默认值要落到 model 构造参数与 Parameters 上，而不是停在配置视图里。"""
    captured: dict = {}

    class _FakeParameters:
        # _parameters_kwargs 按字段名探测能接受哪些思考参数
        model_fields = {"thinking_enable": None, "reasoning_effort": None}

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeModel:
        Parameters = _FakeParameters

        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeCredential:
        def get_chat_model_class(self):
            return _FakeModel

    monkeypatch.setattr(
        model_module.CredentialFactory,
        "from_dict",
        staticmethod(lambda data: _FakeCredential()),
    )

    config = model_module.ModelConfig()
    config._settings["provider_type"] = "fake"
    config._settings["model"] = "fake-model"
    config._build_model()

    # 默认上下文窗口原样传给模型
    assert captured["context_size"] == 200_000
    # 默认思考档 medium 映射成引擎参数：开思考 + reasoning_effort
    assert captured["parameters"].kwargs == {
        "thinking_enable": True,
        "reasoning_effort": "medium",
    }
