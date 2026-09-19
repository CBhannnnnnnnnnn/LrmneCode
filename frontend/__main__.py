"""入口：``lrmneagent`` 或 ``python -m frontend``。"""

from .app import LrmneAgentApp


def main() -> None:
    LrmneAgentApp().run()


if __name__ == "__main__":
    main()
