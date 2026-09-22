"""主应用。

由单文件 ``app.py`` 拆分而来；``__init__`` 转发 ``LrmneCodeApp`` 与既有的模块级常量，
使 ``from frontend import app``、``app.LrmneCodeApp`` 一类既有用法零改动。
"""

from .main import LrmneCodeApp
from .params import STREAM_FLUSH_INTERVAL

__all__ = ['LrmneCodeApp', 'STREAM_FLUSH_INTERVAL']
