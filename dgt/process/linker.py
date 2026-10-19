"""档案与 GRP 项目关联。结果为 确定匹配、疑似匹配、未匹配。"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

CONFIRMED, SUSPECT, UNMATCHED = "确定匹配", "疑似匹配", "未匹配"


def norm_name(s: str | None) -> str:
    s = (s or "").replace("∶", ":").replace("：", ":")
    return re.sub(r"[\s\-－—_()（）\[\]［］【】〔〕《》<>,，.。、;；:·•“”\"']", "", s)


def norm_code(s: str | None) -> str:
    s = (s or "").replace("－", "-").replace("［", "[").replace("］", "]").replace("〔", "[").replace("〕", "]")
    return re.sub(r"\s", "", s).upper()


@dataclass
class LinkResult:
    status: str
    project: dict | None = None
    basis: str = ""
    candidates: list = field(default_factory=list)


class ProjectLinker:
    def __init__(self, projects: list[dict], fuzzy_threshold: float = 0.85):
        self.projects = projects
        self.threshold = fuzzy_threshold
        self.by_code = {}
        self.by_name = {}
        for p in projects:
            if p.get("code"):
                self.by_code.setdefault(norm_code(p["code"]), []).append(p)
            if p.get("name"):
                self.by_name.setdefault(norm_name(p["name"]), []).append(p)

    def link(self, case: dict) -> LinkResult:
        if not self.projects:
            return LinkResult(UNMATCHED, basis="尚未导入 GRP 项目对照表")
        codes = [case.get(k) for k in ("XMBH", "XMRWSBH") if case.get(k)]
        names = [case.get(k) for k in ("XMMC", "BGTM", "TM") if case.get(k)]
        for c in codes:
            hit = self.by_code.get(norm_code(c))
            if hit and len(hit) == 1:
                return LinkResult(CONFIRMED, hit[0], f"项目编号 {c} 一致")
            if hit:
                return LinkResult(SUSPECT, hit[0], f"项目编号 {c} 对应多个项目",
                                  [{"uuid": h["uuid"], "name": h["name"], "score": 1.0} for h in hit])
        for n in names:
            hit = self.by_name.get(norm_name(n))
            if hit and len(hit) == 1:
                return LinkResult(CONFIRMED, hit[0], "项目名称一致")
        cands = []
        for n in names:
            nn = norm_name(n)
            for p in self.projects:
                pn = norm_name(p.get("name"))
                if not pn or not nn:
                    continue
                score = difflib.SequenceMatcher(None, nn, pn).ratio()
                if pn in nn or nn in pn:
                    score = max(score, 0.9)
                if score >= self.threshold:
                    cands.append({"uuid": p["uuid"], "name": p["name"], "score": round(score, 3)})
        if cands:
            cands.sort(key=lambda c: -c["score"])
            best = next(p for p in self.projects if p["uuid"] == cands[0]["uuid"])
            return LinkResult(SUSPECT, best, f"项目名称相似度 {cands[0]['score']}", cands[:5])
        return LinkResult(UNMATCHED, basis="项目编号和名称均未命中对照表")
