"""规则配置加载与版本登记。"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def default_rules_dir() -> Path:
    """规则目录。打包后在程序目录下，开发时在仓库根目录下。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "rules"
    return Path(__file__).resolve().parents[2] / "rules"


@dataclass
class RuleSet:
    """一套已加载的规则，含各文件内容、版本号和摘要值。"""

    root: Path
    ruleset: dict
    periods: dict
    stages: dict
    classify: dict
    naming: dict
    excel_header: dict
    secrecy: dict
    validation: dict
    digests: dict = field(default_factory=dict)

    @property
    def version(self) -> str:
        return str(self.ruleset.get("version", "未定义"))

    @property
    def params(self) -> dict:
        return self.ruleset.get("params", {})

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for k in sorted(self.digests):
            h.update(f"{k}:{self.digests[k]}".encode())
        return h.hexdigest()[:16]

    def file_versions(self) -> dict:
        return {
            "periods": self.periods.get("version"),
            "stages": self.stages.get("version"),
            "classify": self.classify.get("version"),
            "naming": self.naming.get("version"),
            "excel_header": self.excel_header.get("version"),
            "secrecy": self.secrecy.get("version"),
            "validation": self.validation.get("version"),
        }

    # ---- 便捷访问 ----
    def stage_defs(self) -> list[dict]:
        return self.stages["stages"]

    def stage_def(self, name: str) -> dict | None:
        for s in self.stage_defs():
            if s["name"] == name:
                return s
        return None

    def stage4_name(self, level: str | None) -> str | None:
        if not level:
            return None
        lv = self.stages["levels"].get(level)
        return lv["stage4"] if lv else None

    def stage_names(self, level: str | None = None) -> list[str]:
        """按顺序列出阶段名。给定层级时第 4 阶段只保留对应名称。"""
        out = []
        for s in sorted(self.stage_defs(), key=lambda x: x["order"]):
            if level and s.get("level") and s["level"] != level:
                continue
            out.append(s["name"])
        return out

    def file_types(self, stage: str, subdir: str | None = None) -> dict:
        s = self.stage_def(stage)
        if not s:
            return {}
        if "subdirs" in s:
            if subdir:
                return dict(s["subdirs"].get(subdir, {}))
            merged = {}
            for v in s["subdirs"].values():
                merged.update(v)
            return merged
        return dict(s.get("file_types", {}))

    def subdirs(self, stage: str) -> list[str]:
        s = self.stage_def(stage)
        return list(s.get("subdirs", {}).keys()) if s else []

    def period_type(self, stage: str) -> str:
        s = self.stage_def(stage)
        return s["period_type"] if s else "once"


def _load_yaml(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return yaml.safe_load(raw.decode("utf-8")) or {}, hashlib.sha256(raw).hexdigest()[:16]


def load_rules(root: Path | str | None = None) -> RuleSet:
    root = Path(root) if root else default_rules_dir()
    main, main_digest = _load_yaml(root / "ruleset.yaml")
    parts = {}
    digests = {"ruleset.yaml": main_digest}
    for name in main.get("files", []):
        data, dg = _load_yaml(root / name)
        parts[Path(name).stem] = data
        digests[name] = dg
    return RuleSet(root=root, ruleset=main, digests=digests, **parts)
