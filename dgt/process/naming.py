"""四段式命名。{期间标识}-{项目名称}-{文件类型}-{版本名称}.{扩展名}"""
from __future__ import annotations

import re


def clean_project_name(name: str, cfg: dict) -> str:
    pn = cfg["project_name"]
    s = name or ""
    for a, b in (pn.get("replace") or {}).items():
        s = s.replace(a, b)
    remove = set(pn.get("remove", ""))
    s = "".join(ch for ch in s if ch not in remove)
    # 兜底：Windows 文件名非法字符与全角标点区段
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)
    s = "".join(ch for ch in s if not (0xFF01 <= ord(ch) <= 0xFF0F or 0xFF1A <= ord(ch) <= 0xFF20
                                      or 0x3000 <= ord(ch) <= 0x3003 or 0x3008 <= ord(ch) <= 0x3011))
    return s


def extract_period(period_type: str, texts: list[str], cfg: dict) -> tuple[str | None, str]:
    """返回期间标识和来源说明。一次性阶段返回空串。"""
    if period_type == "once":
        return "", "一次性阶段无期间标识"
    for src in cfg.get("period_sources", {}).get(period_type, []):
        pat = re.compile(src["pattern"])
        for t in texts:
            if not t:
                continue
            m = pat.search(t)
            if not m:
                continue
            if period_type == "monthly":
                y, mo = m.group(1), int(m.group(2))
                if 1 <= mo <= 12:
                    return f"{y}年{mo}月", f"题名“{m.group(0)}”"
            else:
                y = m.group(1)
                if 1949 <= int(y) <= 2100:
                    return y, f"题名“{m.group(0)}”"
    return None, "题名中取不到业务年度"


def build_name(period_id: str, project: str, file_type: str, version: str, ext: str) -> str:
    parts = [p for p in (period_id, project, file_type, version) if p]
    return "-".join(parts) + "." + ext.lower().lstrip(".")


def ext_of(path: str | None) -> str:
    if not path or "." not in path.rsplit("/", 1)[-1]:
        return ""
    return path.rsplit(".", 1)[-1].lower()
