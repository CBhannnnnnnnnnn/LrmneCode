"""commands 单元测试：锁斜杠解析、命令集边界，以及「参数怎样变成协议命令」。

命令一律不带参数是硬约束（见 commands 模块 docstring），因此这里额外锁两条：
处理器签名里不能有第二个参数；已被卡片取代的旧命令不能再出现在命令表里。
"""

import base64
import inspect
from dataclasses import dataclass

import pytest

from frontend.commands import (
    COMMANDS,
    attach_file,
    cmd_attach,
    cmd_cwd,
    cmd_diff,
    cmd_model,
    cmd_permission,
    cmd_sessions,
    cmd_skills,
    cmd_thinking,
    cmd_undo,
    find,
    match_prefix,
    parse,
)


class _Conv:
    def __init__(self):
        self.inflight = set()


class _Store:
    def __init__(self):
        self.current = _Conv()


@dataclass
class _Opened:
    kind: str
    title: str
    choices: list | None = None
    current: object = None
    on_select: object = None
    on_submit: object = None


class _App:
    """替身：只记录命令处理器对外发出的调用。"""

    def __init__(self):
        self.sent = []
        self.chats = []
        self.notices = []
        self.attachments = []
        self.pickers = []
        self.prompts = []
        self.confirmations = []
        self.store = _Store()
        self.model_window_opened = 0
        self.session_window_opened = 0
        self.config = {
            "thinking_level": "medium",
            "mode": "default",
            "root": "D:/code/demo",
        }

    # -- 协议 / 提示 --

    def send(self, operation, parameters):
        self.sent.append((operation, parameters))

    def set_config_value(self, key, value, confirmation):
        self.sent.append(("config.set", {"key": key, "value": value}))
        self.confirmations.append(confirmation)

    def send_chat(self, text):
        self.chats.append(text)

    def notify_line(self, text, level):
        self.notices.append((text, level))

    def add_attachment(self, item):
        self.attachments.append(item)

    # -- 浮窗 --

    def open_model_config(self):
        self.model_window_opened += 1

    def open_sessions(self):
        self.session_window_opened += 1

    def open_picker(self, kind, title, choices=None, current=None, on_select=None):
        opened = _Opened(kind, title, choices, current, on_select=on_select)
        self.pickers.append(opened)
        return opened

    def open_prompt(
        self, kind, title, caption="", value="", placeholder="", on_submit=None
    ):
        opened = _Opened(
            kind, title, current=value, on_submit=on_submit
        )
        self.prompts.append(opened)
        return opened

    # -- 配置缓存 --

    @property
    def thinking_level(self):
        return str(self.config.get("thinking_level") or "")

    @property
    def permission_mode(self):
        return str(self.config.get("mode") or "")

    @property
    def workspace_root(self):
        return str(self.config.get("root") or "")

    @property
    def pending_attachments(self):
        return self.attachments


def test_parse_ignores_plain_text_and_bare_slash():
    assert parse("hello") is None
    assert parse("/") is None
    assert parse("  /help") is None


def test_parse_splits_name_and_args():
    assert parse("/Model gpt-4") == ("model", ["gpt-4"])


def test_find_matches_name_or_alias():
    assert find("help").name == "help"
    assert find("?").name == "help"
    assert find("resume").name == "sessions"
    assert find("missing") is None


def test_match_prefix_includes_alias():
    names = [cmd.name for cmd in match_prefix("re")]

    assert "sessions" in names  # alias: resume
    assert "clear" in names  # alias: reset


def test_every_command_handler_takes_only_the_app():
    """命令不吃参数：签名里出现第二个位置参数就说明参数又漏回用户手里了。"""
    for cmd in COMMANDS:
        assert cmd.handler is not None, cmd.name
        params = list(inspect.signature(cmd.handler).parameters.values())
        assert len(params) == 1, f"/{cmd.name} 接受参数了：{params}"


def test_internal_commands_are_not_registered():
    """取值域命令与存档命令都被卡片取代，不能再作为斜杠命令出现。"""
    for name in ("config", "delete", "rm", "use-skill"):
        assert find(name) is None, name


def test_interrupt_is_escape_only():
    """暂停只走 Esc：命令表里不该再有 /interrupt（或它的 stop 别名）。"""
    assert find("interrupt") is None
    assert find("stop") is None


def test_sessions_opens_the_switcher_window():
    app = _App()

    cmd_sessions(app)

    assert app.session_window_opened == 1
    assert app.sent == []


def test_model_opens_window():
    app = _App()

    cmd_model(app)

    assert app.model_window_opened == 1
    assert app.sent == []


def test_thinking_offers_levels_and_writes_the_chosen_one():
    app = _App()

    cmd_thinking(app)

    (picker,) = app.pickers
    assert picker.kind == "thinking"
    assert [choice.value for choice in picker.choices] == ["off", "low", "medium", "high"]
    assert picker.current == "medium"  # 卡片要标出当前值
    assert app.sent == []  # 打开卡片本身不发命令

    picker.on_select("high")

    assert app.sent == [("config.set", {"key": "thinking_level", "value": "high"})]


def test_permission_offers_modes_with_notes():
    app = _App()

    cmd_permission(app)

    (picker,) = app.pickers
    assert picker.kind == "permission"
    assert picker.current == "default"
    assert all(choice.note for choice in picker.choices)  # 每个模式都要有说明

    picker.on_select("accept_edits")
    assert app.sent == [("config.set", {"key": "mode", "value": "accept_edits"})]


def test_cwd_prompts_with_current_root_and_sets_it():
    app = _App()

    cmd_cwd(app)

    (prompt,) = app.prompts
    assert prompt.current == "D:/code/demo"  # 预填当前值，回车即原样写回
    assert app.sent == []

    prompt.on_submit("D:/code/other")
    assert app.sent == [("config.set", {"key": "root", "value": "D:/code/other"})]


def test_skills_lists_then_runs_the_picked_skill():
    app = _App()

    cmd_skills(app)

    (picker,) = app.pickers
    assert picker.kind == "skills"
    assert app.sent == [("skill.list", {})]

    picker.on_select("review")
    assert app.chats == ["请阅读并执行 skill: review"]


@pytest.mark.parametrize(
    ("handler", "operation"),
    [(cmd_diff, "diff.show"), (cmd_undo, "diff.undo")],
)
def test_diff_commands_pick_a_round_from_the_receipt(handler, operation):
    app = _App()

    handler(app)

    (picker,) = app.pickers
    assert picker.kind in ("diff", "undo")
    assert picker.choices is None  # 候选项来自 diff.list 回执
    assert app.sent == [("diff.list", {})]

    picker.on_select(3)
    assert app.sent[-1] == (operation, {"round": 3})


def test_attach_opens_a_prompt_instead_of_taking_an_argument():
    app = _App()

    cmd_attach(app)

    (prompt,) = app.prompts
    assert prompt.kind == "attach"
    assert app.sent == []


def test_attach_file_rejects_missing_file_and_encodes_existing_file(tmp_path):
    missing = _App()
    attach_file(missing, str(tmp_path / "nope.txt"))
    assert missing.attachments == []
    assert any(level == "error" for _, level in missing.notices)

    path = tmp_path / "note.txt"
    path.write_bytes("你好".encode("utf-8"))
    app = _App()
    attach_file(app, str(path))

    assert app.attachments[0]["name"] == "note.txt"
    assert base64.b64decode(app.attachments[0]["data"]) == "你好".encode("utf-8")
