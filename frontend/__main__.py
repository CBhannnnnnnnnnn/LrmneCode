"""入口：``lrmneagent`` 或 ``python -m frontend``。"""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version

from .app import LrmneAgentApp

# 分发名（pyproject 的 project.name），与命令名 lrmneagent 不同
DIST_NAME = "lrmne-agent"


def _version() -> str:
    """版本号取自安装元数据；直接从源码运行（未安装）时退回占位。"""
    try:
        return dist_version(DIST_NAME)
    except PackageNotFoundError:
        return "0.0.0+source"


def main() -> None:
    """命令行入口：``--version`` 打印版本并退出，否则启动 TUI。"""
    parser = argparse.ArgumentParser(
        prog="lrmneagent",
        description="LrmneAgent —— 终端编码智能体",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"lrmneagent {_version()}",
    )
    parser.parse_args()
    LrmneAgentApp().run()


if __name__ == "__main__":
    main()
