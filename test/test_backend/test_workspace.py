"""workspace 单元测试：只锁会话存档和根路径，不初始化沙箱。"""

import os

from agentscope.state import AgentState

from backend.workspace import CODE_HOME_NAME, SessionManager, WorkspaceManager


def test_session_roundtrip_and_corrupt_file(tmp_path):
    manager = SessionManager(str(tmp_path))
    assert manager.load_state("missing") is None

    saved = AgentState(summary="hello")
    manager.save_state("c-1", saved)
    loaded = manager.load_state("c-1")

    assert loaded is not None
    assert loaded.summary == "hello"
    assert loaded.session_id == saved.session_id

    state_path = tmp_path / "sessions" / "bad" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("not json", encoding="utf-8")
    assert manager.load_state("bad") is None


def test_bind_get_and_drop_live_agents(tmp_path):
    manager = SessionManager(str(tmp_path))
    agent = object()

    assert manager.get("c-1") is None
    manager.bind("c-1", agent)

    assert manager.get("c-1") is agent
    assert manager.live_agents() == [agent]

    manager.drop_all()
    assert manager.live_agents() == []


def test_workspace_manager_normalizes_root_and_creates_code_home(tmp_path):
    root = tmp_path / "proj"
    manager = WorkspaceManager(f"  {root}  ")

    assert manager.project_root == os.path.abspath(root)
    assert manager.code_home == os.path.join(manager.project_root, CODE_HOME_NAME)
    assert os.path.isdir(manager.code_home)
