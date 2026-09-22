"""主应用。

由单文件 ``app.py`` 拆分而来；``__init__`` 转发 ``LrmneAgentApp`` 与既有的模块级常量，
使 ``from frontend import app``、``app.LrmneAgentApp`` 一类既有用法零改动。
"""

from .main import LrmneAgentApp
from .params import STREAM_FLUSH_INTERVAL

__all__ = ['LrmneAgentApp', 'STREAM_FLUSH_INTERVAL']
