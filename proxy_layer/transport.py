import sys
import asyncio

async def read_line() -> str | None:
    
    """从 stdin 读一行。EOF（前端关管道）返回 None。"""
    loop = asyncio.get_running_loop()
    # readline 会阻塞，丢到线程池，避免卡住事件循环
    line = await loop.run_in_executor(None, sys.stdin.readline)
    if not line:          
        return None

    return line.strip()   

async def write_line(line: str) -> None:
    """写一行到 stdout，必须 flush，否则前端可能一直等。"""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()

def log(msg: str) -> None:
    """写一行到 stderr 并 flush。"""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
