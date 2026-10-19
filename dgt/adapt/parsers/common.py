"""各解析器共用的文件配对与补漏逻辑。"""
from __future__ import annotations

import re

from .base import ArchiveScan, Document, split_code


def pair_by_stem(scan: ArchiveScan, folder: str) -> dict[str, str]:
    """列出某目录下按编号命名的文件和文件夹，返回 {大写编号: 实际相对路径}。"""
    out = {}
    real = scan.resolve(folder)
    if real is None:
        return out
    for name in scan.list_dir(real):
        c = split_code(name)
        if not c:
            continue
        stem = name.rsplit(".", 1)[0].upper() if "." in name else name.upper()
        rel = f"{real}/{name}"
        # 同一编号既有文件又有文件夹时优先文件
        if stem in out and scan.abspath(out[stem]).is_file():
            continue
        out[stem] = rel
    return out


def rel_from_submitter_path(abs_path: str | None, anchors: tuple[str, ...]) -> str | None:
    """把汇交单位机器上的绝对路径转换为档内相对路径。"""
    if not abs_path:
        return None
    parts = re.split(r"[\\/]+", abs_path)
    for i, p in enumerate(parts):
        if p in anchors:
            return "/".join(parts[i:])
    return None


def add_uncatalogued(scan: ArchiveScan, docs: list[Document], archive_dir: str, source_dir: str,
                     notes: list[str]) -> None:
    """目录未登记但存档区或源文件区存在的编号文件补为文献，题名标缺失。"""
    known = set()
    for d in docs:
        for rel in (d.archive_rel, d.source_rel):
            if rel:
                known.add(rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper())
    arch = pair_by_stem(scan, archive_dir)
    src = pair_by_stem(scan, source_dir)
    for stem in sorted(set(arch) | set(src)):
        if stem in known:
            continue
        c = split_code(stem)
        docs.append(Document(kind="result", category=c[0], volume=c[1], item=c[2], title=None,
                             title_source="文件名（目录未登记）", archive_rel=arch.get(stem),
                             source_rel=src.get(stem)))
        notes.append(f"{stem} 目录未登记，按文件名补录")
