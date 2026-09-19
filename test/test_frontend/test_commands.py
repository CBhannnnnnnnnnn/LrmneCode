"""commands 单元测试：只锁斜杠解析和「参数怎样变成协议命令」。"""

import base64

from frontend.commands import (
    cmd_attach,
    cmd_config,
    cmd_interrupt,
    cmd_model,
    cmd_resume,
    cmd_use_skill,
    find,
    match_prefix,
    parse,
)


class _App:
    def __init__(self):
        self.commands = []
        self.notices = []
        self.attachments = []
        self._chat_inflight = False
        self.skills = {"1": "review"}

    def send_command(self, operation, parameters, kind=None, label=None):
        self.commands.append((operation, parameters))

    def show_notice(self, text, level):
        self.notices.append((text, level))

    def add_attachment(self, item):
        self.attachments.append(item)

    def resolve_skill_name(self, name):
        return self.skills.get(name)


def test_parse_ignores_plain_text_and_bare_slash():
    assert parse("hello") is None
    assert parse("/") is None
    assert parse("  /help") is None


def test_parse_splits_name_and_args():
    assert parse("/Model gpt-4") == ("model", ["gpt-4"])


def test_find_matches_name_or_alias():
    assert find("help").name == "help"
    assert find("?").name == "help"
    assert find("missing") is None


def test_match_prefix_includes_alias():
    names = [cmd.name for cmd in match_prefix("re")]

    assert "resume" in names
    assert "clear" in names  # alias: reset


def test_interrupt_does_nothing_when_idle():
    app = _App()

    cmd_interrupt(app, [])

    assert app.commands == []
    assert app.notices[0][1] == "info"


def test_interrupt_sends_control_command_when_busy():
    app = _App()
    app._chat_inflight = True

    cmd_interrupt(app, [])

    assert app.commands == [("chat.interrupt", {})]


def test_resume_requires_a_cid():
    app = _App()

    cmd_resume(app, [])
    assert app.commands == []

    cmd_resume(app, ["c-9"])
    assert app.commands == [("session.resume", {"source_cid": "c-9"})]


def test_config_maps_args_and_parses_json_values():
    app = _App()

    cmd_config(app, [])
    cmd_config(app, ["mode"])
    cmd_config(app, ["context_size", "3"])
    cmd_config(app, ["note", "hello", "world"])

    assert app.commands == [
        ("config.get", {}),
        ("config.get", {"key": "mode"}),
        ("config.set", {"key": "context_size", "value": 3}),
        ("config.set", {"key": "note", "value": "hello world"}),
    ]


def test_model_switches_between_get_and_set():
    app = _App()

    cmd_model(app, [])
    cmd_model(app, ["gpt-4"])

    assert app.commands == [
        ("config.get", {"key": "model"}),
        ("config.set", {"key": "model", "value": "gpt-4"}),
    ]


def test_use_skill_stops_when_name_cannot_be_resolved():
    app = _App()

    cmd_use_skill(app, [])
    cmd_use_skill(app, ["missing"])
    cmd_use_skill(app, ["1"])

    assert app.commands == [
        ("chat.send", {"text": "请阅读并执行 skill: review"}),
    ]
    assert any(level == "error" for _, level in app.notices)


def test_attach_rejects_missing_file_and_encodes_existing_file(tmp_path):
    missing = _App()
    cmd_attach(missing, [])
    cmd_attach(missing, [str(tmp_path / "nope.txt")])
    assert missing.attachments == []
    assert any(level == "error" for _, level in missing.notices)

    path = tmp_path / "note.txt"
    path.write_bytes("你好".encode("utf-8"))
    app = _App()
    cmd_attach(app, [str(path)])

    assert app.attachments[0]["name"] == "note.txt"
    assert base64.b64decode(app.attachments[0]["data"]) == "你好".encode("utf-8")
