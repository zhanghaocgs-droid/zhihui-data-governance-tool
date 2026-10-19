"""ML 目录通用解析内核。V1.0 作为可选入口与 XML 互校，V4.0 作为早期图文主入口。

SUB_DIR.ML 每行为 类型@分目录文件名，例如 正文@Z01_0001.ml。
分目录 ML 每行为 题名@起始页码。题名过长时会折成多行且页码相同，需要合并。
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import ArchiveScan
from ...core.textio import read_text


@dataclass
class MlEntry:
    title: str
    start_page: int | None
    end_page: int | None = None
    line_no: int = 0


def parse_sub_dir(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "@" not in line:
            continue
        kind, _, name = line.rpartition("@")
        out.append((kind.strip(), name.strip()))
    return out


def parse_doc_ml(text: str, total_pages: int | None = None) -> list[MlEntry]:
    entries: list[MlEntry] = []
    for no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if "@" in line:
            title, _, page = line.rpartition("@")
            page = page.strip()
            p = int(page) if page.isdigit() else None
        else:
            title, p = line, None
        title = title.strip()
        # 页码相同或缺页码的续行并入上一条题名
        if entries and (p is None or p == entries[-1].start_page):
            entries[-1].title += title
            continue
        entries.append(MlEntry(title=title, start_page=p, line_no=no))
    for i, e in enumerate(entries):
        nxt = entries[i + 1].start_page if i + 1 < len(entries) else None
        if e.start_page is not None:
            if nxt is not None:
                e.end_page = max(e.start_page, nxt - 1)
            elif total_pages:
                e.end_page = total_pages
    return entries


def read_ml_catalog(scan: ArchiveScan) -> dict:
    """读取档内 ML 目录。返回 {分目录文件名: [MlEntry]}，无 SUB_DIR.ML 时返回空。"""
    sub = scan.resolve("SUB_DIR.ML")
    if not sub:
        return {}
    text, _ = read_text(scan.abspath(sub))
    out = {}
    for kind, name in parse_sub_dir(text):
        rel = scan.resolve(name)
        if not rel:
            out[name] = None
            continue
        t, _ = read_text(scan.abspath(rel))
        out[rel] = parse_doc_ml(t)
    return out
