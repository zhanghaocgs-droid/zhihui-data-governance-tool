"""落库说明第六章校验：目录正则、文件名正则、词表、版本连续性、唯一性、长度。"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Failure:
    check: str
    target: str
    message: str


class LandingValidator:
    def __init__(self, rules):
        self.rules = rules
        v = rules.validation
        uuid = v["uuid_pattern"]
        self.dir_res = {lv: re.compile(p.replace("{UUID}", uuid)) for lv, p in v["dir_patterns"].items()}
        self.name_res = {k: re.compile(p) for k, p in v["name_patterns"].items()}
        self.checks = v.get("checks", {})
        self.max_name = rules.naming.get("max_name_len", 120)
        self.max_path = rules.naming.get("max_path_len", 255)
        vw = rules.stages["version_words"]
        self.text_versions = set(vw["text"])
        self.num_version = re.compile("^" + vw["numeric_pattern"] + "$")

    @staticmethod
    def split(name: str) -> tuple[str, str, str, str, str] | None:
        """拆出 期间标识、项目名称、文件类型、版本、扩展名。三段式期间标识为空。"""
        stem, _, ext = name.rpartition(".")
        parts = stem.split("-")
        if len(parts) == 4:
            return parts[0], parts[1], parts[2], parts[3], ext
        if len(parts) == 3:
            return "", parts[0], parts[1], parts[2], ext
        return None

    def check_one(self, rel_dir: str, name: str, stage: str, subdir: str | None,
                  level: str, abs_len: int | None = None) -> list[Failure]:
        """rel_dir 形如 /二级项目/{uuid}/成果评审/成果评审。"""
        out = []
        target = f"{rel_dir}/{name}"
        if self.checks.get("dir_regex", True):
            rx = self.dir_res.get(level)
            if not rx or not rx.match(rel_dir):
                out.append(Failure("目录路径正则", target, f"目录 {rel_dir} 不符合{level}目录规则"))
        ptype = self.rules.period_type(stage)
        if self.checks.get("name_regex", True):
            if not self.name_res[ptype].match(name):
                out.append(Failure("文件名正则", target, f"文件名不符合{ptype}阶段命名规则"))
        if self.checks.get("vocabulary", True):
            parts = self.split(name)
            if not parts:
                out.append(Failure("受控词表", target, "无法拆分为四段式"))
            else:
                _, proj, ftype, ver, _ = parts
                vocab = self.rules.file_types(stage, subdir)
                if ftype not in vocab:
                    out.append(Failure("受控词表", target, f"文件类型 {ftype} 不在 {stage}{'/' + subdir if subdir else ''} 词表"))
                if ver not in self.text_versions and not self.num_version.match(ver):
                    out.append(Failure("受控词表", target, f"版本 {ver} 不是受控版本词"))
        if self.checks.get("length", True):
            if len(name) > self.max_name:
                out.append(Failure("长度", target, f"文件名 {len(name)} 字符，超过 {self.max_name}"))
            if abs_len is not None and abs_len > self.max_path:
                out.append(Failure("长度", target, f"全路径 {abs_len} 字符，超过 {self.max_path}"))
        return out

    def check_group(self, items: list[dict]) -> list[Failure]:
        """跨文件校验：版本连续性、唯一性、版本体系统一。items 含 project_uuid、rel_dir、name。"""
        out = []
        seen = defaultdict(list)
        series = defaultdict(list)
        systems = defaultdict(set)
        for it in items:
            parts = self.split(it["name"])
            if not parts:
                continue
            period, proj, ftype, ver, _ = parts
            seen[(it["rel_dir"], period, proj, ftype, ver)].append(it["name"])
            m = self.num_version.match(ver)
            if m:
                series[(it["rel_dir"], period, ftype)].append(int(ver[1:]))
                systems[it["project_uuid"]].add("数值型")
            else:
                systems[it["project_uuid"]].add("文字型")
        if self.checks.get("uniqueness", True):
            for k, names in seen.items():
                if len(names) > 1:
                    out.append(Failure("唯一性", f"{k[0]}/{names[0]}", f"同一阶段目录内重复 {len(names)} 次"))
        if self.checks.get("version_continuity", True):
            for k, nums in series.items():
                s = sorted(set(nums))
                if s != list(range(1, len(s) + 1)):
                    out.append(Failure("版本连续性", f"{k[0]} {k[1]} {k[2]}", f"版本序列 {s} 不从 v1 起连续"))
            for uuid, sys_ in systems.items():
                if len(sys_) > 1:
                    out.append(Failure("版本连续性", uuid, "同一项目混用数值型和文字型版本"))
        return out
