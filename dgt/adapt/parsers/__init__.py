"""解析器注册表。periods.yaml 中的 parser 字段对应这里的键。"""
from __future__ import annotations

from .base import Parser, UnsupportedParser
from .xianxing_djb import XianxingDjbParser
from .xianxing_xml import XianxingXmlParser


class JinqiParser(UnsupportedParser):
    """近期报盘 Edmaker2014，V2.0 实现：根目录 XML、地质档案文件目录阶段参考、成果源与存档配对。"""
    code = "jinqi"
    label = "近期报盘（Edmaker2014）"


class QingqianParser(UnsupportedParser):
    """清欠期，V3.0 实现：补交标识识别、签章缺失标注、真实性待核。"""
    code = "qingqian"
    label = "清欠期"


class ZaoqiParser(UnsupportedParser):
    """早期图文，V4.0 实现：SUB_DIR.ML 解析、页图组装、多页 TIF 合成 PDF、图件资源组关联。"""
    code = "zaoqi"
    label = "早期图文标准"


REGISTRY: dict[str, type[Parser]] = {
    "xianxing_xml": XianxingXmlParser,
    "xianxing_djb": XianxingDjbParser,
    "jinqi": JinqiParser,
    "qingqian": QingqianParser,
    "zaoqi": ZaoqiParser,
}


def get_parser(code: str) -> Parser:
    cls = REGISTRY.get(code)
    if cls is None:
        raise KeyError(f"未登记的解析器 {code}")
    return cls()
