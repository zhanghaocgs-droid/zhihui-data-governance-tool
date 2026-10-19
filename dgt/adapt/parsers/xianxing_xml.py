"""现行报盘 XML 型解析器，样例 Edmaker2017。

目录入口为 管理性文件/地质资料文件目录.xml，根节点 AJXX。
案卷字段取根节点下的叶子节点；成果文件取 CGWJCList/CGWJXXList；原始资料取 YSWJMLList/YSWJXXList。
"""
from __future__ import annotations

from lxml import etree

from .base import ArchiveScan, Document, ParseResult, Parser, WJLX_TO_CATEGORY, split_code
from .common import add_uncatalogued, pair_by_stem, rel_from_submitter_path
from ...core.textio import decode_bytes

CATALOG = "管理性文件/地质资料文件目录.xml"
ARCHIVE_DIR = "存档电子文件"
SOURCE_DIR = "源电子文件"
RAW_DIR = "原始资料电子文件"
MGMT_DIR = "管理性文件"
ANCHORS = (ARCHIVE_DIR, SOURCE_DIR, RAW_DIR, MGMT_DIR)


def load_xml(path) -> etree._Element:
    raw = open(path, "rb").read()
    parser = etree.XMLParser(recover=True, huge_tree=True)
    try:
        root = etree.fromstring(raw, parser)
        if root is not None:
            return root
    except (etree.XMLSyntaxError, ValueError):
        pass
    text, _ = decode_bytes(raw)
    text = text.split("?>", 1)[1] if text.lstrip().startswith("<?xml") else text
    return etree.fromstring(text.encode("utf-8"), parser)


def _leaf_fields(el) -> dict:
    out = {}
    for c in el:
        if isinstance(c.tag, str) and len(c) == 0:
            out[c.tag] = (c.text or "").strip()
    return out


class XianxingXmlParser(Parser):
    code = "xianxing_xml"
    label = "现行报盘（XML型）"

    def parse(self, scan: ArchiveScan) -> ParseResult:
        res = ParseResult()
        cat_rel = scan.resolve(CATALOG)
        root = load_xml(scan.abspath(cat_rel))
        res.case = _leaf_fields(root)
        res.case_source = cat_rel
        arch_files = pair_by_stem(scan, ARCHIVE_DIR)
        src_files = pair_by_stem(scan, SOURCE_DIR)

        # 成果文件
        for grp in root.findall("CGWJCList"):
            wjlx = (grp.findtext("WJLX") or "").strip().upper()
            category = WJLX_TO_CATEGORY.get(wjlx)
            for f in grp.findall("CGWJXXList"):
                fd = _leaf_fields(f)
                src_name = fd.get("WJMC") or ""
                arc_name = fd.get("CDWJMC") or ""
                code = split_code(arc_name) or split_code(src_name)
                cat = category or (code[0] if code else "Q")
                stem = (arc_name or src_name).rsplit(".", 1)[0].upper() if (arc_name or src_name) else ""
                arch_rel = scan.resolve(f"{ARCHIVE_DIR}/{arc_name}") if arc_name else None
                src_rel = scan.resolve(f"{SOURCE_DIR}/{src_name}") if src_name else None
                arch_rel = arch_rel or arch_files.get(stem)
                src_rel = src_rel or src_files.get(stem)
                resources = []
                for ct in f.findall("CTList"):
                    rel = rel_from_submitter_path(ct.findtext("CTPath"), ANCHORS)
                    if rel:
                        real = scan.resolve(rel)
                        resources.append({"name": ct.findtext("CTMC") or "", "rel": real or rel,
                                          "exists": real is not None})
                extra = {k: fd[k] for k in ("BLC", "TH", "BZ", "SMZL", "SMSX", "WJDX", "CDWJDX") if fd.get(k)}
                extra["xml_id"] = fd.get("Id")
                res.documents.append(Document(
                    kind="result", category=cat,
                    volume=fd.get("CH"), item=fd.get("JH"),
                    title=fd.get("WJBT") or None,
                    title_source=f"{cat_rel}#CGWJXXList[Id={fd.get('Id')}]",
                    archive_rel=arch_rel, source_rel=src_rel, resources=resources,
                    secrecy=fd.get("MJ") or None,
                    declared_archive=arc_name or None, declared_source=src_name or None,
                    extra=extra))

        # 管理性文件全部作为管理性文献
        mgmt = scan.resolve(MGMT_DIR)
        for name in scan.list_dir(mgmt):
            rel = f"{mgmt}/{name}"
            if scan.abspath(rel).is_dir():
                continue
            res.documents.append(Document(
                kind="mgmt", category="GL", title=name.rsplit(".", 1)[0],
                title_source="文件名", archive_rel=rel))

        # 原始资料目录，只登记
        for grp in root.findall("YSWJMLList"):
            dz = (grp.findtext("DZ") or "").strip()
            raw_cat = dz.split("/")[-1].strip().upper() if "/" in dz else dz[:1]
            for f in grp.findall("YSWJXXList"):
                fd = _leaf_fields(f)
                fname = fd.get("DZWJM") or ""
                rel = None
                if fname:
                    rel = scan.resolve(f"{RAW_DIR}/{fname}")
                    if rel is None and "." not in fname:
                        hits = scan.glob(f"{RAW_DIR}/{fname}.*")
                        rel = hits[0] if hits else None
                res.documents.append(Document(
                    kind="raw", category=raw_cat, volume=fd.get("XH"), item=fd.get("JH"),
                    title=fd.get("WJMC") or None,
                    title_source=f"{cat_rel}#YSWJXXList[Id={fd.get('Id')}]",
                    archive_rel=rel, secrecy=fd.get("MJ") or None,
                    declared_archive=f"{RAW_DIR}/{fname}" if fname else None,
                    extra={"SFHJ": fd.get("SFHJ"), "ZTXS": fd.get("ZTXS"), "xml_id": fd.get("Id")}))

        add_uncatalogued(scan, res.documents, ARCHIVE_DIR, SOURCE_DIR, res.notes)
        return res
