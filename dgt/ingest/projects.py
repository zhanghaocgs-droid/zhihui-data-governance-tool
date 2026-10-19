"""GRP 项目对照表导入。支持 xlsx 和 csv，列名按 ruleset.yaml 的 project_columns 识别。"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import openpyxl

UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def normalize_uuid(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower().replace("-", "").replace("{", "").replace("}", "")
    return s if UUID_RE.match(s) else None


def normalize_level(value) -> str | None:
    s = str(value or "").strip()
    if s in ("二级项目", "二级", "2", "二"):
        return "二级项目"
    if s in ("三级项目", "三级", "3", "三"):
        return "三级项目"
    return None


def _read_rows(path: Path) -> list[list]:
    if path.suffix.lower() == ".csv":
        raw = path.read_bytes()
        from ..core.textio import decode_bytes
        text, _ = decode_bytes(raw)
        return list(csv.reader(text.splitlines()))
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def read_projects(path: Path | str, columns: dict) -> tuple[list[dict], list[str]]:
    """返回项目记录和问题说明。UUID 不合规或层级不明的行登记问题但不导入。"""
    rows = _read_rows(Path(path))
    problems: list[str] = []
    header_idx = None
    colmap: dict[str, int] = {}
    for i, r in enumerate(rows[:20]):
        names = [str(c).strip() if c is not None else "" for c in r]
        found = {}
        for field, aliases in columns.items():
            for a in aliases:
                if a in names:
                    found[field] = names.index(a)
                    break
        if "uuid" in found and "name" in found:
            header_idx, colmap = i, found
            break
    if header_idx is None:
        return [], ["未找到包含项目UUID和项目名称的表头行"]
    out = []
    for n, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def get(f):
            j = colmap.get(f)
            return r[j] if j is not None and j < len(r) else None
        if all(c in (None, "") for c in r):
            continue
        uuid = normalize_uuid(get("uuid"))
        if not uuid:
            problems.append(f"第{n}行 UUID 不是32位十六进制：{get('uuid')}")
            continue
        level = normalize_level(get("level"))
        if not level:
            problems.append(f"第{n}行 项目层级无法识别：{get('level')}")
        out.append({
            "uuid": uuid,
            "name": str(get("name") or "").strip(),
            "code": str(get("code") or "").strip(),
            "level": level,
            "year": str(get("year") or "").strip(),
            "source": Path(path).name,
        })
    return out, problems
