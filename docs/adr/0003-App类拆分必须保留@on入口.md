# App 类不用纯 mixin 拆分，`@on` 入口必须留在类自身

治理 `frontend/app.py` 时想把 `LrmneAgentApp`（71 个方法）按职责拆进若干 mixin。
实测（Textual 8.2.8）与源码确认：**普通 mixin 里的 `@on(...)` 装饰处理器永不触发**。

`textual/message_pump.py:75-95` 的 `_MessagePumpMeta.__new__` 只从 `class_dict`（类**自身**的字典）
收集 `_decorated_handlers`，不合并基类；分发时（同文件 `758` 行起）遍历 `self.__class__.__mro__`，
读每个类自己的 `__dict__["_decorated_handlers"]`。普通 mixin 不是 `MessagePump` 子类，
元类不会处理它，于是它 `__dict__` 里根本没有这张表 —— 处理器**静默不注册**，界面照常启动。

实测对照（同一个 App）：

| 写在 mixin 里的东西 | 是否生效 |
|---|---|
| 普通方法、`compose`、命名约定 `on_*` | 生效（走 `getattr`，按 MRO 解析） |
| `action_*`（绑定声明在 App 类上） | 生效 |
| `@on(...)` 装饰的处理器 | **静默失效** |

`BINDINGS` 同样不是从 mixin 合并的：子类自己的 `BINDINGS` 会整体覆盖基类的
（实测把 `App` 自带的 `ctrl+c`/`ctrl+q` 一起顶掉了）。

**决定**：`app.py` 拆包时，6 个 `@on` 处理器（`InputArea.Submitted`/`TabPressed`/`PaletteNavigate`/`Changed`、
`OptionList.OptionSelected`、`ApprovalPanel.Decision`）与 `BINDINGS` 一律留在 `app/main.py` 的类里，
作为薄入口；其余方法搬进 `app/mixins/*`，靠正常属性查找调用。

**Why:** 这是框架机制决定的，不是风格选择。而且失败形态是静默的 —— 没有异常、没有警告，
只有"点了没反应"，正是本项目历史上最难查的一类问题（同类问题：私有属性撞名顶掉挂载钩子）。写进 ADR 是为了让后来人不要再试一遍纯 mixin。
