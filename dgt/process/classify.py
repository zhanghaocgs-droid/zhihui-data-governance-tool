"""阶段分类引擎。按 classify.yaml 打分，输出阶段、子目录、文件类型、依据和置信度。"""
from __future__ import annotations

from dataclasses import dataclass, field

STAGE4 = "第4阶段"

# 动作：accept 直接落库；review 进人工复核；associate 关联保存；raw 原始区保留
ACCEPT, REVIEW, ASSOCIATE, RAW = "accept", "review", "associate", "raw"


@dataclass
class ClassifyResult:
    action: str
    stage: str | None = None
    subdir: str | None = None
    file_type: str | None = None
    confidence: float = 0.0
    rule_id: str | None = None
    evidence: str = ""
    reason: str = ""
    candidates: list = field(default_factory=list)


def resolve_stage4(rules, stage: str | None, file_type, level: str | None):
    """把规则中的 第4阶段 按项目层级落为 实施方案 或 年度设计。层级未知时返回两者并列写法。"""
    if stage != STAGE4:
        return stage, file_type
    if level:
        name = rules.stage4_name(level)
        ft = file_type.get(level) if isinstance(file_type, dict) else file_type
        return name, ft
    names = "或".join(rules.stage4_name(lv) for lv in ("二级项目", "三级项目"))
    ft = "或".join(file_type.values()) if isinstance(file_type, dict) else file_type
    return names, ft


class Classifier:
    def __init__(self, rules):
        self.rules = rules
        self.items = rules.classify.get("rules", [])
        p = rules.params
        self.accept = float(p.get("accept_threshold", 0.7))
        self.margin = float(p.get("conflict_margin", 0.1))
        self.components = set(p.get("component_categories", []))
        self.unmatched = p.get("unmatched_action", {})
        self.attach_mode = p.get("attachments_mode", "associate")

    def _match(self, title: str, category: str) -> list[dict]:
        hits = []
        for r in self.items:
            cats = r.get("categories")
            if cats and category not in cats:
                continue
            kws = r.get("keywords") or []
            matched = [k for k in kws if k == "*" or k in title]
            if not matched:
                continue
            ex = [e for e in (r.get("exclude") or []) if e in title]
            if ex:
                continue
            hits.append({"rule": r, "matched": matched, "score": float(r.get("weight", 0))})
        hits.sort(key=lambda h: (-h["score"], h["rule"]["id"]))
        return hits

    def classify(self, doc, level: str | None = None) -> ClassifyResult:
        if doc.kind == "raw":
            return ClassifyResult(RAW, reason="原始资料，只登记不落库")
        cat = doc.category
        if cat in self.components:
            if self.attach_mode == "archive":
                stage, ft = "成果评审", "成果报告"
                return ClassifyResult(ACCEPT, stage, "成果评审", ft, 1.0, "COMPONENT",
                                      f"类别{cat}为成果组成部分，随成果报告落库")
            return ClassifyResult(ASSOCIATE, reason=f"类别{cat}为成果组成部分，关联保存只登记")
        title = doc.title or ""
        hits = self._match(title, cat) if title else []
        cands = []
        for h in hits[:5]:
            st, ft = resolve_stage4(self.rules, h["rule"]["stage"], h["rule"]["file_type"], level)
            cands.append({"rule": h["rule"]["id"], "stage": st, "subdir": h["rule"].get("subdir"),
                          "file_type": ft, "score": h["score"],
                          "matched": [m for m in h["matched"] if m != "*"]})
        if not hits:
            act = self.unmatched.get(cat, self.unmatched.get("default", "review"))
            why = "题名缺失" if not title else "未命中任何分类规则"
            if act == "associate":
                return ClassifyResult(ASSOCIATE, reason=f"{why}，类别{cat}按成果组成部分关联保存")
            return ClassifyResult(REVIEW, reason=why)
        best = cands[0]
        evidence = f"规则{best['rule']} 命中“{'、'.join(best['matched']) or '类别默认'}”"
        res = ClassifyResult(REVIEW, best["stage"], best["subdir"], best["file_type"], best["score"],
                             best["rule"], evidence, candidates=cands)
        # 次高分来自同一结论的规则不算冲突
        rivals = [c for c in cands[1:] if (c["stage"], c["subdir"], c["file_type"]) !=
                  (best["stage"], best["subdir"], best["file_type"])]
        if best["score"] < self.accept:
            res.reason = f"置信度{best['score']:.2f}低于阈值{self.accept:.2f}"
        elif rivals and best["score"] - rivals[0]["score"] < self.margin:
            res.reason = f"与规则{rivals[0]['rule']}结论冲突"
        elif level is None and best["stage"] and "或" in best["stage"]:
            res.action = ACCEPT
            res.reason = "项目层级未知，第4阶段待关联后确定"
        else:
            res.action = ACCEPT
        return res
