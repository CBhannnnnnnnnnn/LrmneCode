import asyncio
from pydantic import ValidationError
from . import scheduler
import handlers
from .transport import read_line, write_line
from .schema import (
    BaseProtocol,
    CommandProtocol,
    ErrorCode,
    MessageType,
    build_error_message,
    from_line,
    make_event,
    make_receipt_error,
    to_line,
)

class Runtime:
    """读入站行、校验协议，把调度产出写成出站行。

    非 command 消息忽略；解析失败时 request_id / conversation_id 均为 None。
    """

    def __init__(self):
        self._tasks = set()

    async def run(self):
        while True:

            line = await read_line()
            if line is None:  
                break
            if line == "":
                continue

            try:
                msg = from_line(line)

            # 整行无法解析：尚无会话/请求 id，只能回 invalid_message
            except ValidationError as e:
                receipt = make_receipt_error(
                    None, None,
                    ErrorCode.INVALID_MESSAGE,
                    build_error_message(e),
                )
                await write_line(to_line(receipt))
                continue

            if msg.message_type != MessageType.COMMAND:
                continue


            task = asyncio.create_task(self._execute(msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)


    async def _execute(self, msg: CommandProtocol):
        try:

            async for chunk in scheduler.submit(msg):
                await write_line(to_line(self._as_frame(chunk, msg.conversation_id)))

        except Exception as e:
            receipt = make_receipt_error(
                msg.request_id,
                msg.conversation_id,
                ErrorCode.INTERNAL_ERROR,
                f"unexpected error in runtime: {e}"
            )
            await write_line(to_line(receipt))

    def _as_frame(self, chunk, conversation_id: str):
        """把调度产出收成协议帧；dict 按 {event, data} 包装，避免无帧。"""

        if isinstance(chunk, BaseProtocol):
            return chunk
        
        if isinstance(chunk, dict):
            return make_event(conversation_id, chunk["event"], chunk.get("data", {}))
        raise TypeError(f"无法包装的产出类型: {type(chunk).__name__}")
