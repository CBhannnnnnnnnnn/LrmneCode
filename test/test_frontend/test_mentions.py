"""mentions 单元测试：逐层浏览、关键字搜、``@`` 触发词、目录钻取与插值。"""

from __future__ import annotations

from frontend.mentions import (
    DIR_KIND,
    FILE_KIND,
    SKILL_KIND,
    Mention,
    accept,
    browse,
    cursor_offset,
    from_skills,
    location_of,
    match,
    reference_spans,
    scope_of,
    search,
    token_at,
    triggered,
    walk,
)


# ---------- 逐层浏览 ----------


def _tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# 指南", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    for noise in ("node_modules", "__pycache__", ".venv"):
        (tmp_path / noise).mkdir()
        (tmp_path / noise / "junk.js").write_text("x", encoding="utf-8")
    (tmp_path / "my notes.md").write_text("x", encoding="utf-8")
    return tmp_path


def test_browse_lists_one_level_dirs_first(tmp_path):
    items = browse(str(_tree(tmp_path)))

    assert [item.label for item in items] == ["docs", "src", "README.md"]
    assert [item.kind for item in items] == [DIR_KIND, DIR_KIND, FILE_KIND]
    assert all("0 B" not in item.note for item in items)  # 大小是 note，不是标签


def test_browse_drills_into_a_scope(tmp_path):
    items = browse(str(_tree(tmp_path)), "src/")

    assert [item.label for item in items] == ["src/app.py", "src/notes.txt"]
    # 目录带进作用域，插入时才能接着往下打
    assert all(item.label.startswith("src/") for item in items)


def test_browse_skips_noise_and_unusable_roots(tmp_path):
    labels = [item.label for item in browse(str(_tree(tmp_path)))]

    assert "my notes.md" not in labels  # 含空格：回读时会被拆成两个词
    assert not any("node_modules" in label for label in labels)
    assert browse("") == []
    assert browse("D:/definitely/not/here") == []
    assert browse(str(tmp_path), "没有这一层/") == []


def test_walk_covers_the_whole_tree(tmp_path):
    labels = [item.label for item in walk(str(_tree(tmp_path)))]

    # 逐层向下、每层目录在前：先根这一层，再进 docs/、src/
    assert labels == [
        "docs",
        "src",
        "README.md",
        "docs/guide.md",
        "src/app.py",
        "src/notes.txt",
    ]


# ---------- 作用域与搜索 ----------


def test_scope_of_splits_level_and_query():
    assert scope_of("work") == ("", "work")
    assert scope_of("backend/work") == ("backend/", "work")
    assert scope_of("backend/adapter/") == ("backend/adapter/", "")
    assert scope_of("") == ("", "")


def test_search_finds_files_under_the_scope(tmp_path):
    root = _tree(tmp_path)

    assert [item.label for item in search(str(root), "", "app")] == ["src/app.py"]
    assert search(str(root), "docs/", "app") == []
    assert [item.label for item in search(str(root), "src/", "app")] == ["src/app.py"]


# ---------- skill 候选 ----------


def test_from_skills_keeps_name_and_description():
    items = from_skills(
        [{"name": "review", "description": "审查改动"}, {"name": ""}, "junk"],
    )

    assert [item.label for item in items] == ["review"]
    assert items[0].kind == SKILL_KIND
    assert "审查改动" in items[0].note


# ---------- 匹配 ----------


def _items(*labels):
    return [Mention(FILE_KIND, label) for label in labels]


def test_match_prefers_filename_then_shorter_path():
    items = _items("backend/workspace.py", "workspace/notes.md", "deep/nest/workspace.py")

    assert [item.label for item in match(items, "workspace")] == [
        "backend/workspace.py",
        "deep/nest/workspace.py",
        "workspace/notes.md",
    ]


def test_match_filters_and_keeps_empty_query_in_order():
    items = _items("a.py", "b.py")

    assert match(items, "zzz") == []
    assert match(items, "") == items
    assert match(items, "@a.py") == [items[0]]  # 查询词里带 @ 也认
    assert len(match(items, "", limit=1)) == 1


# ---------- 触发词 ----------


def test_token_at_needs_a_word_boundary():
    assert token_at("看下 @app", len("看下 @app")) == (3, "app")
    assert token_at("@app") == (0, "app")
    # 邮箱那样贴在字符后面的 @ 不算提及
    assert token_at("mail a@b.com") is None
    # 空白结束一个词：面板该收起
    assert token_at("@app.py 然后") is None
    assert token_at("看下 @", len("看下 @")) == (3, "")
    assert token_at("没有提及") is None
    # 钻进目录后片段里带斜杠，仍算同一个词
    assert token_at("@src/") == (0, "src/")
    # 词尾多打一个西文逗号：认的还是那个路径
    assert token_at("看下 @note.md,") == (3, "note.md")
    # 中文标点说明后面接的是解释，这个词已经写完：面板收起、Enter 就是发送
    assert token_at("看下 @note.md，然后") is None


def test_token_at_only_looks_before_the_cursor():
    text = "@app.py 看下 @note"

    assert token_at(text, len(text)) == (11, "note")
    assert token_at(text, len("@app.py")) == (0, "app.py")


def test_triggered_splits_commands_and_mentions():
    assert triggered("/mo") == ("command", "mo")
    assert triggered("/") == ("command", "")
    assert triggered("/mo now") is None  # 命令只在首词
    assert triggered("看下 @app") == ("mention", "app")
    assert triggered("普通消息") is None


# ---------- 替换与光标 ----------


def test_accept_replaces_only_the_token_and_returns_the_caret():
    text = "看下 @wor 这段"
    updated, caret = accept(text, len("看下 @wor"), Mention(FILE_KIND, "src/work.py"))

    assert updated == "看下 @src/work.py  这段"
    assert updated[:caret] == "看下 @src/work.py "


def test_accept_leaves_whatever_follows_the_token_alone():
    # 词尾手滑多打一个逗号：只换掉那个 @词，逗号原样留在后面
    text = "看下 @not.md,"
    updated, caret = accept(text, len(text), Mention(FILE_KIND, "note.md"))

    assert updated == "看下 @note.md ,"
    assert updated[:caret] == "看下 @note.md "


def test_accept_without_a_live_token_does_nothing():
    assert accept("普通消息", len("普通消息"), Mention(FILE_KIND, "a.py")) is None


def test_accept_keeps_a_directory_open_for_the_next_level():
    updated, caret = accept("@sr", 3, Mention(DIR_KIND, "src"))

    # 目录不加尾空格：加了面板就该收起了，也就钻不进去
    assert updated == "@src/"
    assert caret == 5
    assert token_at(updated, caret) == (0, "src/")


def test_cursor_offset_and_location_round_trip_multiline():
    text = "第一行\n第二行 @app"

    offset = cursor_offset(text, (1, len("第二行 @app")))
    assert offset == len(text)
    assert location_of(text, offset) == (1, len("第二行 @app"))
    assert cursor_offset(text, (0, 2)) == 2


# ---------- 正文里的引用区间 ----------


def test_reference_spans_covers_at_words_only():
    line = "看下 @src/app.py 和 @README.md，谢谢 a@b.com"

    assert reference_spans(line) == [(3, 14), (17, 27)]
    assert reference_spans("没有引用") == []
    assert reference_spans("@") == []
