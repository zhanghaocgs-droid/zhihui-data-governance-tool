"""交接清单读取与对账。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

# 交接清单列名与类别代码对照
RESULT_COLS = {"正文": "Z", "审批": "S", "附图": "T", "附表": "B", "附件": "J",
               "软件": "R", "数据库": "K", "多媒体": "M", "其他": "Q"}
RAW_COLS = {"底/D": "D", "测/C": "C", "观/G": "G", "探/T": "T", "样/Y": "Y",
            "试/S": "S", "录/L": "L", "像/X": "X", "综/Z": "Z", "文/W": "W"}


@dataclass
class HandoverRow:
    dh: str
    sheet: str
    seq: int
    counts: dict = field(default_factory=dict)
    raw_counts: dict = field(default_factory=dict)
    file_count: int | None = None
    dir_count: int | None = None
    data_size: str | None = None
    data_bytes: int | None = None


_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def parse_size(text) -> int | None:
    if text is None:
        return None
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)\s*$", str(text), re.I)
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def dh_text(value) -> str | None:
    """档号统一按文本处理。Excel 中的数值转为不带小数的文本。"""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    return s or None


def dh_key(dh: str) -> str:
    """对账比对键。纯数字档号去掉前导零后比较，其余原样。"""
    return str(int(dh)) if dh.isdigit() else dh


def read_handover(path: Path | str) -> list[HandoverRow]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: list[HandoverRow] = []
    for ws in wb.worksheets:
        header = None
        for r in ws.iter_rows(values_only=True):
            cells = list(r)
            if header is None:
                if "档号" in [str(c).strip() if c is not None else "" for c in cells]:
                    header = [str(c).strip() if c is not None else "" for c in cells]
                continue
            rec = dict(zip(header, cells))
            seq = rec.get("序号")
            dh = dh_text(rec.get("档号"))
            # 每表末尾有说明文字，序号非数字的行不是数据
            if not isinstance(seq, (int, float)) or not dh:
                continue
            row = HandoverRow(dh=dh, sheet=ws.title, seq=int(seq))
            row.counts = {code: int(rec.get(col) or 0) for col, code in RESULT_COLS.items() if col in rec}
            row.raw_counts = {code: int(rec.get(col) or 0) for col, code in RAW_COLS.items() if col in rec}
            row.file_count = int(rec["文件数"]) if rec.get("文件数") is not None else None
            row.dir_count = int(rec["文件夹数"]) if rec.get("文件夹数") is not None else None
            row.data_size = str(rec.get("数据量")) if rec.get("数据量") is not None else None
            row.data_bytes = parse_size(row.data_size)
            rows.append(row)
    wb.close()
    return rows


def reconcile(scan, row: HandoverRow | None, size_tolerance: float = 0.02) -> tuple[str, list[str]]:
    """返回对账状态和差异说明。状态为 一致、数量差异、清单外、文件不可读。"""
    if row is None:
        return "清单外", ["交接清单中没有该档号"]
    diffs = []
    if row.file_count is not None and row.file_count != scan.file_count:
        diffs.append(f"文件数 清单{row.file_count} 实扫{scan.file_count}")
    if row.dir_count is not None and row.dir_count != scan.dir_count:
        diffs.append(f"文件夹数 清单{row.dir_count} 实扫{scan.dir_count}")
    if row.data_bytes and scan.total_bytes:
        if abs(row.data_bytes - scan.total_bytes) > row.data_bytes * size_tolerance:
            diffs.append(f"数据量 清单{row.data_size} 实扫{scan.total_bytes / 1024 ** 2:.1f} MB")
    for code, n in row.counts.items():
        got = scan.category_counts.get(code, 0)
        if n != got:
            diffs.append(f"类别{code} 清单{n}件 实扫{got}件")
    if scan.unreadable:
        return "文件不可读", diffs + [f"{scan.unreadable} 项无法读取"]
    return ("一致" if not diffs else "数量差异"), diffs
