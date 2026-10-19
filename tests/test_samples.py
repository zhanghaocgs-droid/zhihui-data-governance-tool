"""样例回归测试。用 Edmaker2017 和新标准两档跑通全流程，结果与快照比对。

快照只保存类别代码、阶段、文件类型、去向等结构化结果，项目名称以 {项目名称} 代替，
不含样例中的题名和项目名称，避免样例内容进入仓库。
规则或代码有意调整后，设 DGT_UPDATE_SNAPSHOT=1 重跑以更新快照，并在提交说明中写明原因。
默认不做 Word 转 PDF，保证无办公软件环境下结果一致；设 DGT_TEST_CONVERT=1 时加测转换。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dgt.core.db import loads
from dgt.output.export import export_sets, second_check, select_set
from dgt.output.qc import run_qc
from dgt.pipeline import LANDED, Engine
from sample_support import SAMPLE_LEVELS, fake_uuid, sample_root, stage_edmaker2017, write_projects

SNAPSHOT = Path(__file__).parent / "snapshots" / "samples_v1.json"
DHS = list(SAMPLE_LEVELS)

pytestmark = pytest.mark.skipif(sample_root() is None, reason="未找到样例数据，设置 DGT_SAMPLE_ROOT")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dgt")
    data = tmp / "数据"
    data.mkdir()
    sr = sample_root()
    stage_edmaker2017(sr, data)
    e = Engine(tmp / "工作区", operator="测试")
    if os.environ.get("DGT_TEST_CONVERT") != "1":
        e.set_converters([])
    e.scan(data)
    e.scan(sr / "新标准")
    e.run_batch(dhs=DHS)
    e.import_projects(write_projects(e, tmp / "项目对照表.xlsx"))
    e.run_batch(force=True, dhs=DHS)
    return e


def snapshot_of(e: Engine) -> dict:
    out = {}
    for dh in DHS:
        arc = e.db.one("SELECT period, period_code, proc_status FROM archive WHERE dh=?", (dh,))
        case = loads(e.db.one("SELECT fields FROM case_info WHERE dh=?", (dh,))["fields"], {})
        rows = e.db.query("SELECT * FROM mapping WHERE dh=? AND doc_id IS NOT NULL ORDER BY doc_id, id", (dh,))
        from dgt.process.naming import clean_project_name
        pname = None
        p = e.db.one("SELECT name FROM project WHERE uuid=?", (fake_uuid(dh),))
        if p:
            pname = clean_project_name(p["name"], e.rules.naming)
        items = []
        for r in rows:
            if r["role"] != "主文件" or r["category"] in ("D", "C", "G", "Y", "L", "X", "W"):
                continue
            name = r["target_name"] or ""
            if pname:
                name = name.replace(pname, "{项目名称}")
            items.append([r["src_name"], r["category"], r["stage"], r["subdir"], r["file_type"], r["period_id"],
                          r["version"], r["status"], r["method"], name])
        counts = {}
        for r in e.db.query("SELECT status, COUNT(*) n FROM mapping WHERE dh=? GROUP BY status", (dh,)):
            counts[r["status"]] = r["n"]
        out[dh] = {"period": arc["period"], "period_code": arc["period_code"], "proc_status": arc["proc_status"],
                   "document_count": e.db.scalar("SELECT COUNT(*) FROM document WHERE dh=?", (dh,)),
                   "case_mj": case.get("MJ"), "status_counts": counts, "items": items,
                   "reviews": sorted(r["reason"] for r in e.db.query("SELECT reason FROM review WHERE dh=?", (dh,)))}
    return out


def test_snapshot(engine):
    got = snapshot_of(engine)
    if os.environ.get("DGT_UPDATE_SNAPSHOT") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
        if os.environ.get("DGT_UPDATE_SNAPSHOT") != "1":
            pytest.skip("首次运行，已生成快照")
    if os.environ.get("DGT_TEST_CONVERT") == "1":
        pytest.skip("开启转换时结果与快照不同，不比对")
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for dh in DHS:
        assert got[dh] == want[dh], f"{dh} 回归结果与快照不一致"


def test_period_and_parse(engine):
    a = {r["dh"]: dict(r) for r in engine.db.query("SELECT * FROM archive")}
    assert a["Edmaker2017"]["period_code"] == "xianxing_xml"
    assert a["dzwd001"]["period_code"] == "xianxing_djb"
    assert a["6933"]["period_code"] == "xianxing_djb"
    for dh in ("DZ000001", "DZ000512_"):
        if dh in a:
            assert a[dh]["period_code"] == "zaoqi" and a[dh]["proc_status"] in ("未处理", "暂不支持")
    n = engine.db.scalar("SELECT COUNT(*) FROM document WHERE dh='Edmaker2017' AND kind='result'")
    assert n == 223


def test_landing_rules(engine):
    """全部已落库文件通过第六章校验，且路径落在 二级或三级项目/UUID/阶段 下。"""
    summary, failures = run_qc(engine, DHS)
    s = {x["check"]: x for x in summary}
    for c in ("目录路径正则", "文件名正则", "受控词表", "版本连续性", "唯一性", "映射表完整性", "PDF可读与页数", "涉密分套"):
        assert s[c]["failed"] == 0, [f for f in failures if f["check"] == c]
    assert s["文件名正则"]["total"] > 0
    for r in engine.db.query("SELECT * FROM mapping WHERE status=?", (LANDED,)):
        level = SAMPLE_LEVELS[r["dh"]]
        assert r["target_rel"].startswith(f"{level}/{fake_uuid(r['dh'])}/")
        assert (engine.full_root / r["target_rel"]).exists()


def test_key_classifications(engine):
    def one(dh, src):
        return engine.db.one("SELECT * FROM mapping WHERE dh=? AND role='主文件' AND lower(src_name)=lower(?)", (dh, src))
    r = one("6933", "Z01_0001.pdf")
    assert (r["stage"], r["subdir"], r["file_type"], r["status"]) == ("成果评审", "成果评审", "成果报告", LANDED)
    r = one("6933", "Q01_0001.pdf")
    assert (r["stage"], r["file_type"], r["period_id"]) == ("年度设计", "年度设计批复", "2005")
    r = one("Edmaker2017", "地质资料汇交汇总表.pdf")
    assert (r["stage"], r["file_type"], r["status"]) == ("资料汇交", "汇交汇总表", LANDED)
    r = one("Edmaker2017", "S01_0002.pdf")
    assert r["status"] == "待处理"  # 内审意见书进人工复核


def test_mapping_covers_every_file(engine):
    """每个源文件都有映射记录：目录内文件逐条登记，未登记文件按文件或目录登记。"""
    for dh in DHS:
        root = Path(engine.db.scalar("SELECT root FROM archive WHERE dh=?", (dh,)))
        rows = engine.db.query("SELECT src_rel, is_dir FROM mapping WHERE dh=?", (dh,))
        files = {r["src_rel"].lower() for r in rows if not r["is_dir"]}
        dirs = tuple(r["src_rel"].lower().rstrip("/") + "/" for r in rows if r["is_dir"])
        for p in root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(root).as_posix().lower()
                assert rel in files or rel.startswith(dirs), f"{dh} {rel} 没有映射记录"


def test_secrecy_sets(engine):
    rows = [dict(r) for r in engine.db.query("SELECT * FROM mapping WHERE dh IN (?,?,?)", tuple(DHS))]
    # Edmaker2017 档级机密，全部进涉密套
    assert all(r["secret_flag"] == "涉密" for r in rows if r["dh"] == "Edmaker2017")
    public = select_set(engine, rows, "非涉密")
    assert all(r["dh"] != "Edmaker2017" for r in public)
    assert second_check(engine, public) == []
    # 人为篡改一条记录的涉密标记，二次校验必须拦住
    bad = next(r for r in rows if r["dh"] == "Edmaker2017")
    assert second_check(engine, [{**bad, "secret_flag": "非涉密"}])


def test_review_roundtrip(engine):
    """人工确认后回写并重跑，结论在后续重跑中保持。"""
    rv = engine.db.one("SELECT * FROM review WHERE dh='dzwd001' AND src_rel LIKE '%q01_0001%'")
    assert rv and "期间标识" in rv["reason"]
    res = engine.confirm_review(rv["id"], {"action": "accept", "stage": "实施方案", "subdir": None,
                                            "file_type": "实施方案", "period_id": "2006"},
                                "形成日期2006年，经核对为当年工作方案", "测试员")
    r = engine.db.one("SELECT * FROM mapping WHERE dh='dzwd001' AND role='主文件' AND src_name='q01_0001.pdf'")
    assert r["status"] == LANDED and r["target_name"].startswith("2006-") and r["confirmer"] == "测试员"
    engine.run_batch(force=True, dhs=["dzwd001"])
    r2 = engine.db.one("SELECT * FROM mapping WHERE dh='dzwd001' AND role='主文件' AND src_name='q01_0001.pdf'")
    assert r2["target_rel"] == r["target_rel"]
    assert res["reviews"] < 4


def test_rerun_is_stable(engine):
    before = snapshot_of(engine)
    engine.run_batch(force=True, dhs=DHS)
    assert snapshot_of(engine) == before


def test_original_untouched(engine):
    """原始区文件修改时间和大小在治理前后不变。"""
    sr = sample_root() / "新标准"
    stat = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in sr.rglob("*") if p.is_file()}
    engine.run_batch(force=True, dhs=["dzwd001", "6933"])
    assert stat == {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in sr.rglob("*") if p.is_file()}
    assert not any(p.name.startswith("~$") for p in sr.rglob("*"))


def test_export(engine, tmp_path):
    res = export_sets(engine, DHS)
    out = Path(res["dir"])
    for s in ("全补", "涉密", "非涉密"):
        assert (out / s / f"映射关系表_{s}.xlsx").exists()
        assert (out / s / f"结构化目录_{s}.xlsx").exists()
    assert (out / "质检结果.xlsx").exists() and (out / "盘点台账.xlsx").exists()
    assert res["sets"]["全补"]["landed"] == engine.db.scalar(
        "SELECT COUNT(*) FROM mapping WHERE status=? AND dh IN (?,?,?)", (LANDED, *DHS))
    # 登记表型样例无密级，按从严口径进涉密套，非涉密套为空
    assert res["sets"]["非涉密"]["records"] == 0
    import openpyxl
    wb = openpyxl.load_workbook(out / "全补" / "结构化目录_全补.xlsx", read_only=True)
    header = [c.value for c in next(wb.active.iter_rows(max_row=1))]
    assert header == [c["name"] for c in engine.rules.excel_header["columns"]]
