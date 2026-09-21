"""数据块的落地与描述：base64 → 字节 → 人类可读的展示形态。

协议里数据块以增量 base64 字符串流入（``stream.data`` / ``tool.result.data``），
完整后才能解码。文本内嵌展示；图片在终端里画不出来，落到临时文件并显示路径；
其余类型只报大小。
"""

from __future__ import annotations

import base64
import binascii
import os
import tempfile

# 常见媒体类型 → 临时文件扩展名
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
}


def decode_base64(raw: str) -> bytes | None:
    """解码增量拼接的 base64；补齐省略的 padding，彻底解不动再放弃。"""
    text = "".join((raw or "").split())
    if not text:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        try:
            return base64.b64decode(text + "=" * (-len(text) % 4))
        except (binascii.Error, ValueError):
            return None


def save_temp(data: bytes, media_type: str) -> str:
    """把数据写进系统临时目录，返回路径；扩展名按媒体类型给。"""
    fd, path = tempfile.mkstemp(
        prefix="lrmne-", suffix=EXTENSIONS.get(media_type, ".bin")
    )
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def human_size(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def describe_image(data: bytes, media_type: str) -> str:
    """图片的一行描述：尽量带上像素尺寸。"""
    dims = _png_dimensions(data)
    size = human_size(len(data))
    return f"{dims[0]}×{dims[1]} · {size}" if dims else size


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
