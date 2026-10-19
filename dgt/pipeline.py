"""治理引擎。按档执行八步：扫描对账、时期识别、目录解析、项目关联、阶段分类、命名转换、输出校验、涉密标记。"""
from __future__ import annotations

import os
import traceback
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import __version__
from .adapt.period import identify
from .adapt.parsers import get_parser
from .adapt.parsers.base import ArchiveScan, CATEGORY_NAMES, Document
from .core.config import load_rules
from .core.db import Database, dumps, loads, now
from .core.fsguard import WriteGuard
from .ingest.handover import dh_key, read_handover, reconcile
from .ingest.projects import read_projects
from .ingest.scan import discover_archives, scan_archive
from .process.classify import ACCEPT, ASSOCIATE, RAW, REVIEW, Classifier
from .process.convert import ConvertError, Converter
from .process.linker import CONFIRMED, SUSPECT, UNMATCHED, ProjectLinker
from .process.naming import build_name, clean_project_name, ext_of, extract_period
from .process.secrecy import SecrecyJudge
from .output.validate import LandingValidator

# 去向状态
LANDED, ASSOCIATED, RAW_KEPT, PENDING = "已落库", "关联保存", "原始区保留", "待处理"
# 处理方式
M_COPY, M_DOC2PDF, M_NONE = "直接复制", "DOC转PDF", "只登记"

RAW_DIR = "原始资料电子文件"


class Engine:
    def __init__(self, workspace: Path | str, rules_dir: Path | str | None = None, operator: str = ""):
        self.ws = Path(workspace)
        self.ws.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.ws / "dgt.sqlite")
        self.operator = operator
        self.out_root = self.ws / "成果区"
        self.full_root = self.out_root / "全补"
        self.pending_root = self.out_root / "待匹配"
        self.tmp_root = self.out_root / "_临时"
        self.guard = WriteGuard(self.out_root)
        self.out_root.mkdir(parents=True, exist_ok=True)
        for r in loads(self.db.get_meta("data_roots"), []):
            self.guard.add_source(r)
        self.reload_rules(rules_dir)

    # ------------------------------------------------------------------ 规则
    def reload_rules(self, rules_dir=None) -> None:
        self.rules = load_rules(rules_dir or self.db.get_meta("rules_dir") or None)
        if rules_dir:
            self.db.set_meta("rules_dir", str(rules_dir))
        self.classifier = Classifier(self.rules)
        self.validator = LandingValidator(self.rules)
        self.secrecy = SecrecyJudge(self.rules.secrecy)
        p = self.rules.params
        self.converter = Converter(p.get("converters", []), p.get("libreoffice_path", ""), self.tmp_root)
        if not self.db.one("SELECT 1 FROM rule_version WHERE digest=?", (self.rules.digest,)):
            self.db.insert("rule_version", {"version": self.rules.version, "digest": self.rules.digest,
                                            "files": dumps(self.rules.file_versions()), "loaded_at": now(),
                                            "review_note": None})
            self.db.log("规则", f"登记规则版本 {self.rules.version}（摘要 {self.rules.digest}）")

    @property
    def rule_tag(self) -> str:
        return f"{self.rules.version}#{self.rules.digest[:8]}"

    def set_converters(self, order: list[str]) -> None:
        """测试或无办公软件环境下可关闭转换。"""
        self.converter = Converter(order, self.rules.params.get("libreoffice_path", ""), self.tmp_root)

    # ------------------------------------------------------------------ 导入
    def import_handover(self, path: Path | str) -> int:
        rows = read_handover(path)
        c = self.db.connect()
        c.execute("DELETE FROM handover")
        for r in rows:
            c.execute("INSERT OR REPLACE INTO handover VALUES (?,?,?,?,?,?,?,?)",
                      (r.dh, r.sheet, r.seq, dumps({"成果": r.counts, "原始": r.raw_counts}),
                       r.file_count, r.dir_count, r.data_size, r.data_bytes))
        c.commit()
        self.db.set_meta("handover_file", str(path))
        self.db.log("导入", f"导入交接清单 {Path(path).name}，共 {len(rows)} 档", operator=self.operator)
        return len(rows)

    def import_projects(self, path: Path | str) -> tuple[int, list[str]]:
        rows, problems = read_projects(path, self.rules.params.get("project_columns", {}))
        for r in rows:
            self.db.upsert("project", r)
        self.db.log("导入", f"导入项目对照表 {Path(path).name}，{len(rows)} 个项目，{len(problems)} 条问题",
                    operator=self.operator)
        return len(rows), problems

    def projects(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM project")]

    # ------------------------------------------------------------------ 第1、2步 扫描对账与时期识别
    def _handover_index(self) -> dict:
        from .ingest.handover import HandoverRow
        idx = {}
        for r in self.db.query("SELECT * FROM handover"):
            cnt = loads(r["counts"], {})
            idx[dh_key(r["dh"])] = HandoverRow(dh=r["dh"], sheet=r["sheet"], seq=r["seq"],
                                               counts=cnt.get("成果", {}), raw_counts=cnt.get("原始", {}),
                                               file_count=r["file_count"], dir_count=r["dir_count"],
                                               data_size=r["data_size"], data_bytes=r["data_bytes"])
        return idx

    def scan(self, data_root: Path | str, progress: Callable[[str, int], None] | None = None,
             should_stop: Callable[[], bool] | None = None) -> int:
        data_root = Path(data_root).resolve()
        roots = loads(self.db.get_meta("data_roots"), [])
        if str(data_root) not in roots:
            roots.append(str(data_root))
            self.db.set_meta("data_roots", dumps(roots))
        self.guard.add_source(data_root)
        hidx = self._handover_index()
        seen = set()
        n = 0
        for path in discover_archives(data_root):
            if should_stop and should_stop():
                break
            dh = path.name
            if progress:
                progress(dh, n)
            self.guard.add_source(path)
            sc = scan_archive(path, dh)
            pr = identify(sc, self.rules)
            row = hidx.get(dh_key(dh))
            status, diffs = reconcile(sc, row)
            seen.add(dh_key(dh))
            basis = list(pr.basis)
            if pr.needs_review:
                basis.append("时期识别存在并列或未达阈值，需人工判别")
            old = self.db.one("SELECT proc_status FROM archive WHERE dh=?", (dh,))
            self.db.upsert("archive", {
                "dh": dh, "root": str(path), "period": pr.period or "待人工判别",
                "period_code": pr.code, "period_score": pr.score,
                "period_basis": dumps({"basis": basis, "scores": pr.scores, "label": pr.label,
                                       "supported": pr.supported}),
                "catalog_entries": dumps(sc.catalog_entries), "file_count": sc.file_count,
                "dir_count": sc.dir_count, "total_bytes": sc.total_bytes,
                "category_counts": dumps({"成果": sc.category_counts, "原始": sc.raw_counts,
                                          "顶层": sc.top_entries}),
                "ext_counts": dumps(sc.ext_counts), "unreadable": sc.unreadable,
                "recon_status": status, "recon_detail": dumps(diffs),
                "proc_status": old["proc_status"] if old else "未处理",
                "proc_rule_version": None, "proc_message": None,
                "scanned_at": now(), "processed_at": None, "batch_id": None})
            self._clear_issues(dh, kinds=("对账差异", "时期识别"))
            if status != "一致":
                self._issue(dh, "对账差异", f"{status}：{'；'.join(diffs)}", "核实后与甲方确认")
            if pr.needs_review or not pr.code:
                self._issue(dh, "时期识别", "时期识别不确定", "人工判别时期")
            self.db.log("盘点", f"{dh} 扫描完成：{sc.file_count} 个文件，时期 {pr.period or '待判别'}，对账 {status}",
                        dh=dh, operator=self.operator)
            n += 1
        # 清单中有、目录中没有的档
        for key, row in hidx.items():
            if key in seen:
                continue
            exists = self.db.one("SELECT dh FROM archive WHERE dh=? OR dh=?", (row.dh, key))
            if exists:
                continue
            self.db.upsert("archive", {"dh": row.dh, "root": None, "period": "未知", "period_code": None,
                                       "recon_status": "目录缺失",
                                       "recon_detail": dumps(["交接清单有该档，数据目录中未找到文件夹"]),
                                       "proc_status": "未处理", "scanned_at": now()})
        return n

    # ------------------------------------------------------------------ 问题与复核
    def _issue(self, dh, kind, desc, suggestion="", auto=1) -> None:
        self.db.insert("issue", {"dh": dh, "kind": kind, "description": desc, "suggestion": suggestion,
                                 "owner": None, "status": "未关闭", "created_at": now(), "closed_at": None,
                                 "auto": auto})

    def _clear_issues(self, dh, kinds=None) -> None:
        if kinds:
            q = ",".join("?" * len(kinds))
            self.db.execute(f"DELETE FROM issue WHERE dh=? AND auto=1 AND kind IN ({q})", (dh, *kinds))
        else:
            self.db.execute("DELETE FROM issue WHERE dh=? AND auto=1 AND kind NOT IN ('对账差异','时期识别')",
                            (dh,))

    def decisions(self, dh: str) -> dict:
        out = {}
        for r in self.db.query("SELECT * FROM decision WHERE dh=?", (dh,)):
            out[(r["src_rel"], r["kind"])] = dict(r)
        return out

    # ------------------------------------------------------------------ 第3至8步 按档治理
    def process_archive(self, dh: str, batch_id: int | None = None) -> dict:
        arc = self.db.one("SELECT * FROM archive WHERE dh=?", (dh,))
        if not arc or not arc["root"]:
            raise RuntimeError(f"档 {dh} 未盘点或目录缺失")
        info = loads(arc["period_basis"], {})
        if not arc["period_code"] or not info.get("supported"):
            msg = f"{arc['period']} 时期暂不支持，待后续版本"
            self.db.execute("UPDATE archive SET proc_status=?, proc_message=? WHERE dh=?", ("暂不支持", msg, dh))
            return {"dh": dh, "status": "暂不支持"}
        root = Path(arc["root"])
        self.guard.add_source(root)
        scan = ArchiveScan(dh=dh, root=root)
        self._purge_outputs(dh)
        self.db.log("治理", f"{dh} 开始治理，规则 {self.rule_tag}", dh=dh, batch_id=batch_id,
                    operator=self.operator)

        # 第3步 目录解析
        parsed = get_parser(arc["period_code"]).parse(scan)
        self.db.upsert("case_info", {"dh": dh, "fields": dumps(parsed.case), "source": parsed.case_source})
        doc_ids = {}
        for i, d in enumerate(parsed.documents, start=1):
            doc_ids[id(d)] = self.db.insert("document", {
                "dh": dh, "seq": i, "kind": d.kind, "category": d.category, "volume": d.volume,
                "item": d.item, "title": d.title, "title_source": d.title_source,
                "archive_rel": d.archive_rel, "source_rel": d.source_rel,
                "resources": dumps(d.resources), "secrecy": d.secrecy, "stage_ref": d.stage_ref,
                "extra": dumps({**d.extra, "declared_archive": d.declared_archive,
                                "declared_source": d.declared_source})})
        for note in parsed.notes:
            self.db.log("解析", note, dh=dh, batch_id=batch_id)

        # 第4步 项目关联
        decisions = self.decisions(dh)
        link = self._link(dh, parsed.case, decisions)
        project = link["project"]
        level = project.get("level") if project else None
        pname_raw = (project or {}).get("name") or ""
        pname_src = "GRP项目对照表" if pname_raw else ""
        if not pname_raw:
            for k in ("XMMC", "BGTM", "TM"):
                if parsed.case.get(k):
                    pname_raw, pname_src = parsed.case[k], f"目录字段{k}"
                    break
        pname = clean_project_name(pname_raw, self.rules.naming)
        if len(pname) > self.rules.naming["project_name"].get("max_chars_warn", 30):
            self._issue(dh, "项目名称过长", f"项目名称 {len(pname)} 字，超过建议长度", "确认是否使用简称")

        # 第5、6步 分类与命名规划
        plans = []
        for d in parsed.documents:
            plans.append(self._plan_document(dh, d, doc_ids[id(d)], level, pname, decisions))
        self._assign_versions(plans, decisions)

        # 第7、8步 输出、校验、涉密标记、映射
        ctx = {"dh": dh, "batch_id": batch_id, "period": arc["period"], "link": link, "level": level,
               "pname_src": pname_src, "case": parsed.case}
        referenced_files, referenced_dirs = set(), set()
        missing = []
        for p in plans:
            self._execute_plan(p, ctx, referenced_files, referenced_dirs, missing)
        self._register_unreferenced(scan, ctx, referenced_files, referenced_dirs)
        if missing:
            self._issue(dh, "文件缺失", f"{len(missing)} 条目录记录在档内找不到对应文件，例如 {'、'.join(missing[:5])}",
                        "向全国馆核实")

        pending_reviews = self.db.scalar("SELECT COUNT(*) FROM review WHERE dh=? AND status!='已确认'", (dh,), 0)
        landed = self.db.scalar("SELECT COUNT(*) FROM mapping WHERE dh=? AND status=?", (dh, LANDED), 0)
        status = "待复核" if pending_reviews else "已完成"
        if link["status"] == UNMATCHED:
            status = "待项目关联"
        self.db.execute("UPDATE archive SET proc_status=?, proc_rule_version=?, proc_message=?, processed_at=?, "
                        "batch_id=? WHERE dh=?",
                        (status, self.rule_tag, f"落库{landed}件，待复核{pending_reviews}件，项目{link['status']}",
                         now(), batch_id, dh))
        self.db.log("治理", f"{dh} 治理结束：{status}，落库 {landed} 件，待复核 {pending_reviews} 件",
                    dh=dh, batch_id=batch_id, operator=self.operator)
        return {"dh": dh, "status": status, "landed": landed, "reviews": pending_reviews}

    def _purge_outputs(self, dh: str) -> None:
        """重跑前清除该档旧结果。人工结论保存在 decision 表，不受影响。"""
        for r in self.db.query("SELECT target_rel FROM mapping WHERE dh=? AND status=?", (dh, LANDED)):
            if r["target_rel"]:
                self.guard.remove_file(self.full_root / r["target_rel"])
        if (self.pending_root / dh).exists():
            self.guard.remove_tree(self.pending_root / dh)
        c = self.db.connect()
        c.execute("DELETE FROM mapping WHERE dh=?", (dh,))
        c.execute("DELETE FROM review WHERE dh=?", (dh,))
        c.execute("DELETE FROM document WHERE dh=?", (dh,))
        c.execute("DELETE FROM archive_project WHERE dh=? AND manual=0", (dh,))
        c.commit()
        self._clear_issues(dh)
        self._prune_empty_dirs(self.full_root)

    def _prune_empty_dirs(self, root: Path) -> None:
        if not root.exists():
            return
        for d, dirs, files in os.walk(root, topdown=False):
            p = Path(d)
            if p != root and not os.listdir(p):
                self.guard.check(p)
                p.rmdir()

    def _link(self, dh: str, case: dict, decisions: dict) -> dict:
        manual = self.db.one("SELECT * FROM archive_project WHERE dh=? AND manual=1", (dh,))
        if manual:
            p = self.db.one("SELECT * FROM project WHERE uuid=?", (manual["project_uuid"],))
            if p:
                return {"status": CONFIRMED, "project": dict(p), "basis": f"人工确认：{manual['basis'] or ''}",
                        "confirmer": manual["confirmer"]}
        linker = ProjectLinker(self.projects(), self.rules.params.get("project_fuzzy_threshold", 0.85))
        res = linker.link(case)
        project = res.project if res.status == CONFIRMED else None
        if res.project:
            self.db.upsert("archive_project", {"dh": dh, "project_uuid": res.project["uuid"],
                                               "match_status": res.status, "basis": res.basis,
                                               "confirmer": None, "confirmed_at": None, "manual": 0})
        if res.status == SUSPECT:
            self.db.insert("review", {"dh": dh, "src_rel": "", "mapping_id": None, "kind": "project",
                                      "reason": f"疑似匹配：{res.basis}",
                                      "suggestion": dumps({"uuid": res.project["uuid"], "name": res.project["name"]}),
                                      "candidates": dumps(res.candidates), "status": "待复核",
                                      "created_at": now()})
        if res.status == UNMATCHED:
            self._issue(dh, "项目未匹配", res.basis, "导入或补充 GRP 项目对照表后重跑")
        return {"status": res.status if res.status != SUSPECT else SUSPECT, "project": project,
                "basis": res.basis, "confirmer": None}

    def _plan_document(self, dh, d: Document, doc_id, level, pname, decisions) -> dict:
        key = d.key
        dec = decisions.get((key, "classify"))
        if dec and dec["status"] == "已确认":
            r = loads(dec["result"], {})
            act = r.get("action", ACCEPT)
            cls = {"action": act, "stage": r.get("stage"), "subdir": r.get("subdir") or None,
                   "file_type": r.get("file_type"), "confidence": 1.0, "rule_id": "人工确认",
                   "evidence": f"人工确认：{dec['basis'] or ''}", "reason": "", "candidates": [],
                   "period_id": r.get("period_id"), "version": r.get("version"),
                   "confirmer": dec["confirmer"], "reviewer": dec["reviewer"]}
        else:
            c = self.classifier.classify(d, level)
            cls = {**asdict(c), "period_id": None, "version": None, "confirmer": None, "reviewer": None}
        plan = {"doc": d, "doc_id": doc_id, "key": key, **cls, "pname": pname, "level": level,
                "period_note": ""}
        if plan["action"] == ACCEPT and plan["stage"]:
            if plan["period_id"] is None:
                ptype = self.rules.period_type(plan["stage"]) if self.rules.stage_def(plan["stage"]) else "annual"
                pid, note = extract_period(ptype, [d.title or ""], self.rules.naming)
                plan["period_id"], plan["period_note"] = pid, note
                if pid is None:
                    plan["action"] = REVIEW
                    plan["reason"] = f"期间标识缺失：{note}"
        return plan

    def _assign_versions(self, plans: list[dict], decisions) -> None:
        """同一阶段、期间、文件类型出现多份时，本档改用数值型版本。"""
        groups = defaultdict(list)
        for p in plans:
            # 格式不能落库的文献只登记，不参与版本编号
            if p["action"] == ACCEPT and p["stage"] and self._choose_source(p["doc"])[1] != M_NONE:
                groups[(p["stage"], p["subdir"], p["period_id"], p["file_type"])].append(p)
        numeric = any(len(v) > 1 for v in groups.values())
        for k, items in groups.items():
            items.sort(key=lambda p: (p["doc"].category, p["doc"].volume or "", p["doc"].item or "", p["key"]))
            for i, p in enumerate(items, start=1):
                if p.get("version"):
                    continue
                if numeric:
                    p["version"] = f"v{i}"
                    if len(items) > 1:
                        p["evidence"] += f"；同类{len(items)}份按序号定版本"
                else:
                    stage = p["stage"]
                    vocab = self.rules.file_types(stage, p["subdir"]) if self.rules.stage_def(stage) else {}
                    p["version"] = vocab.get(p["file_type"], "定稿")

    def _choose_source(self, d: Document) -> tuple[str | None, str, str]:
        """选落库主文件。返回 (相对路径, 处理方式, 说明)。"""
        conv = set(self.rules.naming.get("convert_to_pdf", []))
        allowed = set(self.rules.naming.get("allowed_ext", []))
        a, s = d.archive_rel, d.source_rel
        prefer = self.rules.params.get("prefer_archive_pdf", True)
        ae, se = ext_of(a), ext_of(s)
        if a and ae in allowed and ae not in conv and prefer:
            return a, M_COPY, "存档文件直接复制"
        if a and ae in conv:
            return a, M_DOC2PDF, "存档文件为 Word，转 PDF"
        if s and se in conv:
            return s, M_DOC2PDF, "无可用存档 PDF，源文件转 PDF"
        if a and ae in allowed:
            return a, M_COPY, "存档文件直接复制"
        if s and se in allowed:
            return s, M_COPY, "源文件直接复制"
        return a or s, M_NONE, "格式不在落库扩展名范围，关联保存"

    def _target_dir(self, level, uuid, stage, subdir) -> str:
        parts = [level, uuid, stage] + ([subdir] if subdir else [])
        return "/".join(parts)

    def _execute_plan(self, p: dict, ctx: dict, ref_files: set, ref_dirs: set, missing: list) -> None:
        d: Document = p["doc"]
        dh = ctx["dh"]
        link = ctx["link"]
        project = link["project"] or {}
        sec_flag, sec_note = self.secrecy.judge(ctx["case"].get("MJ"), d.secrecy, p["pname"], d.title)
        base = {
            "batch_id": ctx["batch_id"], "dh": dh, "doc_id": p["doc_id"], "category": d.category,
            "title": d.title, "title_source": d.title_source, "period": ctx["period"],
            "project_uuid": project.get("uuid"), "project_level": project.get("level"),
            "match_status": link["status"], "rule_version": self.rule_tag,
            "secrecy": d.secrecy or ctx["case"].get("MJ") or "", "secret_flag": sec_flag,
            "authenticity": "", "confirmer": p.get("confirmer"), "reviewer": p.get("reviewer"),
            "processed_at": now(),
        }
        main_rel, method, why = self._choose_source(d)
        others = [r for r in (d.archive_rel, d.source_rel) if r and r != main_rel]
        declared = d.declared_archive or d.declared_source
        if main_rel is None and d.kind != "raw":
            missing.append(declared or d.title or "未命名")

        def row(role, src_rel, **kw):
            is_dir = bool(src_rel) and (Path(self._abs(dh, src_rel))).is_dir()
            r = {**base, "role": role, "src_rel": src_rel or (declared and f"[缺失]{declared}") or "",
                 "src_name": (src_rel or declared or "").rsplit("/", 1)[-1], "is_dir": int(is_dir),
                 "file_count": self._count_files(self._abs(dh, src_rel)) if is_dir else (1 if src_rel else 0),
                 "stage": None, "subdir": None, "file_type": None, "period_id": None, "version": None,
                 "target_rel": None, "target_name": None, "method": M_NONE, "status": ASSOCIATED,
                 "evidence": p.get("evidence") or p.get("reason") or "", "confidence": p.get("confidence"),
                 "note": ""}
            r.update(kw)
            if src_rel:
                (ref_dirs if is_dir else ref_files).add(src_rel)
            return r

        rows = []
        if p["action"] == RAW:
            st = RAW_KEPT
            note = p["reason"]
            if not main_rel:
                note += "；目录登记文件不在档内" if (d.extra.get("SFHJ") == "是") else "；未汇交电子文件"
                if d.extra.get("SFHJ") == "是":
                    missing.append(declared or d.title or "")
            rows.append(row("原始资料", main_rel, status=st, note=note, evidence="原始资料只登记"))
        elif p["action"] == ASSOCIATE:
            rows.append(row("主文件", main_rel, status=ASSOCIATED if main_rel else PENDING,
                            note=p["reason"] if main_rel else p["reason"] + "；文件缺失",
                            evidence=p["reason"]))
        else:
            # accept 或 review
            stage, subdir, ftype = p["stage"], p["subdir"], p["file_type"]
            plan_kw = {"stage": stage, "subdir": subdir, "file_type": ftype,
                       "period_id": p.get("period_id"), "version": p.get("version")}
            if p["action"] == REVIEW:
                r = row("主文件", main_rel, status=PENDING, note=f"进人工复核：{p['reason']}", **plan_kw)
                rows.append(r)
            elif main_rel is None:
                rows.append(row("主文件", None, status=PENDING, note="目录登记文件在档内不存在", **plan_kw))
            elif method == M_NONE:
                rows.append(row("主文件", main_rel, status=ASSOCIATED, note=why, **plan_kw))
            else:
                rows.append(self._land(p, ctx, main_rel, method, why, row, plan_kw))
        for o in others:
            rows.append(row("源文件" if o == d.source_rel else "存档文件", o, status=ASSOCIATED,
                            note="与落库主文件同名对应" if rows and rows[0]["status"] == LANDED else "同一文献的另一格式"))
        for res in d.resources:
            if res.get("exists"):
                rows.append(row("关联资源", res["rel"], status=ASSOCIATED, note=f"{res.get('name', '')}，随文献关联保存"))
        for r in rows:
            mid = self.db.insert("mapping", r)
            if r is rows[0] and p["action"] == REVIEW:
                self.db.insert("review", {
                    "dh": dh, "src_rel": p["key"], "mapping_id": mid, "kind": "classify",
                    "reason": p["reason"],
                    "suggestion": dumps({"stage": p["stage"], "subdir": p["subdir"], "file_type": p["file_type"],
                                         "period_id": p.get("period_id"), "version": p.get("version"),
                                         "confidence": p.get("confidence"), "evidence": p.get("evidence"),
                                         "rule_id": p.get("rule_id")}),
                    "candidates": dumps(p.get("candidates") or []), "status": "待复核", "created_at": now()})

    def _land(self, p, ctx, main_rel, method, why, row, plan_kw) -> dict:
        dh = ctx["dh"]
        project = ctx["link"]["project"] or {}
        level, uuid = project.get("level"), project.get("uuid")
        ext = "pdf" if method == M_DOC2PDF else ext_of(main_rel)
        name = build_name(p.get("period_id") or "", p["pname"], p["file_type"], p["version"], ext)
        stage, subdir = p["stage"], p["subdir"]
        if not (level and uuid):
            rel_dir = f"{dh}/{stage}" + (f"/{subdir}" if subdir else "")
            fails = []
            if self.rules.stage_def(stage):
                fails = [f for f in self.validator.check_one(f"/二级项目/{'0' * 32}/{stage}" + (f"/{subdir}" if subdir else ""),
                                                             name, stage, subdir, "二级项目")
                         if f.check != "目录路径正则"]
            note = "项目未关联，待关联后落库" + (f"；预校验：{fails[0].message}" if fails else "")
            return row("主文件", main_rel, status=PENDING, method=method, target_rel=f"待匹配/{rel_dir}/{name}",
                       target_name=name, note=note, **plan_kw)
        rel_dir = self._target_dir(level, uuid, stage, subdir)
        target_rel = f"{rel_dir}/{name}"
        fails = self.validator.check_one("/" + rel_dir, name, stage, subdir, level, abs_len=len(target_rel))
        if fails:
            msg = "；".join(f"{f.check}:{f.message}" for f in fails)
            self._issue(dh, "落库校验", f"{main_rel} 校验不通过：{msg}", "修正规则或人工处理")
            return row("主文件", main_rel, status=PENDING, method=method, target_rel=target_rel,
                       target_name=name, note=f"校验不通过，不输出：{msg}", **plan_kw)
        dst = self.full_root / target_rel
        self.guard.check(dst)
        owner = self.db.one("SELECT dh FROM mapping WHERE target_rel=? AND status=? AND dh!=?",
                            (target_rel, LANDED, dh))
        if owner or dst.exists():
            self._issue(dh, "跨档重名", f"{target_rel} 已被档 {owner['dh'] if owner else '其他'} 占用",
                        "人工确认版本或期间标识")
            return row("主文件", main_rel, status=PENDING, method=method, target_rel=target_rel,
                       target_name=name, note="落库位置已被占用，需人工确认版本", **plan_kw)
        src_abs = Path(self._abs(dh, main_rel))
        note = why
        if method == M_DOC2PDF:
            try:
                info = self.converter.to_pdf(src_abs, self.guard.check(dst))
                note = f"{why}；{info['engine']}转换，源页数{info['src_pages'] or '未知'}，PDF页数{info['pdf_pages']}"
                if info["src_pages"] and info["src_pages"] != info["pdf_pages"]:
                    self._issue(dh, "页数不一致", f"{main_rel} 源{info['src_pages']}页，PDF {info['pdf_pages']}页",
                                "人工核对")
            except ConvertError as e:
                self._issue(dh, "格式转换", f"{main_rel} 转换失败：{e}", "安装转换器或人工转换")
                return row("主文件", main_rel, status=PENDING, method=method, target_rel=target_rel,
                           target_name=name, note=f"转换失败：{e}", **plan_kw)
        else:
            self.guard.copy(src_abs, dst)
        return row("主文件", main_rel, status=LANDED, method=method, target_rel=target_rel,
                   target_name=name, note=note, **plan_kw)

    def _abs(self, dh, rel) -> str:
        cache = self.__dict__.setdefault("_root_cache", {})
        if dh not in cache:
            cache[dh] = self.db.scalar("SELECT root FROM archive WHERE dh=?", (dh,))
        return str(Path(cache[dh]) / rel) if rel else ""

    @staticmethod
    def _count_files(path: str) -> int:
        n = 0
        for _, _, files in os.walk(path):
            n += len(files)
        return n

    def _register_unreferenced(self, scan: ArchiveScan, ctx, ref_files: set, ref_dirs: set) -> None:
        """目录未引用的文件逐个登记；同一目录下超过阈值时按目录登记一行。"""
        dh = ctx["dh"]
        threshold = int(self.rules.params.get("dir_register_threshold", 50))
        ref_files_l = {r.lower() for r in ref_files}
        ref_dirs_l = tuple(r.lower().rstrip("/") + "/" for r in ref_dirs)
        project = ctx["link"]["project"] or {}
        base = {"batch_id": ctx["batch_id"], "dh": dh, "doc_id": None, "period": ctx["period"],
                "project_uuid": project.get("uuid"), "project_level": project.get("level"),
                "match_status": ctx["link"]["status"], "rule_version": self.rule_tag,
                "method": M_NONE, "processed_at": now(), "title": None, "title_source": "",
                "secrecy": ctx["case"].get("MJ") or ""}
        flag, _ = self.secrecy.judge(ctx["case"].get("MJ"), None, None, None)
        base["secret_flag"] = flag
        root = scan.root
        for d, dirs, files in os.walk(root):
            rel_d = os.path.relpath(d, root).replace("\\", "/")
            rel_d = "" if rel_d == "." else rel_d
            if rel_d and (rel_d.lower() + "/").startswith(ref_dirs_l):
                dirs[:] = []
                continue
            dirs[:] = [x for x in dirs if not ((f"{rel_d}/{x}" if rel_d else x).lower() + "/").startswith(ref_dirs_l)]
            un = [f for f in files if (f"{rel_d}/{f}" if rel_d else f).lower() not in ref_files_l]
            if not un:
                continue
            in_raw = rel_d.split("/")[0] == RAW_DIR
            status = RAW_KEPT if in_raw else ASSOCIATED
            note = "原始资料电子文件，原始区保留" if in_raw else "目录未登记的文件，按档关联保存"
            if len(un) > threshold:
                self.db.insert("mapping", {**base, "role": "未登记目录", "src_rel": rel_d + "/", "src_name": rel_d,
                                           "is_dir": 1, "file_count": len(un), "category": None,
                                           "status": status, "evidence": "目录未引用", "note": f"{note}，按目录登记"})
                continue
            for f in sorted(un):
                rel = f"{rel_d}/{f}" if rel_d else f
                self.db.insert("mapping", {**base, "role": "未登记文件", "src_rel": rel, "src_name": f,
                                           "is_dir": 0, "file_count": 1, "category": None, "status": status,
                                           "evidence": "目录未引用", "note": note})

    # ------------------------------------------------------------------ 批次
    def run_batch(self, dh_from: str | None = None, dh_to: str | None = None, force: bool = False,
                  name: str | None = None, progress: Callable[[str, int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None, dhs: list[str] | None = None) -> int:
        rows = self.db.query("SELECT dh, proc_status, proc_rule_version, root FROM archive WHERE root IS NOT NULL")

        def in_range(dh):
            k = dh_key(dh)
            def cmp(a, b):
                if a.isdigit() and b.isdigit():
                    return (int(a) > int(b)) - (int(a) < int(b))
                return (a > b) - (a < b)
            if dh_from and cmp(k, dh_key(dh_from)) < 0:
                return False
            if dh_to and cmp(k, dh_key(dh_to)) > 0:
                return False
            return True

        todo = [r for r in rows if (dhs is None or r["dh"] in dhs) and in_range(r["dh"])]
        todo.sort(key=lambda r: (not dh_key(r["dh"]).isdigit(), int(dh_key(r["dh"])) if dh_key(r["dh"]).isdigit() else 0, r["dh"]))
        bid = self.db.insert("batch", {"name": name or f"批次{now()}", "dh_from": dh_from, "dh_to": dh_to,
                                       "program_version": __version__, "rule_version": self.rule_tag,
                                       "status": "运行中", "total": len(todo), "done": 0, "failed": 0,
                                       "started_at": now(), "finished_at": None, "operator": self.operator})
        done = failed = 0
        for i, r in enumerate(todo):
            if should_stop and should_stop():
                self.db.execute("UPDATE batch SET status='已中止', finished_at=? WHERE id=?", (now(), bid))
                return bid
            if not force and r["proc_status"] in ("已完成", "待复核", "待项目关联", "暂不支持") \
                    and r["proc_rule_version"] == self.rule_tag:
                done += 1
                continue
            if progress:
                progress(r["dh"], i, len(todo))
            try:
                self.process_archive(r["dh"], bid)
                done += 1
            except Exception as e:
                failed += 1
                self.db.execute("UPDATE archive SET proc_status='失败', proc_message=? WHERE dh=?",
                                (str(e)[:500], r["dh"]))
                self.db.log("治理", f"{r['dh']} 失败：{e}\n{traceback.format_exc()}", dh=r["dh"], level="错误",
                            batch_id=bid)
            self.db.execute("UPDATE batch SET done=?, failed=? WHERE id=?", (done, failed, bid))
        self.db.execute("UPDATE batch SET status=?, finished_at=? WHERE id=?",
                        ("已完成" if not failed else "部分失败", now(), bid))
        return bid

    # ------------------------------------------------------------------ 人工复核
    def confirm_review(self, review_id: int, result: dict, basis: str, confirmer: str,
                       reviewer: str | None = None, rerun: bool = True) -> dict | None:
        rv = self.db.one("SELECT * FROM review WHERE id=?", (review_id,))
        if not rv:
            raise KeyError(review_id)
        if rv["kind"] == "project":
            uuid = result["uuid"]
            self.db.execute("DELETE FROM archive_project WHERE dh=?", (rv["dh"],))
            self.db.upsert("archive_project", {"dh": rv["dh"], "project_uuid": uuid, "match_status": CONFIRMED,
                                               "basis": basis, "confirmer": confirmer, "confirmed_at": now(),
                                               "manual": 1})
        else:
            self.db.upsert("decision", {"dh": rv["dh"], "src_rel": rv["src_rel"], "kind": rv["kind"],
                                        "result": dumps(result), "basis": basis, "confirmer": confirmer,
                                        "reviewer": reviewer, "status": "已确认", "decided_at": now()})
        self.db.execute("UPDATE review SET status='已确认' WHERE id=?", (review_id,))
        self.db.log("复核", f"复核确认 {rv['src_rel'] or '项目关联'}：{dumps(result)}，依据 {basis}",
                    dh=rv["dh"], operator=confirmer)
        if rerun:
            return self.process_archive(rv["dh"])
        return None

    def submit_review(self, review_id: int, note: str, operator: str) -> None:
        self.db.execute("UPDATE review SET status='已提交' WHERE id=?", (review_id,))
        self.db.log("复核", f"提交技术负责人复核：{note}", operator=operator)

    def link_project_manually(self, dh: str, uuid: str, basis: str, confirmer: str) -> dict:
        self.db.execute("DELETE FROM archive_project WHERE dh=?", (dh,))
        self.db.upsert("archive_project", {"dh": dh, "project_uuid": uuid, "match_status": CONFIRMED,
                                           "basis": basis, "confirmer": confirmer, "confirmed_at": now(),
                                           "manual": 1})
        return self.process_archive(dh)

    def add_issue(self, dh: str, kind: str, desc: str, owner: str) -> None:
        self.db.insert("issue", {"dh": dh, "kind": kind, "description": desc, "suggestion": "",
                                 "owner": owner, "status": "未关闭", "created_at": now(), "closed_at": None,
                                 "auto": 0})
