"""档案扫描盘点。流式遍历，不打开、不哈希文件。"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Callable, Iterator

from ..adapt.parsers.base import ArchiveScan, split_code

# 目录文件识别名（小写），用于盘点时登记目录入口
CATALOG_NAMES = {
    "地质资料文件目录.xml", "sub_dir.ml", "电子文件登记表.html", "电子文件登记表.htm",
    "地质档案文件目录.pdf", "地质资料汇交汇总表.pdf", "成果和原始地质资料清单.doc",
}
# 判定一个目录是否为档案文件夹的标志项（小写）
ARCHIVE_MARKERS = {
    "管理性文件", "存档电子文件", "源电子文件", "成果资料存档电子文件", "成果资料源电子文件",
    "地质资料文件目录.xml", "sub_dir.ml",
}
RAW_DIR_NAMES = {"原始资料电子文件"}


def is_archive_dir(path: Path) -> bool:
    try:
        names = {n.lower() for n in os.listdir(path)}
    except OSError:
        return False
    return bool(names & ARCHIVE_MARKERS)


def discover_archives(data_root: Path | str, max_depth: int = 3) -> Iterator[Path]:
    """在数据目录下找档案文件夹。正式数据为 根/档号/，样例可能多一两层。"""
    root = Path(data_root)
    if is_archive_dir(root):
        yield root
        return

    def walk(d: Path, depth: int):
        try:
            entries = [e for e in os.scandir(d) if e.is_dir()]
        except OSError:
            return
        entries = sorted(entries, key=lambda e: e.name)
        for e in entries:
            p = Path(e.path)
            if is_archive_dir(p):
                yield p
            elif depth < max_depth:
                yield from walk(p, depth + 1)

    yield from walk(root, 1)


def scan_archive(path: Path | str, dh: str | None = None,
                 progress: Callable[[int], None] | None = None) -> ArchiveScan:
    root = Path(path)
    scan = ArchiveScan(dh=dh or root.name, root=root)
    cat_stems: dict[str, set] = {}
    raw_stems: dict[str, set] = {}
    ext = Counter()
    try:
        scan.top_entries = sorted(
            [(e.name, "dir" if e.is_dir() else "file") for e in os.scandir(root)])
    except OSError:
        scan.unreadable += 1

    stack = [(root, 0, False)]
    while stack:
        d, depth, in_raw = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            scan.unreadable += 1
            continue
        with it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    scan.unreadable += 1
                    continue
                name = e.name
                low = name.lower()
                raw_here = in_raw or (depth == 0 and name in RAW_DIR_NAMES)
                if is_dir:
                    scan.dir_count += 1
                    # 附图源文件常以 T01_0001 文件夹形式存在，按件计数
                    if depth <= 1 and not raw_here:
                        c = split_code(name)
                        if c:
                            cat_stems.setdefault(c[0], set()).add(name.upper())
                    stack.append((Path(e.path), depth + 1, raw_here))
                    continue
                scan.file_count += 1
                try:
                    scan.total_bytes += e.stat(follow_symlinks=False).st_size
                except OSError:
                    scan.unreadable += 1
                suffix = low.rsplit(".", 1)[-1] if "." in low else ""
                ext[suffix] += 1
                if low in CATALOG_NAMES or (low.endswith(".ml") and depth == 0) or \
                        (depth == 0 and low.endswith(".xml")):
                    scan.catalog_entries.append(os.path.relpath(e.path, root).replace("\\", "/"))
                stem = name.rsplit(".", 1)[0].upper()
                if raw_here:
                    if depth <= 3 and len(stem) >= 2 and stem[0] in "DCGTYSLXZW" and stem[1:].isdigit():
                        raw_stems.setdefault(stem[0], set()).add(stem)
                elif depth <= 1:
                    c = split_code(name)
                    if c and not low.endswith(".ml"):
                        cat_stems.setdefault(c[0], set()).add(stem)
                if progress and scan.file_count % 5000 == 0:
                    progress(scan.file_count)
    scan.category_counts = {k: len(v) for k, v in sorted(cat_stems.items())}
    scan.raw_counts = {k: len(v) for k, v in sorted(raw_stems.items())}
    scan.ext_counts = dict(ext.most_common())
    scan.catalog_entries.sort()
    return scan


def light_scan(path: Path | str, dh: str | None = None) -> ArchiveScan:
    """只建立目录定位能力、不做统计的轻量扫描，治理阶段使用。"""
    root = Path(path)
    return ArchiveScan(dh=dh or root.name, root=root)
