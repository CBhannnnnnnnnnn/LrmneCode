"""snapshot 单元测试：只锁一轮快照的保存、跳过和恢复。"""

from backend.snapshot import MAX_ROUNDS, SnapshotManager


def _manager(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return SnapshotManager(str(tmp_path / "home"), "c-1", str(root)), root


def test_begin_round_snapshots_text_and_skips_hidden_or_binary(tmp_path):
    manager, root = _manager(tmp_path)
    note = root / "note.txt"
    note.write_text("你好", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\xff\xfe")
    hidden = root / ".git"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("nope", encoding="utf-8")

    assert manager.begin_round() == 1

    files = manager.list_files(1)
    assert str(note.resolve()) in files or str(note) in files
    assert all(".git" not in path for path in files)
    assert all(not path.endswith("bin.dat") for path in files)
    assert manager.get_original(str(note)) == "你好"
    assert manager.get_original(str(root / "missing.txt")) is None


def test_undo_round_restores_snapshotted_text(tmp_path):
    manager, root = _manager(tmp_path)
    note = root / "note.txt"
    note.write_text("before", encoding="utf-8")
    manager.begin_round()
    note.write_text("after", encoding="utf-8")

    result = manager.undo_round(1)

    assert str(note.resolve()) in result["restored"] or str(note) in result["restored"]
    assert note.read_text(encoding="utf-8") == "before"


def test_missing_round_and_corrupt_meta_are_empty(tmp_path):
    manager, _root = _manager(tmp_path)
    assert manager.undo_round(9) == {"restored": [], "round": 9}
    assert manager.list_files(9) == []

    manager._meta_path.write_text("not json", encoding="utf-8")
    reloaded = SnapshotManager(str(tmp_path / "home"), "c-1", str(tmp_path / "project"))

    assert reloaded.list_rounds() == []


def test_cleanup_old_keeps_only_the_newest_rounds(tmp_path):
    manager, _root = _manager(tmp_path)
    manager._meta["rounds"] = [{"round": index, "files": []} for index in range(1, MAX_ROUNDS + 2)]

    manager.cleanup_old()

    assert [item["round"] for item in manager.list_rounds()] == list(
        range(2, MAX_ROUNDS + 2)
    )
