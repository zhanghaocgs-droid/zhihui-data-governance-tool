"""时期识别器。按 periods.yaml 的特征打分，得分最高且过阈值才定时期。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .parsers.base import ArchiveScan


@dataclass
class PeriodResult:
    code: str | None
    period: str | None
    label: str | None
    score: float
    supported: bool
    basis: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    needs_review: bool = False


def _check(scan: ArchiveScan, feat: dict) -> bool:
    t = feat["type"]
    path = feat.get("path", "")
    if t == "dir":
        return scan.exists_dir(path)
    if t == "no_dir":
        return not scan.exists_dir(path)
    if t == "file":
        if "*" in path:
            return bool(scan.glob(path))
        r = scan.resolve(path)
        return r is not None and scan.abspath(r).is_file()
    if t == "xml_node":
        r = scan.resolve(path)
        if r is None:
            return False
        try:
            with open(scan.abspath(r), "rb") as f:
                head = f.read(65536)
        except OSError:
            return False
        return f"<{feat['node']}>".encode() in head
    raise ValueError(f"未知特征类型 {t}")


def _describe(feat: dict, hit: bool) -> str:
    names = {"dir": "存在目录", "no_dir": "不存在目录", "file": "存在文件", "xml_node": "XML含节点"}
    what = feat.get("path", "")
    if feat["type"] == "xml_node":
        what = f"{what}#{feat['node']}"
    return f"{'√' if hit else '×'} {names[feat['type']]} {what}"


def identify(scan: ArchiveScan, rules) -> PeriodResult:
    threshold = rules.params.get("period_threshold", 0.6)
    best = None
    scores = {}
    details = {}
    for p in rules.periods["periods"]:
        feats = p.get("features") or []
        if not feats:
            continue
        score = 0.0
        basis = []
        dead = False
        for f in feats:
            hit = _check(scan, f)
            basis.append(_describe(f, hit))
            if hit:
                score += float(f.get("weight", 0))
            elif f.get("required"):
                dead = True
        score = 0.0 if dead else round(score, 3)
        scores[p["code"]] = score
        details[p["code"]] = basis
        if best is None or score > scores[best["code"]]:
            best = p
    if not best or scores[best["code"]] < threshold:
        return PeriodResult(None, None, None, scores.get(best["code"], 0) if best else 0, False,
                            basis=["未达到任一时期的识别阈值，需人工判别"], scores=scores, needs_review=True)
    # 并列最高视为多时期命中，进人工
    top = scores[best["code"]]
    ties = [c for c, s in scores.items() if s == top]
    return PeriodResult(best["code"], best["period"], best["label"], top, bool(best.get("supported")),
                        basis=details[best["code"]], scores=scores, needs_review=len(ties) > 1)
