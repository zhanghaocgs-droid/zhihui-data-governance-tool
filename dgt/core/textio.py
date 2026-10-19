"""文本读取。ML、XML、登记表可能是 GBK、GB2312 或 UTF-8 编码。"""
from __future__ import annotations

from pathlib import Path

_ENCODINGS = ("utf-8-sig", "gb18030", "utf-16")


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """返回解码文本与所用编码。gb18030 兼容 GBK 和 GB2312。"""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace"), "gb18030-replace"


def read_text(path: Path | str) -> tuple[str, str]:
    return decode_bytes(Path(path).read_bytes())
