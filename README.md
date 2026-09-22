# LrmneCode

> 面向当前工作空间的终端编码智能体 —— 在终端里与 AI 结对，读代码、改文件、跑命令。

LrmneCode 是一个运行在终端里的编码助手。它把对话界面（基于 [Textual](https://github.com/Textualize/textual)）
和智能体内核（基于 [AgentScope](https://github.com/agentscope-ai/agentscope)）拆成两个进程，
通过一条行协议通信：你只管在终端里说话，它在当前工作目录里读文件、改代码、执行命令，
每次动文件前都要过一遍权限与审批。

## 特性

- **终端原生**：TUI 交互，`/` 呼出命令、`@` 引用文件、流式输出、工具调用可折叠展开。
- **工作空间感知**：以启动目录为项目根，工具的工作目录绑在源码根上，而不是把整个磁盘当游乐场。
- **权限与审批**：五种权限模式 + 逐次工具审批，危险改动默认要你点头。
- **改动可回滚**：每轮对话开始前快照工作区，`/diff` 看改动、`/undo` 一键还原。
- **会话可续**：会话状态落盘，`/sessions` 随时载入历史存档。
- **多模型提供方**：Anthropic / OpenAI / DashScope / DeepSeek / Gemini / Moonshot / xAI / Ollama。
- **可扩展**：支持 Skills 与 MCP（Model Context Protocol）服务器。

## 环境要求

- Python **3.11+**
- 一个可用的模型 API Key（或本地 Ollama）

## 安装

推荐在虚拟环境中安装：

```bash
# 创建并激活虚拟环境（Windows PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

从 PyPI 安装：

```bash
pip install lrmne-code
```

从源码安装：

```bash
# 克隆仓库后，在仓库根目录执行
pip install .
```

直接从 Git 仓库安装：

```bash
pip install "git+https://github.com/CBhannnnnnnnnnn/LrmneCode.git"
```

开发模式安装（含测试依赖）：

```bash
pip install -e ".[dev]"
```

安装完成后会得到 `lrmnecode` 命令。

## 快速开始

### 1. 配置模型

首次使用需要提供模型提供方与凭证，二选一：

**方式 A：在应用内配置（推荐）**

启动后按 `F2` 或输入 `/model`，在弹出的浮窗里选提供方、填 API Key、填模型名，保存即可。

**方式 B：手写配置文件**

把仓库根目录的 [`settings.example.json`](settings.example.json) 复制到用户主目录下的
`~/.lrmnecode/settings.json`（Windows 为 `C:\Users\<你的用户名>\.lrmnecode\settings.json`），
然后填写：

```json
{
  "model": {
    "provider_type": "openai_credential",
    "credential": {
      "api_key": "sk-在此填入你的密钥",
      "base_url": "https://api.openai.com/v1"
    },
    "model": "gpt-4o"
  }
}
```

可选 `provider_type`：`anthropic_credential`、`openai_credential`、`dashscope_credential`、
`deepseek_credential`、`gemini_credential`、`moonshot_credential`、`xai_credential`、`ollama_credential`。

### 2. 启动

在你想操作的项目目录下执行：

```bash
lrmnecode
```

查看版本：

```bash
lrmnecode --version
```

### 3. 开始对话

直接输入消息回车发送。例如：

```
帮我梳理一下这个项目的目录结构，并指出程序入口在哪
```

按 `Esc` 可暂停正在运行的对话，按 `Ctrl+Q` 退出。

## 常用命令

在输入框里以 `/` 开头即可呼出命令面板（命令**不带参数**，需要选值时会弹出卡片让你挑）。

| 命令 | 别名 | 说明 |
|---|---|---|
| `/help` | `/?` | 显示所有可用命令 |
| `/model` | `/login` | 打开模型配置窗口 |
| `/new` | `/new-chat` | 开新对话 |
| `/sessions` | `/resume` | 切换会话 / 载入磁盘存档 |
| `/clear` | `/reset` | 清空当前会话的显示 |
| `/thinking` | | 选择思考级别（off / low / medium / high） |
| `/permission` | `/perm` | 选择权限模式 |
| `/context` | | 选择上下文窗口大小（200K / 400K / 1M） |
| `/cwd` | `/cd` | 切换工作目录 |
| `/attach` | | 附加文件到下一条消息 |
| `/attachments` | `/attlist` | 查看待发送附件 |
| `/skills` | | 浏览并执行 skill |
| `/mcp` | | 查看已连接的 MCP 服务器 |
| `/diff` | | 查看某一轮的文件改动 |
| `/undo` | | 撤销某一轮的文件修改 |
| `/quit` | `/exit` | 退出 LrmneCode |

### 键盘快捷键

| 按键 | 作用 |
|---|---|
| `Enter` | 发送消息 |
| `Tab` | 补全当前候选（命令 / 引用） |
| `Esc` | 依次：收起面板 → 拒绝审批 → 暂停在途对话 |
| `↑` `↓` | 在候选面板中导航 |
| `F2` | 打开模型配置窗口 |
| `F3` | 打开会话切换窗口 |
| `Ctrl+Q` | 退出 |

### 权限模式

用 `/permission` 切换，取值与含义：

| 模式 | 说明 |
|---|---|
| `default` | 每次改动都询问 |
| `accept_edits` | 工作区内编辑自动允许 |
| `explore` | 只读，改动一律拒绝 |
| `bypass` | 跳过安全检查，谨慎使用 |
| `dont_ask` | 不询问，待处理一律拒绝 |

### 引用文件

在正文里输入 `@` 可按名字引用工作区里的文件、目录或 skill，例如：

```
@backend/adapter/chat.py 这个函数是什么时候被调用的？
```

`@路径` 只是提示词里的一行字，模型会自己用读文件的工具去取内容，不会预先塞进一份可能过期的副本。

## 目录布局

LrmneCode 会在**项目根目录**下创建 `.lrmnecode/` 作为自己的产品元数据目录：

```
<项目根>/.lrmnecode/
├── sessions/     # 会话状态存档（按会话号分目录）
├── snapshots/    # 每轮对话开始前的工作区快照，供 diff / undo
├── skills/       # 项目级 Skills
└── mcp/          # MCP 服务器配置
```

全局配置（模型 / 凭证）独立存放在用户主目录：`~/.lrmnecode/settings.json`。

## 文档

- [文档索引](docs/README.md)
- [核心设计](docs/核心设计.md) —— 架构、协议、调度、注册表
- [开发者指南](docs/开发者指南.md) —— 目录结构、扩展方式、测试
- [用户指南](docs/用户指南.md) —— 安装配置、命令、工作流
- [架构决策记录（ADR）](docs/adr) —— 关键取舍与踩坑记录

## 开发

运行测试：

```bash
pytest test
```

详细的分层约定与扩展方式见 [开发者指南](docs/开发者指南.md)。

## 许可证

本项目采用 MIT 许可证。
