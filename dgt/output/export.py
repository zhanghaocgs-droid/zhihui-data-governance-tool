"""成果输出：从全补成果派生涉密、非涉密两套，每套带结构化目录 Excel 与映射关系表。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..core.db import loads
from .excel import write_catalog, write_inventory, write_mapping, write_qc
from .qc import run_qc

SETS = ("全补", "涉密", "非涉密")


class ExportAborted(RuntimeError):
    pass


def _rows(engine, dhs):
    cond, params = "", ()
    if dhs:
        cond = f" WHERE dh IN ({','.join('?' * len(dhs))})"
        params = tuple(dhs)
    return [dict(r) for r in engine.db.query(f"SELECT * FROM mapping{cond} ORDER BY dh, id", params)]


def _archive_secret(engine, dh) -> bool:
    case = engine.db.one("SELECT fields FROM case_info WHERE dh=?", (dh,))
    mj = loads(case["fields"], {}).get("MJ") if case else None
    return bool(engine.secrecy.level_is_secret(mj))


def catalog_records(engine, rows: list[dict]) -> list[dict]:
    db = engine.db
    cases, docs, projects, archives = {}, {}, {}, {}
    out = []
    for m in rows:
        if m["status"] != "已落库":
            continue
        dh = m["dh"]
        if dh not in cases:
            c = db.one("SELECT fields FROM case_info WHERE dh=?", (dh,))
            cases[dh] = loads(c["fields"], {}) if c else {}
            archives[dh] = {"dh": dh}
        if m["doc_id"] and m["doc_id"] not in docs:
            d = db.one("SELECT * FROM document WHERE id=?", (m["doc_id"],))
            docs[m["doc_id"]] = dict(d) if d else {}
        uuid = m["project_uuid"]
        if uuid and uuid not in projects:
            p = db.one("SELECT * FROM project WHERE uuid=?", (uuid,))
            projects[uuid] = dict(p) if p else {}
        out.append({"archive": archives[dh], "map": m, "doc": docs.get(m["doc_id"], {}),
                    "case": cases[dh], "project": projects.get(uuid, {})})
    return out


def select_set(engine, rows: list[dict], which: str) -> list[dict]:
    if which == "全补":
        return rows
    if which == "涉密":
        return [r for r in rows if r["secret_flag"] == "涉密"]
    return [r for r in rows if r["secret_flag"] == "非涉密" and not _archive_secret(engine, r["dh"])]


def second_check(engine, rows: list[dict]) -> list[str]:
    """非涉密套二次校验：重新按档级、文件级、目录级口径判定，任何一条涉密即拒绝输出。"""
    problems = []
    for r in rows:
        case = engine.db.one("SELECT fields FROM case_info WHERE dh=?", (r["dh"],))
        mj = loads(case["fields"], {}).get("MJ") if case else None
        doc_mj = None
        if r["doc_id"]:
            doc_mj = engine.db.scalar("SELECT secrecy FROM document WHERE id=?", (r["doc_id"],))
        flag, why = engine.secrecy.judge(mj, doc_mj, r["target_name"], r["title"])
        if flag != "非涉密":
            problems.append(f"{r['dh']} {r['src_rel']}：{why}")
    return problems


def export_sets(engine, dhs: list[str] | None = None, sets=SETS, out_dir: Path | None = None) -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_dir or engine.out_root / "导出" / stamp
    engine.guard.check(out_dir)
    rows = _rows(engine, dhs)
    result = {"dir": str(out_dir), "sets": {}}
    for which in sets:
        sel = select_set(engine, rows, which)
        if which == "非涉密":
            problems = second_check(engine, sel)
            if problems:
                engine.db.log("成果输出", f"非涉密套二次校验发现 {len(problems)} 条涉密，已中止该套输出", level="错误")
                result["sets"][which] = {"aborted": True, "problems": problems[:20]}
                continue
        base = out_dir / which
        n_files = 0
        for r in sel:
            if r["status"] == "已落库" and r["target_rel"]:
                engine.guard.link_or_copy(engine.full_root / r["target_rel"], base / "落库成果" / r["target_rel"])
                n_files += 1
        write_mapping(sel, engine.guard.check(base / f"映射关系表_{which}.xlsx"), engine.rule_tag)
        write_catalog(catalog_records(engine, sel), engine.rules.excel_header,
                      engine.guard.check(base / f"结构化目录_{which}.xlsx"))
        result["sets"][which] = {"records": len(sel), "landed": n_files,
                                 "archives": len({r["dh"] for r in sel}),
                                 "projects": len({r["project_uuid"] for r in sel if r["project_uuid"]})}
    # 质检结果与盘点台账
    summary, failures = run_qc(engine, dhs)
    write_qc(summary, failures, engine.guard.check(out_dir / "质检结果.xlsx"),
             {"规则版本": engine.rule_tag, "输出时间": stamp})
    cond, params = "", ()
    if dhs:
        cond = f" WHERE dh IN ({','.join('?' * len(dhs))})"
        params = tuple(dhs)
    write_inventory(engine.db.query(f"SELECT * FROM archive{cond} ORDER BY dh", params),
                    engine.guard.check(out_dir / "盘点台账.xlsx"))
    result["qc"] = summary
    engine.db.log("成果输出", f"输出三套成果到 {out_dir}：" +
                  "，".join(f"{k} {v.get('records', '中止')}" for k, v in result["sets"].items()))
    return result
