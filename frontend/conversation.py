"""按 cid 组织的会话状态与聊天流容器。

协议天然按 ``conversation_id`` 隔离：每条消息带 cid，WAIT 命令的队列也按 cid 排队。
前端因此可以为每个 cid 持有一份独立状态，从而在同一个进程里做多会话切换：
事件与回执都按信封上的 conversation_id 路由到对应的 ``Conversation``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.containers import VerticalScroll

from .widgets import (
    ApprovalPanel,
    AssistantBlock,
    StreamMarkdown,
    ThinkingBlock,
    ToolCallView,
)


@dataclass
class PendingCommand:
    """已发出、等待回执的命令。"""

    request_id: str
    operation: str
    label: str


@dataclass
class TextBlock:
    """流式文本块：一个助手块 + 待刷新的缓冲。"""

    key: str
    block: AssistantBlock
    buffer: str = ""
    scheduled: bool = False

    @property
    def markdown(self) -> StreamMarkdown:
        return self.block.markdown


class ChatView(VerticalScroll):
    """单个会话的聊天流：独占滚动位置与粘底行为。

    会话可能先收到事件、后被打开（session.resume 会落到一个新 cid），
    因此挂载前到达的内容先入缓冲，on_mount 时统一补上。

    粘底要跟着内容长：新块挂载、结果行刷新、Markdown 重渲染的高度都要等布局
    落地才量得到。所以「是否在底部」当场判断（两边都是上一次布局的值，自洽），
    真正的滚动延到刷新之后做——当场滚只会滚到旧内容的末尾，看着像不再跟随。
    """

    def __init__(self, cid: str, **kwargs: Any) -> None:
        kwargs.setdefault("id", f"chat-{cid}")
        super().__init__(**kwargs)
        self.cid = cid
        self._pending: list[Any] = []
        self._pinning = False
        self._height = 0
        # 上次贴底时的滚动位置：视口不在这里、也不在底部，就是用户自己翻过
        self._pinned_at = 0

    def on_mount(self) -> None:
        pending, self._pending = self._pending, []
        for widget in pending:
            super().mount(widget)
        self.stick_bottom()
        # 块内自己长高（结果行刷新、diff 折叠）不会有人来喊，定时看一眼高度变了没
        self.set_interval(0.2, self._follow)

    def add(self, widget: Any) -> None:
        """挂载一个条目并保持粘底。"""
        if not self.is_attached:
            self._pending.append(widget)
            return
        self.mount(widget)
        self.stick_bottom()

    def stick_bottom(self) -> None:
        """用户没翻走时跟着内容走；往上翻阅过就不打扰。

        判据不能只看「现在在不在底部」：布局要几轮刷新才定稳，我们贴上去之后
        内容又长高时就会短暂地"不在底部"，只按这一条判断会把跟随彻底弄丢
        （表现为卡在上一条消息的位置，得手动往下翻）。所以还停在上次贴底的
        位置上也算没翻走。
        """
        try:
            offset = self.scroll_offset.y
            stayed = offset == self._pinned_at or offset >= self.max_scroll_y - 2
        except Exception:
            return
        if not stayed or self._pinning:
            return
        self._pinning = True
        if not self.call_after_refresh(self._pin_now):
            self._pinning = False

    def force_bottom(self) -> None:
        """把底部拉进视野，不管用户翻没翻：要他表态的东西必须看得见。"""
        self.scroll_end(animate=False, force=True)
        self._pinned_at = self.max_scroll_y
        self._height = self.virtual_size.height

    def _pin_now(self) -> None:
        """滚到底；高度还在变就再排一次。

        Markdown 与折叠正文要等下一次刷新才把高度定下来，只滚一次就会差几行，
        看着就是内容往下长、视口停在原地。
        """
        self._pinning = False
        height = self.virtual_size.height
        settling = height != self._height
        self._height = height
        if self.scroll_offset.y != self.max_scroll_y:
            self.scroll_end(animate=False, force=True)
        self._pinned_at = self.max_scroll_y
        if settling:
            self.stick_bottom()

    def _follow(self) -> None:
        height = self.virtual_size.height
        if height != self._height:
            self.stick_bottom()


class Conversation:
    """一个 cid 的全部前端状态：聊天流 + 渲染索引 + 在途与计数。"""

    def __init__(self, cid: str) -> None:
        self.cid = cid
        self.view = ChatView(cid)

        # 渲染索引：reply_id/block_id 或 tool_call_id → 部件
        self.text_blocks: dict[str, TextBlock] = {}
        self.thinking: dict[str, ThinkingBlock] = {}
        self.tools: dict[str, ToolCallView] = {}
        self.approvals: dict[str, ApprovalPanel] = {}
        self.data_blocks: dict[str, dict[str, Any]] = {}

        # 命令与运行状态
        self.pending: dict[str, PendingCommand] = {}
        self.inflight: set[str] = set()  # 在途的流式命令 request_id
        self.queued: set[str] = set()  # 已排队等待前序的 request_id
        # 本次运行是否已下发过中断：一次运行只打断一次，重复按 Esc 不再重发
        # （见 app.action_escape）；run.finished 复位
        self.stopping = False

        # 计数与展示字段
        self.tokens_in = 0
        self.tokens_out = 0
        # 最近一次模型调用的输入长度 ≈ 当前上下文占用，用于上下文压力表
        self.context_tokens = 0
        # 最近一次调用从提示缓存读到的输入 token：状态栏据此算缓存命中率
        self.cache_tokens = 0
        # 最近一次调用写进提示缓存的输入 token：Anthropic 系不计入 input_tokens，
        # 算窗口占用时要一起加回去（见 widgets.prompt_total）
        self.cache_created = 0
        # 最近一次调用的上下文构成（后端按类别估的 token），给状态栏的悬浮浮窗
        self.context_usage: dict | None = None
        # 模型调用累计耗时：状态栏据此算吞吐
        self.gen_seconds = 0.0
        # 最近一次 model.start 的时刻（0 表示当前没有在途调用）
        self.call_started = 0.0
        # 最近一个模型调用产出的文字块（thinking + 正文）：provider 只按「每次调用」报
        # output_tokens，而思考也是模型的产出，所以按各自字数把这份量分摊下去
        self.call_outputs: list[Any] = []
        self.model = ""

        # 本地输入态：每个会话各自保留草稿与待发送附件
        self.draft = ""
        self.attachments: list[dict] = []

    # ---------- 状态查询 ----------

    @property
    def busy(self) -> bool:
        return bool(self.inflight)

    @property
    def run_state(self) -> str:
        """供状态栏渲染：idle / working / queued。"""
        if self.inflight:
            return "working"
        if self.queued:
            return "queued"
        return "idle"

    def reset_tokens(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.context_tokens = 0
        self.cache_tokens = 0
        self.cache_created = 0
        self.gen_seconds = 0.0
        self.call_started = 0.0

    # ---------- 流式文本 ----------

    def begin_text(self, key: str) -> AssistantBlock:
        block = AssistantBlock()
        self.text_blocks[key] = TextBlock(key, block)
        self.call_outputs.append(block)
        return block

    def push_text(self, key: str, delta: str) -> None:
        block = self.text_blocks.get(key)
        if block is not None:
            block.buffer += delta
            block.scheduled = True

    def flush_text(self, key: str) -> bool:
        """把某个块的缓冲写入块内正文；返回是否真的有写入。"""
        block = self.text_blocks.get(key)
        if block is None or not block.scheduled:
            return False
        block.scheduled = False
        block.block.push(block.buffer)
        return True

    def flush_dirty(self) -> bool:
        """写入所有待刷新的块；返回是否有更新（供节流 ticker 调用）。"""
        changed = False
        for key in list(self.text_blocks):
            if self.flush_text(key):
                changed = True
        return changed

    def end_text(self, key: str) -> None:
        """结束一个文本块：写入最终内容、收掉生成状态并移出索引。"""
        block = self.text_blocks.pop(key, None)
        if block is not None:
            block.block.finish(block.buffer)

    def share_usage(self, tokens: int) -> None:
        """把这次调用的 output token 按字数分摊到 thinking 块与正文块上。

        provider 只按「每次调用」报一个 output_tokens，而思考也是模型产出——全记到
        正文块上就把思考的生成量算没了。按字数分摊后各块之和仍等于上报值。
        """
        targets, self.call_outputs = self.call_outputs, []
        if tokens <= 0 or not targets:
            return
        chars = sum(item.chars for item in targets)
        if not chars:
            return
        for item in targets:
            item.mark_usage(int(tokens * item.chars / chars + 0.5))

    def finalize_streams(self) -> None:
        """收尾所有仍在索引里的文本块（reply 结束时兜底）。"""
        for key in list(self.text_blocks):
            self.end_text(key)
        # 这一轮到此为止：用量归因的目标作废，免得下一次调用把数字补到旧块上
        self.call_outputs = []

    def close_open_blocks(self) -> None:
        """run 结束的兜底：把还停在「执行中」的块就地收尾，并清掉索引。

        正常路径下每块都由自己的结束事件收尾；中断时结果帧有可能没能送达
        （见 app.action_escape），块会一直转着动画，run 都结束了还像在跑。
        """
        for view in self.tools.values():
            if not view.finished:
                view.result_end("interrupted")
        for block in self.thinking.values():
            if not block.finished:
                block.finish()
        self.tools.clear()
        self.thinking.clear()

    def set_tools_awaiting(self, pending: bool) -> None:
        """审批挂着的时候，所有还没结束的调用块都不算在跑。

        一批工具调用是整体被审批挂住的：面板只列出要表态的那几个，同批里不需要
        审批的调用也一起停在那儿等，所以它们同样不该转着「执行中 · 20s」计时。
        """
        for view in self.tools.values():
            if pending:
                view.await_approval()
            else:
                view.resume()

    def clear_render_state(self) -> None:
        """清空显示后同步清掉渲染索引，避免残留引用。"""
        self.text_blocks.clear()
        self.thinking.clear()
        self.tools.clear()
        self.data_blocks.clear()
        self.call_outputs = []
        for panel in self.approvals.values():
            panel.resolved = True
        self.approvals.clear()


class ConversationStore:
    """进程内所有会话的前端状态。"""

    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}
        self._current = ""

    # ---------- 查询 ----------

    @property
    def current(self) -> Conversation:
        return self._items[self._current]

    @property
    def current_cid(self) -> str:
        return self._current

    def get(self, cid: str) -> Conversation | None:
        return self._items.get(cid)

    def all(self) -> list[Conversation]:
        return list(self._items.values())

    def get_or_create(self, cid: str | None) -> Conversation:
        """按 cid 取会话；cid 缺失（如 invalid_message 回执）时落到当前会话。"""
        if not cid:
            return self.current
        return self._items.get(cid) or self.create(cid)

    def owner_of(self, request_id: str | None) -> Conversation | None:
        """按 request_id 反查会话：run.finished 等事件只带 request_id。"""
        if not request_id:
            return None
        for conv in self._items.values():
            if request_id in conv.pending or request_id in conv.inflight:
                return conv
        return None

    # ---------- 变更 ----------

    def create(self, cid: str) -> Conversation:
        conv = Conversation(cid)
        self._items[cid] = conv
        if not self._current:
            self._current = cid
        return conv

    def switch(self, cid: str) -> Conversation:
        if cid not in self._items:
            raise KeyError(cid)
        self._current = cid
        return self._items[cid]

    def close(self, cid: str) -> None:
        conv = self._items.pop(cid, None)
        if conv is None:
            return
        conv.view.remove()
        if self._current == cid:
            self._current = next(iter(self._items), "")
