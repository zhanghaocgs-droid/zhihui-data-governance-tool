"""样例回归测试的辅助函数。样例数据不入库，由环境变量 DGT_SAMPLE_ROOT 指定位置。"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from openpyxl import Workbook

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_ROOT = REPO.parent / "数据治理项目实施" / "数据示例" / "数据示例"

# 样例档在测试中假定的项目层级
SAMPLE_LEVELS = {"Edmaker2017": "二级项目", "dzwd001": "二级项目", "6933": "三级项目"}


def sample_root() -> Path | None:
    p = Path(os.environ.get("DGT_SAMPLE_ROOT", DEFAULT_SAMPLE_ROOT))
    return p if p.exists() else None


def fake_uuid(dh: str) -> str:
    return hashlib.md5(f"dgt-test-{dh}".encode()).hexdigest()


def _tiny_pdf(path: Path) -> None:
    import pymupdf
    d = pymupdf.open()
    d.new_page()
    d.save(path)
    d.close()


def _tiny_jpg(path: Path) -> None:
    from PIL import Image
    Image.new("RGB", (8, 8), "white").save(path, "JPEG")


def stage_edmaker2017(src_root: Path, dst_root: Path) -> Path:
    """Edmaker2017 样例的存档区和源文件区为空，按 XML 补造占位文件，验证完整落库链路。"""
    from dgt.adapt.parsers.xianxing_xml import load_xml
    src = src_root / "Edmaker2017"
    dst = dst_root / "Edmaker2017"
    shutil.copytree(src, dst)
    root = load_xml(dst / "管理性文件" / "地质资料文件目录.xml")
    arch = dst / "存档电子文件"
    srcd = dst / "源电子文件"
    for f in root.iter("CGWJXXList"):
        a = (f.findtext("CDWJMC") or "").strip()
        s = (f.findtext("WJMC") or "").strip()
        if a:
            if a.lower().endswith(".pdf"):
                _tiny_pdf(arch / a)
            elif a.lower().endswith((".jpg", ".jpeg")):
                _tiny_jpg(arch / a)
            else:
                (arch / a).write_bytes(b"placeholder")
        if s:
            if "." in s:
                (srcd / s).write_bytes(b"placeholder")
            else:
                (srcd / s).mkdir(exist_ok=True)
                (srcd / s / "placeholder.WT").write_bytes(b"placeholder")
    return dst


def write_projects(engine, path: Path) -> Path:
    """按已解析的案卷字段生成项目对照表，UUID 为固定算法生成的测试值。"""
    from dgt.core.db import loads
    wb = Workbook()
    ws = wb.active
    ws.append(["项目UUID", "项目名称", "项目编号", "项目层级", "年度"])
    for dh, level in SAMPLE_LEVELS.items():
        r = engine.db.one("SELECT fields FROM case_info WHERE dh=?", (dh,))
        if not r:
            continue
        case = loads(r["fields"], {})
        ws.append([fake_uuid(dh), case.get("XMMC") or case.get("BGTM"), case.get("XMBH", ""), level, ""])
    wb.save(path)
    return path
