"""解析器接口与文献数据结构。新增时期只需实现 Parser 并在 periods.yaml 登记。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# 成果文件名形如 Z01_0001，首字母为类别代码
CODE_RE = re.compile(r"^([A-Za-z])(\d{2})_(\d{4})$")

# 汇交报盘中的文件类型代码与类别代码对照
WJLX_TO_CATEGORY = {
    "ZW": "Z", "SP": "S", "FT": "T", "FB": "B", "FJ": "J",
    "SJK": "K", "RJ": "R", "DMT": "M", "QT": "Q",
}
CATEGORY_NAMES = {
    "Z": "正文", "S": "审批", "T": "附图", "B": "附表", "J": "附件",
    "K": "数据库", "R": "软件", "M": "多媒体", "Q": "其他", "GL": "管理性文件",
}
RAW_NAMES = {
    "D": "底", "C": "测", "G": "观", "T": "探", "Y": "样",
    "S": "试", "L": "录", "X": "像", "Z": "综", "W": "文",
}


def split_code(name: str) -> tuple[str, str, str] | None:
    """Z01_0001.pdf 返回 (Z, 01, 0001)。"""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    m = CODE_RE.match(stem)
    if not m:
        return None
    return m.group(1).upper(), m.group(2), m.group(3)


@dataclass
class ArchiveScan:
    """一档的扫描结果。只记录统计和目录入口，不保存全部文件清单。"""

    dh: str
    root: Path
    file_count: int = 0
    dir_count: int = 0
    total_bytes: int = 0
    unreadable: int = 0
    category_counts: dict = field(default_factory=dict)
    raw_counts: dict = field(default_factory=dict)
    ext_counts: dict = field(default_factory=dict)
    top_entries: list = field(default_factory=list)
    catalog_entries: list = field(default_factory=list)
    _dir_cache: dict = field(default_factory=dict, repr=False)

    def _listdir_map(self, d: Path) -> dict:
        key = str(d)
        if key not in self._dir_cache:
            try:
                self._dir_cache[key] = {n.lower(): n for n in os.listdir(d)}
            except OSError:
                self._dir_cache[key] = {}
        return self._dir_cache[key]

    def resolve(self, rel: str) -> str | None:
        """大小写不敏感地定位相对路径，返回实际相对路径（正斜杠），找不到返回 None。"""
        parts = [p for p in re.split(r"[\\/]+", rel) if p]
        cur = self.root
        actual = []
        for p in parts:
            m = self._listdir_map(cur)
            real = m.get(p.lower())
            if real is None:
                return None
            actual.append(real)
            cur = cur / real
        return "/".join(actual)

    def exists_dir(self, rel: str) -> bool:
        r = self.resolve(rel)
        return r is not None and (self.root / r).is_dir()

    def glob(self, rel_pattern: str) -> list[str]:
        """支持末段通配的大小写不敏感匹配，如 源电子文件/电子文件登记表.htm*。"""
        import fnmatch
        parts = [p for p in re.split(r"[\\/]+", rel_pattern) if p]
        parent = "/".join(parts[:-1])
        real_parent = self.resolve(parent) if parent else ""
        if real_parent is None:
            return []
        d = self.root / real_parent if real_parent else self.root
        pat = parts[-1].lower()
        out = []
        for low, real in sorted(self._listdir_map(d).items()):
            if fnmatch.fnmatch(low, pat):
                out.append(f"{real_parent}/{real}" if real_parent else real)
        return out

    def list_dir(self, rel: str) -> list[str]:
        r = self.resolve(rel) if rel else ""
        if r is None:
            return []
        d = self.root / r if r else self.root
        return sorted(self._listdir_map(d).values())

    def abspath(self, rel: str) -> Path:
        return self.root / rel


@dataclass
class Document:
    """一份文献。kind 取 result 成果文献、mgmt 管理性文件、raw 原始资料。"""

    kind: str
    category: str
    title: str | None
    title_source: str
    volume: str | None = None
    item: str | None = None
    archive_rel: str | None = None      # 存档文件相对路径
    source_rel: str | None = None       # 源文件或源文件夹相对路径
    resources: list = field(default_factory=list)   # 关联资源目录，如插图、MapGIS 工程
    secrecy: str | None = None
    stage_ref: str | None = None        # 目录中的阶段参考字段，V2.0 起使用
    declared_archive: str | None = None  # 目录登记的存档文件名
    declared_source: str | None = None   # 目录登记的源文件名
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """文献在档内的稳定标识，用于人工结论回写。"""
        return self.archive_rel or self.source_rel or f"{self.kind}:{self.category}:{self.volume}:{self.item}:{self.title}"


@dataclass
class ParseResult:
    case: dict = field(default_factory=dict)
    case_source: str = ""
    documents: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    issues: list = field(default_factory=list)


class Parser:
    """解析器基类。"""

    code = ""
    label = ""
    supported = True

    def parse(self, scan: ArchiveScan) -> ParseResult:
        raise NotImplementedError


class UnsupportedParser(Parser):
    """尚未实现的时期，占位用，盘点时能识别，治理时提示暂不支持。"""

    supported = False

    def parse(self, scan: ArchiveScan) -> ParseResult:
        raise NotImplementedError(f"{self.label} 解析器将在后续版本实现")
