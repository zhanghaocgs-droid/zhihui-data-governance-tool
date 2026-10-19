"""现行报盘 登记表型解析器，样例 新标准/dzwd001、新标准/新标准例子/6933。

目录入口为 源电子文件/电子文件登记表.html，Word 另存的网页，GB2312 编码。
首表为案卷信息，按 标签单元格 后接 取值单元格 读取；次表为文件明细，
每行找到形如 Z01_0001.doc 的文件名单元格，其后一格为题名，其前一格为数字时为分册号。
"""
from __future__ import annotations

import re

from lxml import html

from .base import ArchiveScan, Document, ParseResult, Parser, split_code
from .common import add_uncatalogued, pair_by_stem
from ...core.textio import read_text

ARCHIVE_DIR = "存档电子文件"
SOURCE_DIR = "源电子文件"
REG_GLOB = "源电子文件/电子文件登记表.htm*"
REG_GLOB_ALT = "存档电子文件/电子文件登记表.htm*"
MGMT_NAMES = ("电子文件登记表", "载体外标签")

FILE_CELL = re.compile(r"^[A-Za-z]\d{2}_\d{4}(\.[A-Za-z0-9]+)?$")
LABELS = {
    "报告题名": "BGTM", "电子文档号": "DZWDH", "档号": "DH", "载体编号": "ZTBH",
    "形成单位": "XCDW", "形成日期": "XCRQ", "项目负责人": "XMFZR", "提交单位": "TJDW",
    "接收单位": "JSDW",
}


def _cell_text(td) -> str:
    return " ".join(td.text_content().split())


def _norm_date(text: str) -> str:
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return text


class XianxingDjbParser(Parser):
    code = "xianxing_djb"
    label = "现行报盘（登记表型）"

    def parse(self, scan: ArchiveScan) -> ParseResult:
        res = ParseResult()
        regs = scan.glob(REG_GLOB) or scan.glob(REG_GLOB_ALT)
        rows: list[list[str]] = []
        tables = []
        if regs:
            reg_rel = regs[0]
            text, _ = read_text(scan.abspath(reg_rel))
            doc = html.fromstring(text)
            tables = list(doc.iter("table"))
            res.case_source = reg_rel
        else:
            res.notes.append("未找到电子文件登记表，题名全部按缺失处理")

        # 案卷信息
        for t in tables[:1]:
            for tr in t.iter("tr"):
                cells = [_cell_text(td) for td in tr if td.tag in ("td", "th")]
                for i, c in enumerate(cells[:-1]):
                    key = LABELS.get(re.sub(r"\s", "", c))
                    if key and key not in res.case and cells[i + 1]:
                        res.case[key] = cells[i + 1]
        if "XCRQ" in res.case:
            res.case["XCRQ"] = _norm_date(res.case["XCRQ"])
        res.case.setdefault("MJ", "")
        if res.case.get("DH") and res.case["DH"] != scan.dh:
            res.notes.append(f"登记表档号 {res.case['DH']} 与文件夹名 {scan.dh} 不一致")

        # 文件明细
        titles: dict[str, dict] = {}
        for t in tables[1:]:
            volume = None
            for tr in t.iter("tr"):
                cells = [_cell_text(td) for td in tr if td.tag in ("td", "th")]
                for i, c in enumerate(cells):
                    if FILE_CELL.match(c):
                        if i > 0 and cells[i - 1].isdigit():
                            volume = cells[i - 1]
                        stem = c.rsplit(".", 1)[0].upper()
                        title = cells[i + 1] if i + 1 < len(cells) else ""
                        entry = titles.setdefault(stem, {"title": title, "volume": volume, "names": []})
                        entry["names"].append(c)
                        break
                rows.append(cells)

        arch = pair_by_stem(scan, ARCHIVE_DIR)
        src = pair_by_stem(scan, SOURCE_DIR)
        for stem, info in titles.items():
            code = split_code(stem)
            res.documents.append(Document(
                kind="result", category=code[0], volume=info["volume"] or code[1], item=code[2],
                title=info["title"] or None,
                title_source=f"{res.case_source}#{stem}",
                archive_rel=arch.get(stem), source_rel=src.get(stem),
                declared_source=info["names"][0]))

        # 登记表、载体外标签作为管理性文件，存档 PDF 与源文件配对
        for base in MGMT_NAMES:
            a = scan.glob(f"{ARCHIVE_DIR}/{base}.*")
            s = scan.glob(f"{SOURCE_DIR}/{base}.*")
            if a or s:
                res.documents.append(Document(
                    kind="mgmt", category="GL", title=base, title_source="文件名",
                    archive_rel=a[0] if a else None, source_rel=s[0] if s else None))

        add_uncatalogued(scan, res.documents, ARCHIVE_DIR, SOURCE_DIR, res.notes)
        return res
