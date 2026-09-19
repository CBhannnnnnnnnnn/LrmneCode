"""入口：在项目根目录运行 ``python -m frontend``。"""

from .app import CodeAgentApp


def main() -> None:
    CodeAgentApp().run()


if __name__ == "__main__":
    main()
