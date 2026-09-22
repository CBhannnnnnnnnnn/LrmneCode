"""命令行入口：``--version`` 打印版本并退出，不启动 TUI。"""

import sys
from importlib.metadata import PackageNotFoundError

import pytest

import frontend.__main__ as entry
from frontend.__main__ import main


def test_version_flag_prints_and_exits(monkeypatch, capsys):
    """装完之后用 `lrmnecode --version` 验证安装，不该进入界面。"""
    monkeypatch.setattr(sys, "argv", ["lrmnecode", "--version"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"lrmnecode {entry._version()}"


def test_version_falls_back_when_not_installed(monkeypatch):
    """直接从源码运行（未安装）时，读不到分发元数据也不能崩。"""

    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(entry, "dist_version", missing)

    assert entry._version() == "0.0.0+source"
