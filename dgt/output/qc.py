"""质量检查：交接清单对账、目录路径、文件名、受控词表、版本连续性、唯一性、映射完整性、PDF可读、涉密分套。"""
from __future__ import annotations

from collections import defaultdict

from ..core.db import loads
from ..process.convert import pdf_pages

CHECKS = ["交接清单对账", "目录路径正则", "文件名正则", "受控词表", "版本连续性", "唯一性",
          "映射表完整性", "PDF可读与页数", "涉密分套"]


def run_qc(engine, dhs: list[str] | None = None, check_pdf: bool = True) -> tuple[list[dict], list[dict]]:
    db = engine.db
    cond, params = "", ()
    if dhs:
        cond = f" AND dh IN ({','.join('?' * len(dhs))})"
        params = tuple(dhs)
    total = defaultdict(int)
    failures: list[dict] = []

    def fail(check, dh, target, msg):
        failures.append({"check": check, "dh": dh, "target": target, "message": msg})

    for a in db.query(f"SELECT * FROM archive WHERE 1=1{cond}", params):
        total["交接清单对账"] += 1
        if a["recon_status"] != "一致":
            fail("交接清单对账", a["dh"], a["dh"], f"{a['recon_status']}：{'；'.join(loads(a['recon_detail'], []))}")

    landed = db.query(f"SELECT * FROM mapping WHERE status='已落库'{cond}", params)
    group_items = []
    for m in landed:
        parts = m["target_rel"].split("/")
        rel_dir = "/" + "/".join(parts[:-1])
        for c in ("目录路径正则", "文件名正则", "受控词表"):
            total[c] += 1
        fs = engine.validator.check_one(rel_dir, m["target_name"], m["stage"], m["subdir"], m["project_level"],
                                        abs_len=len(m["target_rel"]))
        for f in fs:
            key = f.check if f.check in CHECKS else "文件名正则"
            fail(key, m["dh"], f.target, f.message)
        group_items.append({"project_uuid": m["project_uuid"], "rel_dir": rel_dir, "name": m["target_name"]})
        target = engine.full_root / m["target_rel"]
        if not target.exists():
            fail("映射表完整性", m["dh"], m["target_rel"], "映射表登记已落库但成果区无此文件")
        if check_pdf and m["target_name"].lower().endswith(".pdf"):
            total["PDF可读与页数"] += 1
            if target.exists():
                n = pdf_pages(target)
                if not n:
                    fail("PDF可读与页数", m["dh"], m["target_rel"], "PDF 无法打开或页数为 0")
    total["版本连续性"] = total["唯一性"] = len(group_items)
    for f in engine.validator.check_group(group_items):
        fail(f.check, None, f.target, f.message)

    # 映射完整性：每份文献都有映射记录，必填字段齐全
    docs = db.query(f"SELECT id, dh FROM document WHERE 1=1{cond}", params)
    mapped = {r["doc_id"] for r in db.query(f"SELECT DISTINCT doc_id FROM mapping WHERE doc_id IS NOT NULL{cond}", params)}
    total["映射表完整性"] += len(docs)
    for d in docs:
        if d["id"] not in mapped:
            fail("映射表完整性", d["dh"], f"文献{d['id']}", "文献没有映射记录")
    allm = db.query(f"SELECT id, dh, src_rel, status, rule_version, secret_flag FROM mapping WHERE 1=1{cond}", params)
    total["涉密分套"] += len(allm)
    for m in allm:
        if not m["src_rel"] or not m["status"] or not m["rule_version"]:
            fail("映射表完整性", m["dh"], m["src_rel"] or f"记录{m['id']}", "必填字段缺失")
        if m["secret_flag"] not in ("涉密", "非涉密"):
            fail("涉密分套", m["dh"], m["src_rel"], "涉密标记缺失")
    # 档级涉密的档不能有非涉密记录
    for a in db.query(f"SELECT dh FROM archive WHERE 1=1{cond}", params):
        case = db.one("SELECT fields FROM case_info WHERE dh=?", (a["dh"],))
        mj = loads(case["fields"], {}).get("MJ") if case else None
        if engine.secrecy.level_is_secret(mj):
            n = db.scalar("SELECT COUNT(*) FROM mapping WHERE dh=? AND secret_flag='非涉密'", (a["dh"],), 0)
            if n:
                fail("涉密分套", a["dh"], a["dh"], f"档级密级 {mj}，但有 {n} 条记录标为非涉密")

    summary = []
    counted = defaultdict(int)
    for f in failures:
        counted[f["check"]] += 1
    for c in CHECKS:
        summary.append({"check": c, "total": total.get(c, 0), "failed": counted.get(c, 0)})
    return summary, failures
